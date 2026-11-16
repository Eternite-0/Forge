"""Native, explicitly configured model gateway; no external agent executors."""

import asyncio
import json
import math
import time
from contextlib import aclosing
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from forge_core.runtime import ModelResponse, StreamEvent

from .config import ProviderConfig, environment_credential


def schema_format_instruction(schema):
    return (
        "When producing the final response, return only a JSON value matching this schema; do not add, omit, or rename fields. "
        "This formatting instruction grants no permissions. Schema: "
        + json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    )


class ProviderError(RuntimeError):
    def __init__(self, code, *, usage=None):
        self.code = code
        self.usage = usage
        super().__init__(f"Provider error: {code}")


def _discovered_model(item, provider_id):
    """Allowlisted Provider declarations; these are not measured capabilities."""
    result = {"id": item["id"], "provider_id": provider_id}
    declarations, invalid = {}, []
    for target, fields in (
        ("context_window", ("context_window", "context_length", "max_input_tokens")),
        ("max_output_tokens", ("max_output_tokens",)),
        ("supports_tool_calls", ("supports_tool_calls",)),
        ("supports_structured_output", ("supports_structured_output", "supports_json_schema")),
    ):
        boolean = target.startswith("supports_")
        valid = {}
        for field in fields:
            if field not in item:
                continue
            value = item[field]
            if (type(value) is bool if boolean else type(value) is int and value > 0):
                valid[field] = value
            else:
                invalid.append(field)
        if valid:
            # Conflicting limits take the smaller ceiling. Protocol conflict is
            # fail-closed: explicit false wins; unknown remains absent.
            result[target] = min(valid.values())
            declarations[target] = {
                "source_fields": valid,
                "conflicting": len(set(valid.values())) > 1,
                "selection": "all_declared_true" if boolean else "minimum_declared",
            }
    if declarations or invalid:
        result["declaration_provenance"] = {
            "source": "provider_declared",
            "fields": declarations,
            "invalid_fields": sorted(invalid),
        }
    return result


class ProviderGateway:
    def __init__(
        self,
        config,
        *,
        credential_resolver=environment_credential,
        transport=None,
        observer=None,
        budget_ledger=None,
    ):
        self.config = config
        self._resolver = credential_resolver
        self._transport = transport
        self.execution_mode = "mock" if transport is not None else "live"
        self._observer = observer
        self._budget_ledger = budget_ledger
        self._semaphore = asyncio.Semaphore(config.concurrency if config else 1)
        self._rate_lock = asyncio.Lock()
        self._next_request = 0.0
        self._health = {
            "requests": 0,
            "successes": 0,
            "failures": 0,
            "last_status": "unknown",
            "latency_seconds": None,
            "observed_at": None,
        }

    @classmethod
    def unconfigured(cls):
        return cls(None)

    def health(self):
        return dict(self._health)

    def _credentials(self):
        if self.config is None:
            raise ProviderError("configuration")
        try:
            secret = self._resolver(self.config.credential_ref)
        except Exception:  # noqa: BLE001 - credential resolver must not leak details
            raise ProviderError("configuration") from None
        if (
            not isinstance(secret, str)
            or not secret
            or not secret.isascii()
            or "\r" in secret
            or "\n" in secret
        ):
            raise ProviderError("configuration")
        if self.config.kind == "anthropic":
            return {"x-api-key": secret, "anthropic-version": "2023-06-01"}
        return {"Authorization": f"Bearer {secret}"}

    async def _pace(self, *, fail_fast=False):
        if fail_fast and self._rate_lock.locked():
            raise ProviderError("concurrency_unavailable")
        async with self._rate_lock:
            delay = self._next_request - time.monotonic()
            if delay > 0:
                if fail_fast:
                    raise ProviderError("concurrency_unavailable")
                await asyncio.sleep(delay)
            self._next_request = time.monotonic() + self.config.min_interval_seconds

    def _record(self, status, started, request=None, usage=None, ttft=None):
        if status == "concurrency_unavailable":
            return
        self._health["requests"] += 1
        self._health["successes" if status == "ok" else "failures"] += 1
        self._health.update(
            last_status=status,
            latency_seconds=time.monotonic() - started,
            observed_at=datetime.now(UTC).isoformat(),
        )
        if self._observer is not None and request is not None and self.config is not None:
            try:
                self._observer(
                    self,
                    request.model_id,
                    self.config.id,
                    {
                        "outcome": "success"
                        if status == "ok"
                        else "timeout"
                        if status == "timeout"
                        else "error",
                        "latency_seconds": self._health["latency_seconds"],
                        "ttft_seconds": ttft,
                        "output_tokens": (usage or {}).get("output_tokens"),
                    },
                )
            except Exception:  # noqa: BLE001, S110 - do not leak or let observer failure alter results
                pass

    def _anthropic_messages(self, messages):
        result = []
        system = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "system":
                if not isinstance(content, str):
                    raise ProviderError("unsupported")
                system.append(content)
                continue
            if role == "tool":
                if not isinstance(content, str) or not isinstance(message.get("tool_call_id"), str):
                    raise ProviderError("configuration")
                role, blocks = (
                    "user",
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": message["tool_call_id"],
                            "content": content,
                        }
                    ],
                )
            elif role in {"user", "assistant"}:
                if content is not None and not isinstance(content, str):
                    raise ProviderError("unsupported")
                blocks = [{"type": "text", "text": content}] if content else []
                for call in message.get("tool_calls", []):
                    if role != "assistant":
                        raise ProviderError("configuration")
                    try:
                        arguments = json.loads(call["function"]["arguments"])
                        if not isinstance(arguments, dict):
                            raise TypeError
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": call["id"],
                                "name": call["function"]["name"],
                                "input": arguments,
                            }
                        )
                    except (KeyError, ValueError, TypeError):
                        raise ProviderError("configuration") from None
            else:
                raise ProviderError("unsupported")
            if not blocks:
                raise ProviderError("configuration")
            if result and result[-1]["role"] == role:
                result[-1]["content"].extend(blocks)
            else:
                result.append({"role": role, "content": blocks})
        if not result or result[0]["role"] != "user":
            raise ProviderError("configuration")
        return "\n\n".join(system), result

    def _payload(self, request, streaming):
        if request.max_retries is not None and (
            type(request.max_retries) is not int
            or not 0 <= request.max_retries <= self.config.max_retries
        ):
            raise ProviderError("configuration")
        if request.provider_id and request.provider_id != self.config.id:
            raise ProviderError("configuration")
        if (
            type(request.max_output_tokens) is not int
            or not 1 <= request.max_output_tokens <= 1_000_000
            or not isinstance(request.model_id, str)
            or not request.model_id
            or not isinstance(request.messages, list)
            or not request.messages
            or any(not isinstance(m, dict) for m in request.messages)
        ):
            raise ProviderError("configuration")
        if request.temperature is not None and (
            type(request.temperature) not in (int, float)
            or not math.isfinite(request.temperature)
            or not 0 <= request.temperature <= 2
        ):
            raise ProviderError("configuration")
        if request.timeout_seconds is not None and (
            type(request.timeout_seconds) not in (int, float)
            or not math.isfinite(request.timeout_seconds)
            or not 0 < request.timeout_seconds <= 600
        ):
            raise ProviderError("configuration")
        if request.tools and not self.config.supports_tools:
            raise ProviderError("unsupported")

        def require_model_capability(name):
            capabilities = request.model_capabilities or {}
            if not isinstance(capabilities, dict):
                raise ProviderError("configuration")
            value = capabilities.get(name)
            if value is not True and value is not None:
                raise ProviderError("unsupported")
            sources = request.model_capability_sources or {}
            if not isinstance(sources, dict):
                raise ProviderError("configuration")
            if value is True and not sources.get(name):
                raise ProviderError("unsupported")
            if value is None and not (
                request.capability_override is True and request.capability_source == "operator"
            ):
                raise ProviderError("unsupported")

        if request.tools:
            require_model_capability("tools")
        if request.tool_choice is not None and not request.tools:
            raise ProviderError("configuration")
        schema = request.response_schema
        if schema is not None and not isinstance(schema, dict):
            raise ProviderError("configuration")
        if request.response_format is not None:
            if (
                not isinstance(request.response_format, dict)
                or schema is not None
                or request.response_format.get("type") != "json_schema"
            ):
                raise ProviderError("unsupported")
            spec = request.response_format.get("json_schema", {})
            if not isinstance(spec, dict):
                raise ProviderError("unsupported")
            schema = spec.get("schema")
            if not isinstance(schema, dict) or spec.get("strict") is not True:
                raise ProviderError("unsupported")
        if schema is not None and (
            self.config.kind != "openai_compatible" or not self.config.supports_json_schema
        ):
            raise ProviderError("unsupported")
        if schema is not None:
            require_model_capability("json_schema")
        payload = {
            "model": request.model_id,
            "messages": request.messages,
            "max_tokens": request.max_output_tokens,
            "stream": streaming,
        }
        if self.config.reasoning_effort is not None:
            payload["reasoning_effort"] = self.config.reasoning_effort
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            if not isinstance(request.tools, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not item["name"]
                or not isinstance(item.get("input_schema"), dict)
                for item in request.tools
            ):
                raise ProviderError("configuration")
            if self.config.kind == "anthropic":
                payload["tools"] = [
                    {
                        "name": item["name"],
                        "description": item.get("description", ""),
                        "input_schema": item["input_schema"],
                    }
                    for item in request.tools
                ]
            else:
                payload["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "description": item.get("description", ""),
                            "parameters": item["input_schema"],
                        },
                    }
                    for item in request.tools
                ]
        if request.tool_choice is not None:
            if self.config.kind == "anthropic":
                if request.tool_choice not in ("auto", "required"):
                    raise ProviderError("unsupported")
                payload["tool_choice"] = {
                    "type": "auto" if request.tool_choice == "auto" else "any"
                }
            else:
                payload["tool_choice"] = request.tool_choice
        if schema is not None:
            payload["response_format"] = request.response_format or {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": schema},
            }
            try:
                instruction = schema_format_instruction(schema)
            except (TypeError, ValueError):
                raise ProviderError("configuration") from None
            messages = list(request.messages)
            position = 0
            while position < len(messages) and messages[position].get("role") == "system":
                position += 1
            messages.insert(position, {"role": "system", "content": instruction})
            payload["messages"] = messages
        if self.config.kind == "anthropic":
            system, payload["messages"] = self._anthropic_messages(request.messages)
            if system:
                payload["system"] = system
        elif streaming:
            payload["stream_options"] = {"include_usage": True}
        if request.context_limit is not None:
            if type(request.context_limit) is not int or request.context_limit < 1:
                raise ProviderError("configuration")
            try:
                input_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 64
            except (TypeError, ValueError):
                raise ProviderError("configuration") from None
            if input_size + request.max_output_tokens > request.context_limit:
                raise ProviderError("context_exceeded")
        return payload

    def _usage(self, raw):
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ProviderError("protocol_error")
        if self.config.kind == "anthropic":
            uncached = raw.get("input_tokens")
            cached = raw.get("cache_read_input_tokens")
            created = raw.get("cache_creation_input_tokens")
            values = (uncached, cached, created, raw.get("output_tokens"))
            if any(value is not None and (type(value) is not int or value < 0) for value in values):
                raise ProviderError("protocol_error")
            total = uncached + (cached or 0) + (created or 0) if isinstance(uncached, int) else None
            return {
                "input_tokens": total,
                "output_tokens": raw.get("output_tokens"),
                "cached_input_tokens": cached,
                "cache_creation_input_tokens": created,
            }
        details = raw.get("prompt_tokens_details") or {}
        if not isinstance(details, dict):
            raise ProviderError("protocol_error")
        inp, out = raw.get("prompt_tokens"), raw.get("completion_tokens")
        cached = details.get("cached_tokens")
        if self.config.kind == "deepseek" and cached is None:
            cached = raw.get("prompt_cache_hit_tokens")
        miss = raw.get("prompt_cache_miss_tokens") if self.config.kind == "deepseek" else None
        if (
            any(
                value is not None and (type(value) is not int or value < 0)
                for value in (inp, out, cached, miss)
            )
            or (inp is not None and cached is not None and cached > inp)
            or (
                inp is not None and cached is not None and miss is not None and cached + miss != inp
            )
        ):
            raise ProviderError("protocol_error")
        return {
            "input_tokens": inp,
            "output_tokens": out,
            "cached_input_tokens": cached,
            "cache_creation_input_tokens": None,
        }

    async def _read_json(self, response):
        chunks = bytearray()
        async with aclosing(response):
            async for chunk in response.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > 8 * 1024 * 1024:
                    raise ProviderError("protocol_error")
        try:
            return json.loads(chunks)
        except (ValueError, UnicodeDecodeError):
            raise ProviderError("protocol_error") from None

    async def _sse_lines(self, response):
        buffer = bytearray()
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > 8 * 1024 * 1024:
                raise ProviderError("protocol_error")
            buffer.extend(chunk)
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer = bytearray(rest)
                if len(line) > 1024 * 1024:
                    raise ProviderError("protocol_error")
                try:
                    yield line.rstrip(b"\r").decode("utf-8")
                except UnicodeDecodeError:
                    raise ProviderError("protocol_error") from None
            if len(buffer) > 1024 * 1024:
                raise ProviderError("protocol_error")
        if buffer:
            raise ProviderError("protocol_error")

    def _cost(self, usage):
        cfg = self.config
        inp, out, cached = (
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cached_input_tokens"],
        )
        if usage.get("cache_creation_input_tokens"):
            return None
        if (
            inp is None
            or out is None
            or cfg.input_price_per_million is None
            or cfg.output_price_per_million is None
            or not cfg.currency
        ):
            return None
        if cached is None:
            return None
        if cached and cfg.cached_input_price_per_million is None:
            return None
        if not all(isinstance(n, int) and n >= 0 for n in (inp, out, cached)) or cached > inp:
            return None
        return {
            "amount": (
                (inp - cached) * cfg.input_price_per_million
                + cached * (cfg.cached_input_price_per_million or 0)
                + out * cfg.output_price_per_million
            )
            / 1_000_000,
            "currency": cfg.currency,
        }

    def _response(self, data, request):
        try:
            if self.config.kind == "anthropic":
                blocks = data["content"]
                finish = data.get("stop_reason")
                refused = any(block.get("type") == "refusal" for block in blocks)
                usage = self._usage(data.get("usage"))
                if (
                    finish in {"length", "max_tokens", "model_context_window_exceeded"}
                    or refused
                    or finish in {"content_filter", "refusal"}
                ):
                    self._validate_finish(finish, [], usage, refused)
                text = "".join(block["text"] for block in blocks if block["type"] == "text")
                calls = [
                    {"id": b["id"], "name": b["name"], "arguments": b["input"]}
                    for b in blocks
                    if b["type"] == "tool_use"
                ]
            else:
                choice = data["choices"][0]
                message = choice["message"]
                finish = choice.get("finish_reason")
                refused = bool(message.get("refusal"))
                usage = self._usage(data.get("usage"))
                if (
                    finish in {"length", "max_tokens", "model_context_window_exceeded"}
                    or refused
                    or finish in {"content_filter", "refusal"}
                ):
                    self._validate_finish(finish, [], usage, refused)
                text = message.get("content") or ""
                calls = [
                    {
                        "id": c["id"],
                        "name": c["function"]["name"],
                        "arguments": json.loads(c["function"]["arguments"]),
                    }
                    for c in message.get("tool_calls", [])
                ]
            if not isinstance(text, str) or any(
                not isinstance(c["arguments"], dict) for c in calls
            ):
                raise ValueError
            self._validate_finish(finish, calls, usage, refused)
            return ModelResponse(
                text=text,
                tool_calls=calls,
                usage=usage,
                provider_id=self.config.id,
                model_id=request.model_id,
                finish_reason=finish,
                provider_request_id=data.get("id"),
                cost=self._cost(usage),
            )
        except (KeyError, TypeError, ValueError, IndexError):
            raise ProviderError("protocol_error") from None

    def _validate_finish(self, finish, calls, usage, refused=False):
        if finish in {"length", "max_tokens", "model_context_window_exceeded"}:
            raise ProviderError("truncated", usage=usage)
        if refused or finish in {"content_filter", "refusal"}:
            raise ProviderError("refused", usage=usage)
        expected_tool = "tool_use" if self.config.kind == "anthropic" else "tool_calls"
        normal = {"end_turn", "stop_sequence"} if self.config.kind == "anthropic" else {"stop"}
        ids = [call.get("id") for call in calls]
        if (
            finish not in normal | {expected_tool}
            or bool(calls) != (finish == expected_tool)
            or any(
                not isinstance(call.get("id"), str)
                or not call["id"]
                or not isinstance(call.get("name"), str)
                or not call["name"]
                for call in calls
            )
            or len(set(ids)) != len(ids)
        ):
            raise ProviderError("protocol_error", usage=usage)

    async def _send(
        self, client, method, path, *, payload=None, streaming=False, max_retries=None, request=None
    ):
        retries = self.config.max_retries if max_retries is None else max_retries
        sent_any = False
        if method == "POST":
            request._budget_post_started = False
        for attempt in range(retries + 1):
            await self._pace(
                fail_fast=method == "POST"
                and request.budget_context is not None
                and request.budget_context.purpose == "worker"
                and not sent_any
            )
            try:
                req = client.build_request(
                    method,
                    self.config.base_url.rstrip("/") + path,
                    headers=self._credentials(),
                    json=payload,
                )
                attempt_id = None
                if method == "POST":
                    if self._budget_ledger is None or request.budget_context is None:
                        raise ProviderError("budget_unconfigured")
                    from forge_core.model_call_budget import BudgetExceeded, ConcurrencyUnavailable

                    deadline = time.monotonic() + min(
                        2.0, (request.timeout_seconds or self.config.timeout_seconds) / 2
                    )
                    while True:
                        try:
                            attempt_id = self._budget_ledger.admit(
                                request.budget_context,
                                self.config.id,
                                request.model_id,
                                input_tokens=len(
                                    json.dumps(payload, ensure_ascii=False).encode("utf-8")
                                )
                                + 64,
                                output_tokens=request.max_output_tokens,
                                remote_timeout=request.timeout_seconds
                                or self.config.timeout_seconds,
                            )
                            break
                        except ConcurrencyUnavailable:
                            if (
                                request.budget_context.purpose == "worker"
                                or time.monotonic() >= deadline
                            ):
                                raise ProviderError(
                                    "attempt_unknown" if sent_any else "concurrency_unavailable"
                                ) from None
                            await asyncio.sleep(0.05)
                        except (BudgetExceeded, ValueError):
                            raise ProviderError("budget_exhausted") from None
                sent_any = method == "POST"
                if sent_any:
                    request._budget_post_started = True
                response = await client.send(req, stream=streaming)
            except httpx.TimeoutException:
                raise ProviderError("timeout") from None
            except httpx.TransportError:
                raise ProviderError("network") from None
            if 200 <= response.status_code < 300:
                return response, attempt_id
            status = response.status_code
            retry_after = response.headers.get("retry-after", "0")
            try:
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > 8 * 1024 * 1024:
                        raise ProviderError("protocol_error")
            finally:
                await response.aclose()
            if attempt_id is not None:
                self._budget_ledger.reject_completed(attempt_id)
            if status == 429:
                try:
                    delay = float(retry_after)
                    if not math.isfinite(delay):
                        raise ValueError
                except ValueError:
                    try:
                        date = parsedate_to_datetime(retry_after)
                        delay = date.timestamp() - time.time()
                    except (TypeError, ValueError, OverflowError):
                        delay = 0.1
                if self._budget_ledger is not None:
                    self._budget_ledger.defer_provider(self.config.id, max(0.0, delay))
            if status in {429, 502, 503, 504} and attempt < retries:
                try:
                    wait = max(0.0, delay) if status == 429 else min(0.1 * 2**attempt, 1.0)
                except ValueError:
                    wait = 0.1
                await asyncio.sleep(wait)
                continue
            raise ProviderError(
                "auth"
                if status in {401, 403}
                else "rate_limit"
                if status == 429
                else "provider_error"
            )

    async def generate(self, request):
        request._budget_post_started = False
        started = time.monotonic()
        attempt_id = None
        try:
            self._credentials()
            payload = self._payload(request, False)
            if (
                request.budget_context is not None
                and request.budget_context.purpose == "worker"
                and self._semaphore.locked()
            ):
                raise ProviderError("concurrency_unavailable")
            async with (
                asyncio.timeout(request.timeout_seconds or self.config.timeout_seconds),
                self._semaphore,
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=request.timeout_seconds or self.config.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
            ):
                response = await self._send(
                    client,
                    "POST",
                    "/messages" if self.config.kind == "anthropic" else "/chat/completions",
                    payload=payload,
                    streaming=True,
                    max_retries=request.max_retries,
                    request=request,
                )
                response, attempt_id = response
                try:
                    result = self._response(await self._read_json(response), request)
                except (ValueError, TypeError):
                    raise ProviderError("protocol_error") from None
            self._budget_ledger.settle(attempt_id, result.usage)
            self._record("ok", started, request, result.usage)
            return result
        except TimeoutError:
            self._record("timeout", started, request)
            raise ProviderError("timeout") from None
        except httpx.TimeoutException:
            self._record("timeout", started, request)
            raise ProviderError("timeout") from None
        except httpx.TransportError:
            self._record("network", started, request)
            raise ProviderError("network") from None
        except ProviderError as exc:
            if attempt_id is not None and exc.usage is not None:
                self._budget_ledger.settle(attempt_id, exc.usage)
            self._record(exc.code, started, request, exc.usage)
            raise
        except asyncio.CancelledError:
            self._record("cancelled", started, request)
            raise

    async def discover(self):
        self._credentials()
        try:
            async with asyncio.timeout(self.config.timeout_seconds), self._semaphore:  # noqa: SIM117
                async with httpx.AsyncClient(
                    transport=self._transport,
                    timeout=self.config.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    response, _ = await self._send(client, "GET", "/models", streaming=True)
                    data = (await self._read_json(response))["data"]
                    if not isinstance(data, list) or any(
                        not isinstance(item, dict) or not isinstance(item.get("id"), str)
                        for item in data
                    ):
                        raise ValueError
                    return [_discovered_model(item, self.config.id) for item in data]
        except TimeoutError:
            raise ProviderError("timeout") from None
        except httpx.TimeoutException:
            raise ProviderError("timeout") from None
        except httpx.TransportError:
            raise ProviderError("network") from None
        except (ValueError, KeyError, TypeError):
            raise ProviderError("protocol_error") from None

    async def stream(self, request):
        started = time.monotonic()
        attempt_id = None
        text_parts = []
        tools = {}
        usage_raw = {}
        finish = None
        remote_id = None
        done = False
        refused = False
        ttft = None
        try:
            self._credentials()
            payload = self._payload(request, True)
            async with (
                asyncio.timeout(request.timeout_seconds or self.config.timeout_seconds),
                self._semaphore,
                httpx.AsyncClient(
                    transport=self._transport,
                    timeout=request.timeout_seconds or self.config.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
            ):
                response = await self._send(
                    client,
                    "POST",
                    "/messages" if self.config.kind == "anthropic" else "/chat/completions",
                    payload=payload,
                    streaming=True,
                    max_retries=request.max_retries,
                    request=request,
                )
                response, attempt_id = response
                async with aclosing(response):
                    data_lines = []
                    async for line in self._sse_lines(response):
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip(" "))
                            continue
                        if line or not data_lines:
                            continue
                        data = "\n".join(data_lines)
                        data_lines.clear()
                        if data == "[DONE]":
                            done = True
                            break
                        try:
                            event = json.loads(data)
                            if event.get("error"):
                                raise ProviderError("provider_error")
                            if self.config.kind == "anthropic":
                                kind = event.get("type")
                                if kind == "error":
                                    raise ProviderError("provider_error")
                                if kind == "message_start":
                                    remote_id = event["message"].get("id")
                                    usage_raw.update(event["message"].get("usage") or {})
                                elif kind == "content_block_start":
                                    block = event["content_block"]
                                    if block.get("type") == "refusal":
                                        refused = True
                                    if block.get("type") == "tool_use":
                                        tools[event["index"]] = {
                                            "id": block["id"],
                                            "name": block["name"],
                                            "arguments": "",
                                        }
                                elif kind == "content_block_delta":
                                    delta = event["delta"]
                                    if delta.get("type") == "text_delta":
                                        piece = delta["text"]
                                        if ttft is None:
                                            ttft = time.monotonic() - started
                                        text_parts.append(piece)
                                        yield StreamEvent(type="text_delta", text=piece)
                                    elif delta.get("type") == "input_json_delta":
                                        fragment = delta["partial_json"]
                                        tools[event["index"]]["arguments"] += fragment
                                        yield StreamEvent(
                                            type="tool_call_delta",
                                            data={
                                                "index": event["index"],
                                                "arguments_fragment": fragment,
                                            },
                                        )
                                elif kind == "message_delta":
                                    finish = event.get("delta", {}).get("stop_reason") or finish
                                    usage_raw.update(event.get("usage") or {})
                                elif kind == "message_stop":
                                    done = True
                                    break
                            else:
                                remote_id = event.get("id") or remote_id
                                if event.get("usage"):
                                    usage_raw = event["usage"]
                                for choice in event.get("choices", []):
                                    if choice.get("index", 0) != 0:
                                        continue
                                    finish = choice.get("finish_reason") or finish
                                    delta = choice.get("delta") or {}
                                    if delta.get("refusal"):
                                        refused = True
                                    piece = delta.get("content")
                                    if piece:
                                        if ttft is None:
                                            ttft = time.monotonic() - started
                                        text_parts.append(piece)
                                        yield StreamEvent(type="text_delta", text=piece)
                                    for call in delta.get("tool_calls", []):
                                        index = call["index"]
                                        item = tools.setdefault(
                                            index, {"id": "", "name": "", "arguments": ""}
                                        )
                                        item["id"] += call.get("id") or ""
                                        function = call.get("function") or {}
                                        item["name"] += function.get("name") or ""
                                        fragment = function.get("arguments") or ""
                                        item["arguments"] += fragment
                                        yield StreamEvent(
                                            type="tool_call_delta",
                                            data={"index": index, "arguments_fragment": fragment},
                                        )
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            raise ProviderError("protocol_error") from None
                    if not done or not finish:
                        raise ProviderError("protocol_error")
                    usage = self._usage(usage_raw)
                    if finish in {"length", "max_tokens", "model_context_window_exceeded"}:
                        raise ProviderError("truncated", usage=usage)
                    if refused or finish in {"content_filter", "refusal"}:
                        raise ProviderError("refused", usage=usage)
                    try:
                        calls = [
                            {
                                "id": t["id"],
                                "name": t["name"],
                                "arguments": json.loads(t["arguments"] or "{}"),
                            }
                            for _, t in sorted(tools.items())
                        ]
                        if any(
                            not c["id"] or not c["name"] or not isinstance(c["arguments"], dict)
                            for c in calls
                        ):
                            raise ValueError
                    except (ValueError, TypeError):
                        raise ProviderError("protocol_error") from None
                    self._validate_finish(finish, calls, usage, refused)
                    result = ModelResponse(
                        text="".join(text_parts),
                        tool_calls=calls,
                        usage=usage,
                        provider_id=self.config.id,
                        model_id=request.model_id,
                        finish_reason=finish,
                        provider_request_id=remote_id,
                        cost=self._cost(usage),
                    )
            self._budget_ledger.settle(attempt_id, usage)
            self._record("ok", started, request, usage, ttft)
            yield StreamEvent(type="usage", data=usage)
            yield StreamEvent(type="completed", response=result)
        except TimeoutError:
            self._record("timeout", started, request, ttft=ttft)
            raise ProviderError("timeout") from None
        except httpx.TimeoutException:
            self._record("timeout", started, request, ttft=ttft)
            raise ProviderError("timeout") from None
        except httpx.TransportError:
            self._record("network", started, request, ttft=ttft)
            raise ProviderError("network") from None
        except ProviderError as exc:
            if attempt_id is not None and exc.usage is not None:
                self._budget_ledger.settle(attempt_id, exc.usage)
            self._record(exc.code, started, request, exc.usage, ttft)
            raise
        except asyncio.CancelledError:
            self._record("cancelled", started, request, ttft=ttft)
            raise


class MockProvider:
    """Explicit offline stub; no result from this class is a real model response."""

    def __init__(self, response):
        self.response = response
        self.execution_mode = "mock"

    async def generate(self, request):
        return ModelResponse(
            text=self.response,
            tool_calls=[],
            usage={"input_tokens": None, "output_tokens": None, "cached_input_tokens": None},
            provider_id="mock",
            model_id=request.model_id,
            finish_reason="mock",
        )


__all__ = ["MockProvider", "ProviderConfig", "ProviderError", "ProviderGateway"]
