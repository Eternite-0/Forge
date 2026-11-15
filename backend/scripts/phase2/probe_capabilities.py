"""Phase 2 live capability probe against an OpenAI-compatible local gateway.

Reads credentials only from the process environment. Writes a redacted JSON report.
Never prints or persists the credential.
"""

import asyncio
import json
import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE = os.environ.get("FORGE_PROBE_BASE_URL", "http://127.0.0.1:8788/v1").rstrip("/")
MODEL = os.environ.get("FORGE_PROBE_MODEL", "deepseek-v4.1-flash")
KEY = os.environ.get("FORGE_PROVIDER_KEY_LIVE_TEST")
OUT = Path(__file__).resolve().parents[3] / ".local" / "phase2" / "provider"

if not KEY:
    raise SystemExit("FORGE_PROVIDER_KEY_LIVE_TEST is required in the process environment")

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "chat",
                "create_quest",
                "continue_quest",
                "update_quest",
                "status_query",
                "approval_response",
                "ambiguous",
            ],
        },
        "reply": {"type": "string", "minLength": 1},
        "title": {"type": ["string", "null"]},
        "goal": {"type": ["string", "null"]},
        "quest_id": {"type": ["string", "null"]},
        "candidates": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent", "reply", "title", "goal", "quest_id", "candidates"],
    "additionalProperties": False,
}

ROUTER_INSTRUCTION = (
    "You are FORGE's persistent Router. Return only a JSON object with exactly the keys "
    "intent, reply, title, goal, quest_id, candidates. intent must be one of: chat, "
    "create_quest, continue_quest, update_quest, status_query, approval_response, ambiguous. "
    "Use null for unknown title/goal/quest_id. candidates must be an array of strings."
)
ROUTER_TASK = (
    "User message: help me turn last week's data cleaning script into an incremental update, "
    "and assess the risk. There is no historical Quest. Produce the RouterDecision."
)

FENCE = chr(96) * 3


def redact(value):
    if isinstance(value, str):
        return value.replace(KEY, "[REDACTED]")
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {str(k).replace(KEY, "[REDACTED]"): redact(v) for k, v in value.items()}
    return value


def validate_router(obj):
    problems = []
    if not isinstance(obj, dict):
        return ["not_an_object"]
    expected = {"intent", "reply", "title", "goal", "quest_id", "candidates"}
    if set(obj) != expected:
        problems.append("keys_mismatch:" + ",".join(sorted(set(obj) ^ expected)))
    if obj.get("intent") not in ROUTER_SCHEMA["properties"]["intent"]["enum"]:
        problems.append("bad_intent")
    if not isinstance(obj.get("reply"), str) or not obj.get("reply", "").strip():
        problems.append("bad_reply")
    if not isinstance(obj.get("candidates"), list) or any(
        not isinstance(x, str) for x in obj.get("candidates", [])
    ):
        problems.append("bad_candidates")
    for key in ("title", "goal", "quest_id"):
        if obj.get(key) is not None and not isinstance(obj.get(key), str):
            problems.append("bad_" + key)
    return problems


def parse_content(text):
    if not isinstance(text, str):
        return None, ["empty_content"]
    stripped = text.strip()
    if stripped.startswith(FENCE):
        stripped = stripped[len(FENCE) :]
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.removesuffix(FENCE)
        stripped = stripped.strip()
    try:
        return json.loads(stripped), []
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(stripped[start : end + 1]), ["recovered_by_brace_scan"]
            except json.JSONDecodeError:
                return None, ["invalid_json"]
        return None, ["invalid_json"]


async def post(client, payload, timeout=60):
    started = time.perf_counter()
    try:
        response = await client.post(
            "/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer " + KEY},
            timeout=timeout,
        )
    except (httpx.TimeoutException, TimeoutError):
        return {"error": "timeout", "latency_ms": (time.perf_counter() - started) * 1000}
    except httpx.TransportError:
        return {"error": "network", "latency_ms": (time.perf_counter() - started) * 1000}
    entry = {
        "http_status": response.status_code,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    try:
        body = response.json()
    except ValueError:
        entry["error"] = "non_json_body"
        entry["body_prefix"] = response.text[:200]
        return entry
    entry["body"] = body
    return entry


async def stream_case(client):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Count from 1 to 12 separated by spaces."}],
        "max_tokens": 256,
        "stream": True,
    }
    chunks, text = 0, []
    started = time.perf_counter()
    ttft = None
    status = None
    try:
        async with client.stream(
            "POST",
            "/chat/completions",
            json=payload,
            headers={"Authorization": "Bearer " + KEY},
            timeout=60,
        ) as response:
            status = response.status_code
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunks += 1
                if ttft is None:
                    ttft = round((time.perf_counter() - started) * 1000, 1)
                try:
                    piece = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (piece.get("choices") or [{}])[0].get("delta") or {}
                if isinstance(delta.get("content"), str):
                    text.append(delta["content"])
    except (httpx.TimeoutException, TimeoutError):
        return {"error": "timeout"}
    except httpx.TransportError:
        return {"error": "network"}
    return {
        "http_status": status,
        "chunks": chunks,
        "ttft_ms": ttft,
        "total_ms": round((time.perf_counter() - started) * 1000, 1),
        "streamed_chars": len("".join(text)),
        "valid": chunks > 0 and len("".join(text).strip()) > 0,
    }


TOOL = {
    "type": "function",
    "function": {
        "name": "emit_router_decision",
        "description": "Emit the RouterDecision for the user message.",
        "parameters": ROUTER_SCHEMA,
    },
}


def score(entry, mode):
    if "error" in entry:
        return {"ok": False, "error": entry["error"], "latency_ms": entry.get("latency_ms")}
    body = entry.get("body") or {}
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    usage = body.get("usage") or {}
    out = {
        "http_status": entry.get("http_status"),
        "finish_reason": choice.get("finish_reason"),
        "latency_ms": entry.get("latency_ms"),
        "total_tokens": usage.get("total_tokens"),
    }
    if mode == "tool":
        calls = message.get("tool_calls") or []
        out["tool_calls"] = len(calls)
        if len(calls) != 1:
            out.update(ok=False, problems=["tool_call_count:" + str(len(calls))])
            return out
        raw = calls[0].get("function", {}).get("arguments")
        out["arguments_chars"] = len(raw or "")
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            out.update(ok=False, problems=["invalid_json_arguments"])
            return out
        problems = validate_router(obj)
        out.update(ok=not problems, problems=problems, sample=obj)
        return out
    obj, notes = parse_content(message.get("content"))
    if obj is None:
        out.update(
            ok=False,
            problems=notes,
            raw_prefix=(message.get("content") or "")[:200],
        )
        return out
    problems = validate_router(obj) + notes
    out.update(ok=not problems, problems=problems, sample=obj)
    return out


async def structured_matrix(client, repeats):
    results = {"json_schema": [], "tool_call": [], "prompt_json": []}
    system = {"role": "system", "content": ROUTER_INSTRUCTION}
    user = {"role": "user", "content": ROUTER_TASK}
    for _ in range(repeats):
        results["json_schema"].append(
            score(
                await post(
                    client,
                    {
                        "model": MODEL,
                        "messages": [system, user],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {
                                "name": "router_decision",
                                "strict": True,
                                "schema": ROUTER_SCHEMA,
                            },
                        },
                        "max_tokens": 700,
                        "stream": False,
                        "temperature": 0.2,
                    },
                ),
                mode="content",
            )
        )
        results["tool_call"].append(
            score(
                await post(
                    client,
                    {
                        "model": MODEL,
                        "messages": [system, user],
                        "tools": [TOOL],
                        "tool_choice": {
                            "type": "function",
                            "function": {"name": "emit_router_decision"},
                        },
                        "max_tokens": 700,
                        "stream": False,
                        "temperature": 0.2,
                    },
                ),
                mode="tool",
            )
        )
        results["prompt_json"].append(
            score(
                await post(
                    client,
                    {
                        "model": MODEL,
                        "messages": [system, user],
                        "max_tokens": 700,
                        "stream": False,
                        "temperature": 0.2,
                    },
                ),
                mode="content",
            )
        )
    return results


async def auto_tool_case(client):
    entry = await post(
        client,
        {
            "model": MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": "I need the current Quest list before answering. Use a tool.",
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "list_quests",
                        "description": "Read current Quest summaries",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    },
                }
            ],
            "tool_choice": "auto",
            "max_tokens": 300,
            "stream": False,
        },
    )
    if "error" in entry:
        return {"ok": False, "error": entry["error"]}
    choice = (entry.get("body") or {}).get("choices", [{}])[0]
    message = choice.get("message") or {}
    calls = message.get("tool_calls") or []
    return {
        "ok": bool(calls) and calls[0].get("function", {}).get("name") == "list_quests",
        "finish_reason": choice.get("finish_reason"),
        "tool_calls": [c.get("function", {}).get("name") for c in calls],
        "content_prefix": (message.get("content") or "")[:120],
    }


async def needle_case(client, target_tokens=6000):
    filler = "The quick brown fox jumps over the lazy dog. " * 60
    unit_tokens = 360
    repeats = max(1, target_tokens // unit_tokens)
    body = "\n".join(filler for _ in range(repeats))
    marker = "FORGE-NEEDLE-77319"
    text = body + "\nHidden marker: " + marker + "\n" + body
    entry = await post(
        client,
        {
            "model": MODEL,
            "messages": [
                {"role": "user", "content": text + "\n\nReply with only the hidden marker value."},
            ],
            "max_tokens": 64,
            "stream": False,
            "temperature": 0,
        },
        timeout=120,
    )
    if "error" in entry:
        return {"ok": False, "error": entry["error"]}
    body_json = entry.get("body") or {}
    content = ((body_json.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    usage = body_json.get("usage") or {}
    return {
        "ok": marker in content,
        "http_status": entry.get("http_status"),
        "input_tokens": usage.get("prompt_tokens"),
        "latency_ms": entry.get("latency_ms"),
        "content_prefix": content[:80],
        "approx_request_tokens": repeats * unit_tokens * 2,
    }


async def concurrency_case(client, width=4):
    async def one():
        return await post(
            client,
            {
                "model": MODEL,
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                "max_tokens": 16,
                "stream": False,
            },
        )

    started = time.perf_counter()
    results = await asyncio.gather(*[one() for _ in range(width)])
    wall = round((time.perf_counter() - started) * 1000, 1)
    ok = sum(1 for entry in results if "error" not in entry and entry.get("http_status") == 200)
    latencies = [entry.get("latency_ms") for entry in results if entry.get("latency_ms")]
    return {
        "width": width,
        "succeeded": ok,
        "wall_ms": wall,
        "serial_sum_ms": round(sum(latencies), 1) if latencies else None,
        "statuses": [entry.get("http_status") or entry.get("error") for entry in results],
    }


async def model_list(client):
    try:
        response = await client.get(
            "/models", headers={"Authorization": "Bearer " + KEY}, timeout=30
        )
    except (httpx.TimeoutException, TimeoutError):
        return {"error": "timeout"}
    except httpx.TransportError:
        return {"error": "network"}
    out = {"http_status": response.status_code}
    if response.status_code != 200:
        return out
    try:
        data = response.json().get("data") or []
    except ValueError:
        return {**out, "error": "non_json_body"}
    out["count"] = len(data)
    out["ids"] = [row.get("id") for row in data][:80]
    target = next((row for row in data if row.get("id") == MODEL), None)
    out["target_entry"] = {k: v for k, v in (target or {}).items() if k != "id"}
    return out


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "live",
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": BASE,
        "model": MODEL,
        "credential_source": "process environment FORGE_PROVIDER_KEY_LIVE_TEST",
        "layer_note": "Proxy reachability, upstream model completion and answer correctness are reported separately.",
        "cases": {},
    }
    async with httpx.AsyncClient(base_url=BASE, follow_redirects=False, trust_env=False) as client:
        report["cases"]["models"] = await model_list(client)
        chat_entry = await post(
            client,
            {
                "model": MODEL,
                "messages": [{"role": "user", "content": "Reply with the word hello."}],
                "max_tokens": 64,
                "stream": False,
            },
        )
        chat_body = chat_entry.get("body") or {}
        chat_message = (chat_body.get("choices") or [{}])[0].get("message") or {}
        chat_text = (chat_message.get("content") or "").strip()
        report["cases"]["chat"] = [
            {
                "http_status": chat_entry.get("http_status"),
                "latency_ms": chat_entry.get("latency_ms"),
                "usage": chat_body.get("usage"),
                "text": chat_text[:200],
                "ok": chat_entry.get("http_status") == 200
                and isinstance(chat_message.get("content"), str)
                and bool(chat_text),
                "probe_note": "minimal conversation validity only; not a router-schema check",
            }
        ]
        report["cases"]["stream"] = await stream_case(client)
        report["cases"]["auto_tool"] = await auto_tool_case(client)
        report["cases"]["structured"] = await structured_matrix(client, repeats=4)
        report["cases"]["needle"] = await needle_case(client)
        report["cases"]["concurrency"] = await concurrency_case(client)

    structured = report["cases"]["structured"]
    summary = {}
    for name, rows in structured.items():
        oks = [row for row in rows if row.get("ok")]
        latencies = [row["latency_ms"] for row in rows if row.get("latency_ms")]
        summary[name] = {
            "attempts": len(rows),
            "schema_valid": len(oks),
            "success_rate": round(len(oks) / len(rows), 3) if rows else None,
            "median_latency_ms": statistics.median(latencies) if latencies else None,
            "problems": sorted({p for row in rows for p in row.get("problems", [])}),
        }
    report["structured_summary"] = summary
    report["verdict"] = {
        "chat": bool(report["cases"]["chat"][0].get("ok")),
        "streaming": bool(report["cases"]["stream"].get("valid")),
        "tool_calling_forced": summary["tool_call"]["success_rate"] == 1.0,
        "tool_calling_auto": bool(report["cases"]["auto_tool"].get("ok")),
        "json_schema_mode_reliable": summary["json_schema"]["success_rate"] == 1.0,
        "prompt_json_mode_reliable": summary["prompt_json"]["success_rate"] == 1.0,
    }
    (OUT / ("capabilities-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")).write_text(
        json.dumps(redact(report), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    printable = {
        "verdict": report["verdict"],
        "structured": summary,
        "stream": report["cases"]["stream"],
        "auto_tool": report["cases"]["auto_tool"],
        "needle": report["cases"]["needle"],
        "concurrency": report["cases"]["concurrency"],
        "models": {k: v for k, v in report["cases"]["models"].items() if k != "ids"},
    }
    print(json.dumps(redact(printable), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
