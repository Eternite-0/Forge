"""Explicit live loopback protocol probe; run only with operator-supplied process secret."""

import asyncio
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import httpx


async def main():
    secret = os.environ.get("FORGE_PROVIDER_KEY_LIVE_TEST")
    if not secret or not secret.isascii() or "\r" in secret or "\n" in secret:
        raise SystemExit("FORGE_PROVIDER_KEY_LIVE_TEST is required in process environment")
    model = "deepseek-v4.1-flash"
    url = "http://127.0.0.1:8788/v1/chat/completions"
    path = Path(__file__).resolve().parents[2] / ".local" / "phase1" / "milestone5" / "live-probe"
    path.mkdir(parents=True, exist_ok=True)
    report_path = (
        path
        / f"protocol-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}.json"
    )
    tool = {
        "type": "function",
        "function": {
            "name": "add_numbers",
            "description": "Add two numbers",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        },
    }
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    cases = {
        "chat": {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with the word hello."}],
            "max_tokens": 128,
            "stream": False,
        },
        "forced_tool": {
            "model": model,
            "messages": [{"role": "user", "content": "Call add_numbers with a=2 and b=3."}],
            "tools": [tool],
            "tool_choice": {"type": "function", "function": {"name": "add_numbers"}},
            "max_tokens": 256,
            "stream": False,
        },
        "strict_schema": {
            "model": model,
            "messages": [{"role": "user", "content": 'Return exactly {"ok":true}.'}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "probe", "strict": True, "schema": schema},
            },
            "max_tokens": 128,
            "stream": False,
        },
    }

    def redact(value):
        if isinstance(value, str):
            return value.replace(secret, "[REDACTED]")
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key).replace(secret, "[REDACTED]"): redact(item) for key, item in value.items()
            }
        return value

    result = {"mode": "live", "endpoint": url, "model": model, "cases": {}}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
        for name, payload in cases.items():
            entry = {"request": payload, "attempts": 1}
            try:
                async with (
                    asyncio.timeout(20),
                    client.stream(
                        "POST", url, headers={"Authorization": f"Bearer {secret}"}, json=payload
                    ) as response,
                ):
                    entry["http_status"] = response.status_code
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            raise ValueError("response_too_large")
                try:
                    decoded = json.loads(body)
                    entry["response"] = redact(decoded)
                    choice = decoded.get("choices", [{}])[0]
                    message = choice.get("message", {})
                    entry["finish_reason"] = choice.get("finish_reason")
                    entry["usage"] = decoded.get("usage")
                    if name == "chat":
                        entry["chat_valid"] = isinstance(message.get("content"), str) and bool(
                            message["content"].strip()
                        )
                    if name == "forced_tool":
                        calls = message.get("tool_calls") or []
                        valid = False
                        if (
                            len(calls) == 1
                            and calls[0].get("function", {}).get("name") == "add_numbers"
                        ):
                            args = json.loads(calls[0]["function"]["arguments"])
                            valid = args == {"a": 2, "b": 3}
                        entry["tool_call_valid"] = valid
                    if name == "strict_schema":
                        entry["schema_valid"] = json.loads(message.get("content") or "null") == {
                            "ok": True
                        }
                        entry["schema_note"] = (
                            "One conforming response does not establish server-side schema enforcement."
                        )
                except (ValueError, TypeError, KeyError, IndexError):
                    entry["parse_error"] = "invalid_protocol_response"
            except (httpx.TimeoutException, TimeoutError):
                entry["error"] = "timeout"
            except httpx.TransportError:
                entry["error"] = "network"
            except ValueError:
                entry["error"] = "response_too_large"
            result["cases"][name] = entry
    success = all(
        result["cases"][name].get("http_status") == 200
        and result["cases"][name].get(check) is True
        and result["cases"][name].get("finish_reason") == finish
        for name, check, finish in (
            ("chat", "chat_valid", "stop"),
            ("forced_tool", "tool_call_valid", "tool_calls"),
            ("strict_schema", "schema_valid", "stop"),
        )
    )
    result["status"] = "passed_all_three" if success else "partial_or_failed"
    report_path.write_text(
        json.dumps(redact(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            redact(
                {
                    "report": str(report_path),
                    "status": result["status"],
                    "cases": {
                        name: {
                            key: value
                            for key, value in entry.items()
                            if key
                            in {
                                "http_status",
                                "error",
                                "parse_error",
                                "finish_reason",
                                "chat_valid",
                                "tool_call_valid",
                                "schema_valid",
                                "usage",
                            }
                        }
                        for name, entry in result["cases"].items()
                    },
                }
            ),
            ensure_ascii=False,
        )
    )
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
