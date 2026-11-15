import asyncio

import pytest
from pydantic import ValidationError

from forge_core.domain import ModelProfile
from forge_core.model_intelligence import ObservationStore, decode_profile
from forge_core.registry import ModelRegistry
from forge_core.store import Store


def test_legacy_observation_is_explicit_override_and_unknown_stays_unknown(tmp_path):
    profile = decode_profile(
        '{"id":"m","provider":"p","display_name":"M","capabilities":{"coding":80}}', "m"
    )
    assert profile.capability_measurements["coding"].source == "user_override"
    assert profile.capability_measurements["coding"].n_samples is None
    assert profile.capability_measurements.get("vision") is None
    store = Store(tmp_path / "registry.sqlite3")
    ModelRegistry(store).upsert(profile)
    assert ModelRegistry(store).candidates({"vision": 50}) == []


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf")])
def test_bad_capability_is_rejected(value):
    with pytest.raises(ValidationError):
        ModelProfile(id="m", provider="p", display_name="M", capabilities={"coding": value})


def test_invalid_legacy_profile_is_quarantined_without_losing_raw():
    raw = '{"id":"m","provider":"p","display_name":"M","capabilities":{"coding":10000}}'
    profile = decode_profile(raw, "m")
    assert profile.id == "m" and not profile.enabled
    assert profile.quarantined_raw == raw
    assert profile.quarantine_reason
    assert decode_profile("{", "").quarantined_raw == "{"


def test_invalid_persisted_profile_does_not_break_other_profiles(tmp_path):
    store = Store(tmp_path / "legacy.sqlite3")
    registry = ModelRegistry(store)
    registry.upsert(ModelProfile(id="good", provider="p", display_name="Good"))
    raw = '{"id":"bad","provider":"p","display_name":"Bad","capabilities":{"coding":10000}}'
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO models VALUES (?,?,?)", ("bad", raw, "2020"))
    profiles = {profile.id: profile for profile in registry.list()}
    assert profiles["bad"].quarantined_raw == raw and not profiles["bad"].enabled
    assert profiles["good"].enabled


def test_observation_statistics_keep_mock_separate_from_live(tmp_path):
    class Gateway:
        execution_mode = "mock"

    observations = ObservationStore(Store(tmp_path / "observations.sqlite3"))
    observations.record(
        Gateway(),
        "m",
        "p",
        {"outcome": "success", "latency_seconds": 2, "ttft_seconds": 0.5, "output_tokens": 3},
    )
    observations.record(Gateway(), "m", "p", {"outcome": "timeout", "latency_seconds": 4})
    result = observations.summarize("m", "p", mode="mock")
    assert result["n_samples"] == 2 and result["reliability"] == 0.5
    assert result["timeout_rate"] == 0.5 and result["tokens_per_second"] == 2
    assert result["latency_p50_seconds"] == 2 and result["latency_p95_seconds"] == 4
    assert observations.summarize("m", "p", mode="live") is None
    with pytest.raises(ValidationError):
        observations.record(
            Gateway(), "m", "p", {"outcome": "success", "latency_seconds": float("nan")}
        )


def test_discovery_keeps_unknown_metadata_and_preserves_user_changes(tmp_path):
    class Gateway:
        execution_mode = "mock"

        async def discover(self):
            return [{"id": "m", "provider_id": "p"}]

    registry = ModelRegistry(Store(tmp_path / "discover.sqlite3"))
    found = asyncio.run(registry.discover(Gateway()))
    assert len(found) == 1 and found[0].context_window is None
    assert found[0].capability_measurements == {}
    found[0].enabled = False
    registry.upsert(found[0])
    assert asyncio.run(registry.discover(Gateway())) == []
    assert not registry.list()[0].enabled
