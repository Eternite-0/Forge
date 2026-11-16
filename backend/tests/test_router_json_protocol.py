"""Mock/SQLite protocol regressions; no real Provider quality claims."""

import asyncio
import copy
import hashlib
import json

import httpx
import jsonschema
import pytest
from pydantic import ValidationError
from test_router_intelligence_phase2 import ScriptedMock, decision, router

from forge_core.domain import DomainError
from forge_core.model_call_budget import ModelCallBudgetLedger
from forge_core.providers import ProviderConfig, ProviderGateway, schema_format_instruction
from forge_core.registry import ModelRegistry, TeamComposer
from forge_core.router import RouterService, _decision_schema, _router_initial_input_size
from forge_core.router_domain import RouterDecision
from forge_core.router_json_protocol import (
    json_response_diagnostics,
    normalize_router_json_transport,
    strict_router_wire_schema,
)
from forge_core.runtime import ModelResponse
from forge_core.store import Store


def response(text):
    return ModelResponse(text=text, tool_calls=[], usage={"input_tokens": 10, "output_tokens": 5}, finish_reason="mock")


def events(store, kind):
    return [json.loads(row["payload"]) for row in store.list_events() if row["event_type"] == kind]


@pytest.mark.parametrize("compact,autonomous", [(False, False), (True, False), (False, True), (True, True)])
def test_strict_wire_schema_requires_only_all_advertised_properties_and_preserves_local_defaults(compact, autonomous):
    original = RouterDecision.model_json_schema()
    snapshot = copy.deepcopy(original)
    schema = _decision_schema(compact=compact, autonomous=autonomous)

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert set(node.get("required", [])) == set(node.get("properties", {}))
                assert node.get("additionalProperties") is False
            assert "default" not in node
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    jsonschema.Draft202012Validator.check_schema(schema)
    assert RouterDecision.model_json_schema() == snapshot
    legacy = RouterDecision.model_validate(decision())
    assert legacy.constraints == [] and legacy.project_id is None
    if not compact and autonomous:
        jsonschema.validate(legacy.model_dump(mode="json"), schema)
        assert {part.get("type") for part in schema["properties"]["project_id"]["anyOf"]} == {"string", "null"}
        assert schema["properties"]["constraints"]["type"] == "array"


def test_exact_single_json_fence_is_audited_without_reflection_or_validator_bypass(tmp_path):
    store = Store(tmp_path / "fence.sqlite3")
    body = json.dumps(decision())
    text = "```json\n" + body + "\n```"
    provider = ScriptedMock(response(text))
    result = asyncio.run(router(store, provider).handle_user_message("Hello", request_id="fence"))
    assert result["intent"] == "chat" and len(provider.requests) == 1
    normalized = events(store, "router.response_transport_normalized")
    assert len(normalized) == 1
    assert normalized[0]["normalization"] == "exact_single_json_fence"
    assert normalized[0]["raw_response_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert normalized[0]["normalized_response_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert normalized[0]["provenance"] == "router_json_transport_v1"
    assert normalized[0]["validation"] == "pending_original_decision_and_recall_validators"
    assert normalized[0]["execution_mode"] == "mock"
    assert not events(store, "router.decision_rejected")


def test_json_rejection_keeps_only_safe_shape_and_original_hash(tmp_path):
    store = Store(tmp_path / "rejected.sqlite3")
    marker = "PRIVATE_MODEL_CONTENT_NEVER_LOGGED_2cf1"
    text = "Here is the decision: " + json.dumps(decision(reply=marker))
    provider = ScriptedMock(response(text))
    instance = router(store, provider)
    instance.max_repairs = 0
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(instance.handle_user_message("Hello", request_id="shape"))
    rejected = events(store, "router.decision_rejected")[0]
    assert rejected["response_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert rejected["response_shape"]["utf8_bytes"] == len(text.encode())
    assert rejected["response_shape"]["first_token_class"] == "letter"
    assert rejected["response_shape"]["json"]["error_code"] == "expected_value"
    assert marker not in json.dumps(store.list_events())
    assert store.list_quests() == [] and store.list_messages() == [] and len(provider.requests) == 1


def test_projection_preserves_properties_named_default_and_schema_constraints():
    original = {
        "type": "object", "additionalProperties": False, "required": [],
        "properties": {
            "default": {"type": "string", "default": "legacy", "minLength": 1},
            "values": {"type": "array", "items": {"type": "integer"}, "maxItems": 3},
        },
        "examples": [{"default": "annotation_data"}],
    }
    snapshot = copy.deepcopy(original)
    projected = strict_router_wire_schema(original)
    assert original == snapshot
    assert projected["required"] == ["default", "values"]
    assert projected["properties"]["default"] == {"type": "string", "minLength": 1}
    assert projected["properties"]["values"] == original["properties"]["values"]
    assert projected["examples"] == original["examples"]


def test_nested_team_refs_resolve_and_list_defaults_are_required_arrays():
    schema = _decision_schema(autonomous=True)
    assignment = schema["$defs"]["ExecutionAssignment"]
    assert assignment["required"] == ["objective", "role", "depends_on"]
    assert assignment["properties"]["depends_on"]["type"] == "array"
    full = RouterDecision.model_validate(decision("create_quest", execution_team={
        "strategy": "sequential", "assignments": [
            {"objective": "Research sources", "role": "research", "depends_on": []},
            {"objective": "Review source analysis", "role": "reviewer", "depends_on": [0]},
        ],
    })).model_dump(mode="json")
    jsonschema.validate(full, schema)
    del full["execution_team"]["assignments"][0]["depends_on"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(full, schema)
    full["execution_team"]["assignments"][0]["depends_on"] = None
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(full, schema)


@pytest.mark.parametrize("text", [
    'before\n```json\n{}\n```', '```json\n{}\n```\nafter',
    '```json\n{}\n```\n```json\n{}\n```', '```json\n{}',
    '```python\n{}\n```', '```JSON\n{}\n```', '````json\n{}\n````',
    '```json extra\n{}\n```', '```json {} ```', ' ```json\n{}\n```',
    '```json\n{}\n```\n', '```json\r\n{}\r\n```', '```json\n{}\r\n```',
])
def test_nonexact_transport_is_rejected_unchanged(tmp_path, text):
    assert normalize_router_json_transport(text) == (text, None)
    shape = json_response_diagnostics(text)
    assert shape["single_json_fence"] is False and "fenced_json" not in shape
    store = Store(tmp_path / "nonexact.sqlite3")
    provider = ScriptedMock(response(text))
    instance = router(store, provider)
    instance.max_repairs = 0
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(instance.handle_user_message("Hello", request_id="nonexact"))
    assert not events(store, "router.response_transport_normalized")
    assert len(provider.requests) == 1 and store.list_messages() == []


@pytest.mark.parametrize("body,expected", [
    ('{"intent":', "[root]: json_invalid"),
    (json.dumps(decision(priority=99)), "priority: less_than_equal"),
    (json.dumps(decision("create_quest", recalled_requirement_ids=["PRIVATE_UNKNOWN_MEMORY"])),
        "recall_source_not_presented_or_ineligible"),
    (json.dumps(decision("create_quest", execution_team={
        "strategy": "parallel", "assignments": [
            {"objective": "PRIVATE_INVALID_TEAM", "role": "research", "depends_on": [0]},
        ],
    })), "execution_team: team_dependencies_not_earlier_distinct"),
])
def test_transport_conversion_still_requires_original_schema_topology_and_recall(tmp_path, body, expected):
    store = Store(tmp_path / "original-validators.sqlite3")
    text = "```json\n" + body + "\n```"
    provider = ScriptedMock(response(text))
    instance = router(store, provider)
    instance.max_repairs = 0
    before = store.ensure_router()
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(instance.handle_user_message("Analyze the project", request_id="validators"))
    rejected = events(store, "router.decision_rejected")[0]
    assert rejected["errors"] == [expected]
    assert rejected["response_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert rejected["response_shape"]["single_json_fence"] is True
    assert len(events(store, "router.response_transport_normalized")) == 1
    assert "PRIVATE_" not in json.dumps(store.list_events())
    assert store.ensure_router() == before
    assert store.list_messages() == [] and store.list_quests() == []


def test_fenced_invalid_initial_is_reflected_raw_once_and_fenced_repair_is_validated(tmp_path):
    store = Store(tmp_path / "repair-fence.sqlite3")
    invalid = "```json\n" + json.dumps(decision(priority="PRIVATE_INVALID_VALUE")) + "\n```"
    corrected = "```json\n" + json.dumps(decision()) + "\n```"
    provider = ScriptedMock(response(invalid), response(corrected))
    instance = router(store, provider)
    result = asyncio.run(instance.handle_user_message("Hello", request_id="repair-fence"))
    assert result["intent"] == "chat" and len(provider.requests) == 2
    assert provider.requests[1].messages[-2] == {"role": "assistant", "content": invalid}
    assert provider.responses[0].text == invalid and provider.responses[1].text == corrected
    assert [item["stage"] for item in events(store, "router.response_transport_normalized")] == ["initial", "repair"]
    assert events(store, "router.decision_rejected")[0]["errors"] == ["priority: int_type"]
    assert "PRIVATE_INVALID_VALUE" not in json.dumps(store.list_events())
    assert "PRIVATE_INVALID_VALUE" not in json.dumps(store.list_messages())


def test_failed_fenced_reflection_does_not_add_a_second_repair(tmp_path):
    store = Store(tmp_path / "bounded-fence.sqlite3")
    invalid = "```json\n" + json.dumps(decision(priority=99)) + "\n```"
    provider = ScriptedMock(response(invalid))
    with pytest.raises(DomainError, match="Invalid Router model decision"):
        asyncio.run(router(store, provider).handle_user_message("Hello", request_id="bounded-fence"))
    assert len(provider.requests) == 2
    assert len(events(store, "router.response_transport_normalized")) == 2
    assert len(events(store, "router.decision_rejected")) == 2
    assert store.list_messages() == [] and store.list_quests() == []


def test_normalization_does_not_refund_output_bytes_or_change_idempotency(tmp_path):
    store = Store(tmp_path / "raw-budget.sqlite3")
    text = "```json\n" + json.dumps(decision(reply="x" * 1000)) + "\n```"
    provider = ScriptedMock(response(text))
    instance = router(store, provider)
    instance.output_reserve = len(text.encode()) // 8
    with pytest.raises(DomainError, match="Router response exceeds output budget"):
        asyncio.run(instance.handle_user_message("Hello", request_id="raw-budget"))
    assert not events(store, "router.response_transport_normalized")
    instance.output_reserve = 4096
    result = asyncio.run(instance.handle_user_message("Hello", request_id="accepted"))
    repeated = asyncio.run(instance.handle_user_message("Hello", request_id="accepted"))
    assert result == repeated and len(provider.requests) == 2
    assert len(events(store, "router.response_transport_normalized")) == 1


def test_fenced_task_creation_keeps_atomic_turn_and_does_not_self_conflict(tmp_path):
    store = Store(tmp_path / "fenced-task.sqlite3")
    provider = ScriptedMock(response("```json\n" + json.dumps(decision("create_quest")) + "\n```"))
    instance = router(store, provider)
    result = asyncio.run(instance.handle_user_message("Analyze this project", request_id="task"))
    repeated = asyncio.run(instance.handle_user_message("Analyze this project", request_id="task"))
    assert repeated == result and result["intent"] == "create_quest"
    assert len(provider.requests) == 1 and len(store.list_quests()) == 1
    assert len(store.list_messages()) == 2 and len(events(store, "router.response_transport_normalized")) == 1
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM router_turns").fetchone()[0] == 1


@pytest.mark.parametrize("text,first,error", [
    ("", "empty", "expected_value"), ("  \n", "empty", "expected_value"),
    ('{"a":1,}', "object", "expected_property_name"),
    ('{"a":1} explanation', "object", "extra_data"),
    ('Here is the decision: {"a":1}', "letter", "expected_value"),
    ('{"a":', "object", "expected_value"),
    ('{"a":"line\nvalue"}', "object", "invalid_control_character"),
    ('{"a":"\\q"}', "object", "invalid_escape"),
    ('\ufeff{"a":1}', "bom", "unexpected_bom"),
    ('\u00a0{"a":1}', "non_json_whitespace", "expected_value"),
])
def test_error_shapes_have_only_fixed_metadata(text, first, error):
    result = json_response_diagnostics(text)
    assert result["utf8_bytes"] == len(text.encode())
    assert result["first_token_class"] == first
    assert result["json"]["syntax"] == "invalid" and result["json"]["error_code"] == error
    assert 0 <= result["json"]["position_chars"] <= len(text)
    assert result["json"]["line"] >= 1 and result["json"]["column"] >= 1


@pytest.mark.parametrize("text,kind", [
    ('"private"', "string"), ('[]', "array"), ('42', "number"), ('false', "boolean"), ('null', "null"),
])
def test_valid_json_wrong_root_remains_schema_rejection(text, kind):
    assert json_response_diagnostics(text)["json"] == {"syntax": "valid", "root_kind": kind}
    candidate, metadata = normalize_router_json_transport("```json\n" + text + "\n```")
    assert metadata is not None
    with pytest.raises(ValidationError):
        RouterDecision.model_validate_json(candidate)


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity", '{"x":NaN}'])
def test_nonstandard_constants_have_fixed_codes(text):
    assert json_response_diagnostics(text)["json"] == {"syntax": "invalid", "error_code": "nonfinite_number"}


def test_unicode_positions_count_characters_while_size_counts_utf8_bytes():
    text = '{"name":"中文",\n"value":}'
    shape = json_response_diagnostics(text)
    assert shape["utf8_bytes"] > len(text)
    assert shape["json"]["position_chars"] == text.index("}")
    assert shape["json"]["line"] == 2 and shape["json"]["column"] == 9
    assert json_response_diagnostics('"\ud800"')["utf8_bytes"] is None


def test_no_private_fields_values_or_parser_messages_are_returned():
    marker = "PRIVATE_SECRET_NEVER_LOGGED_7b75"
    for text in (json.dumps({marker: marker}) + " " + marker,
        '```json\n{"' + marker + '":"' + marker + '"}\n```'):
        encoded = json.dumps(json_response_diagnostics(text))
        assert marker not in encoded
        assert "Expecting" not in encoded and "Extra data" not in encoded


@pytest.mark.parametrize("text,code", [
    ("1" * 5000, "json_value_limit"), ("[" * 3000, "nesting_limit"),
])
def test_parser_resource_limits_have_bounded_fixed_diagnostics(text, code):
    assert json_response_diagnostics(text)["json"] == {"syntax": "invalid", "error_code": code}


@pytest.mark.parametrize("value", [None, 3, {}, []])
def test_nontext_diagnostics_are_fixed_metadata(value):
    assert json_response_diagnostics(value) == {
        "utf8_bytes": None, "first_token_class": "not_text", "single_json_fence": False,
        "json": {"syntax": "not_text", "error_code": "not_text"},
    }


@pytest.mark.parametrize("window", [32768, 131072])
def test_native_gateway_schema_tool_rounds_reflection_and_original_framing(tmp_path, window):
    """Real Gateway/SQLite with MockTransport only, not a real model call."""
    store = Store(tmp_path / "native.sqlite3")
    wire_requests = []

    def transport(request):
        wire = json.loads(request.content)
        wire_requests.append(wire)
        index = len(wire_requests)
        if index == 1:
            message = {"content": "```json\nPRIVATE_TOOL_TEXT\n```", "tool_calls": [{
                "id": "lookup", "type": "function", "function": {
                    "name": "list_quests", "arguments": "{}",
                },
            }]}
        else:
            full = RouterDecision.model_validate(decision()).model_dump(mode="json")
            properties = wire["response_format"]["json_schema"]["schema"]["properties"]
            content = json.dumps({key: value for key, value in full.items() if key in properties})
            if index == 2:
                value = json.loads(content)
                value["reply"] = 7
                content = json.dumps(value)
            message = {"content": "```json\n" + content + "\n```"}
        return httpx.Response(200, json={
            "choices": [{"message": message, "finish_reason": "tool_calls" if index == 1 else "stop"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        })

    class ObservedGateway(ProviderGateway):
        async def generate(self, request):
            self.requests.append(copy.deepcopy(request))
            return await super().generate(request)

    gateway = ObservedGateway(
        ProviderConfig(id="protocol", kind="openai_compatible", base_url="https://example.invalid/v1",
            credential_ref="FORGE_PROVIDER_KEY_TEST", supports_tools=True, supports_json_schema=True,
            max_retries=0),
        credential_resolver=lambda _: "offline-placeholder",
        budget_ledger=ModelCallBudgetLedger(store.path, token_limit=500000, concurrency_limit=1),
        transport=httpx.MockTransport(transport),
    )
    gateway.requests = []
    instance = RouterService(store, TeamComposer(ModelRegistry(store)), provider=gateway,
        model_id="fixture", provider_id="protocol", context_limit=window, output_reserve=4096,
        autonomous=True)
    result = asyncio.run(instance.handle_user_message('Explain "中文" C:\\project\nbriefly', request_id="native"))
    assert result["intent"] == "chat" and len(wire_requests) == 3
    expected = _decision_schema(compact=window < 65536, autonomous=True)
    for wire, request in zip(wire_requests, gateway.requests, strict=True):
        assert wire["response_format"]["json_schema"] == {
            "name": "router_decision", "strict": True, "schema": expected,
        }
        assert wire["messages"][1] == {"role": "system", "content": schema_format_instruction(expected)}
        assert request.max_output_tokens == 4096 and request.context_limit == window
        assert len(json.dumps(wire, ensure_ascii=False).encode()) + 64 + 4096 <= window
    initial = gateway.requests[0]
    measured = _router_initial_input_size(json.loads(initial.messages[1]["content"]), expected,
        initial.tools, initial.model_id, initial.max_output_tokens)
    assert measured >= len(json.dumps(wire_requests[0], ensure_ascii=False).encode()) + 64
    assert [request.budget_context.purpose for request in gateway.requests] == ["router", "router", "router_reflect"]
    assert any(item.get("content") == "```json\nPRIVATE_TOOL_TEXT\n```" for item in wire_requests[1]["messages"])
    assert "tools" not in wire_requests[2]
    assert len(events(store, "router.response_transport_normalized")) == 2
    assert len(events(store, "router.decision_rejected")) == 1
    assert "PRIVATE_TOOL_TEXT" not in json.dumps(store.list_events())
