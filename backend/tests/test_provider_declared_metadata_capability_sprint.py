"""Scripted HTTP metadata contracts; no real Provider or benchmark is called."""

import asyncio
import json

import httpx

from forge_core.domain import ModelProfile
from forge_core.providers import ProviderConfig, ProviderGateway
from forge_core.registry import ModelRegistry
from forge_core.store import Store


def gateway(items):
    return ProviderGateway(ProviderConfig(id="p", kind="openai_compatible",
        base_url="https://example.invalid/v1", credential_ref="FORGE_PROVIDER_KEY_TEST"),
        credential_resolver=lambda _: "placeholder",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": items})))


def test_loopback_metadata_shape_populates_declared_limits_without_inventing_structured_support(tmp_path):
    item = {"id": "deepseek-v4.1-flash", "context_length": 1000000,
        "max_input_tokens": 1000000, "max_output_tokens": 128000,
        "supports_tool_calls": True, "private_other_field": "NEVER_PERSIST"}
    registry = ModelRegistry(Store(tmp_path / "models.sqlite3"))
    profile = asyncio.run(registry.discover(gateway([item])))[0]
    assert profile.context_window == 1000000 and profile.max_output_tokens == 128000
    assert profile.tool_calling_support.supported is True
    assert profile.tool_calling_support.source == "provider_declared"
    assert profile.structured_output_support is None
    assert profile.capability_measurements == {} and profile.performance_measurements == {}
    declarations = profile.provenance["provider_declared"]
    assert declarations["fields"]["context_window"]["source_fields"] == {
        "context_length": 1000000, "max_input_tokens": 1000000,
    }
    assert declarations["fields"]["context_window"]["conflicting"] is False
    assert declarations["observed_at"] and "not benchmark" in declarations["scope"]
    assert "NEVER_PERSIST" not in profile.model_dump_json()


def test_conflicting_positive_context_declarations_use_the_smaller_ceiling(tmp_path):
    item = {"id": "m", "context_window": 100000000, "context_length": 1000000,
        "max_input_tokens": 900000, "supports_structured_output": True,
        "supports_json_schema": False}
    registry = ModelRegistry(Store(tmp_path / "conflicts.sqlite3"))
    profile = asyncio.run(registry.discover(gateway([item])))[0]
    assert profile.context_window == 900000
    assert profile.structured_output_support.supported is False
    fields = profile.provenance["provider_declared"]["fields"]
    assert fields["context_window"]["conflicting"] is True
    assert fields["supports_structured_output"]["conflicting"] is True
    assert fields["context_window"]["source_fields"]["context_window"] == 100000000


def test_invalid_bool_numeric_strings_and_unknown_protocols_remain_unknown(tmp_path):
    item = {"id": "m", "context_window": True, "context_length": "1000000",
        "max_input_tokens": -1, "max_output_tokens": False,
        "supports_tool_calls": 1, "supports_structured_output": "true"}
    source = gateway([item])
    discovered = asyncio.run(source.discover())[0]
    assert "context_window" not in discovered and "supports_tool_calls" not in discovered
    assert discovered["declaration_provenance"]["invalid_fields"] == [
        "context_length", "context_window", "max_input_tokens", "max_output_tokens",
        "supports_structured_output", "supports_tool_calls",
    ]
    profile = asyncio.run(ModelRegistry(Store(tmp_path / "invalid.sqlite3")).discover(source))[0]
    assert profile.context_window is None and profile.max_output_tokens is None
    assert profile.tool_calling_support is None and profile.structured_output_support is None


def test_identity_only_shape_and_existing_operator_changes_remain_unchanged(tmp_path):
    assert asyncio.run(gateway([{"id": "m"}]).discover()) == [{"id": "m", "provider_id": "p"}]
    registry = ModelRegistry(Store(tmp_path / "preserved.sqlite3"))
    profile = asyncio.run(registry.discover(gateway([{"id": "m", "context_length": 1000000}])))[0]
    profile.context_window, profile.max_output_tokens, profile.enabled = 4096, 512, False
    profile.provenance = {"operator": "unchanged"}
    registry.upsert(profile)
    operator = ModelProfile(id="operator-profile", provider="p", model_id="m",
        display_name="Separate user profile", context_window=8192, enabled=False)
    registry.upsert(operator)
    before = [item.model_dump(mode="json") for item in registry.list()]
    assert asyncio.run(registry.discover(gateway([{"id": "m", "context_length": 2000000,
        "max_output_tokens": 128000, "supports_tool_calls": True}]))) == []
    assert [item.model_dump(mode="json") for item in registry.list()] == before
    assert "2000000" not in json.dumps(before)
