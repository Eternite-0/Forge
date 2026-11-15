"""Validated, versioned non-secret settings persisted under one workspace."""

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .providers.config import ProviderConfig

_lock = threading.Lock()
_applied = {}


class OperatorProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    kind: Literal["openai_compatible", "deepseek", "anthropic"]
    base_url: str = Field(min_length=1, max_length=2048)
    credential_ref: str = Field(pattern=r"^FORGE_PROVIDER_KEY_[A-Z0-9_]{1,128}$")
    supports_tools: bool
    supports_json_schema: bool
    allow_insecure_loopback: bool = False

    @model_validator(mode="after")
    def validate_provider(self):
        ProviderConfig(**self.model_dump())
        return self


class OperatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    provider: OperatorProvider | None
    router_model_id: str | None = Field(default=None, min_length=1, max_length=512)
    router_context_limit: int = Field(ge=2049, le=10_000_000)
    model_token_limit: int | None = Field(default=None, ge=0, le=1_000_000_000)
    enforce_token_budgets: bool = False
    model_concurrency_limit: int = Field(ge=1, le=64)
    network_hosts: list[str] = Field(max_length=64)

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.router_model_id and self.provider is None:
            raise ValueError("Router model requires a provider")
        if self.enforce_token_budgets and self.model_token_limit is None:
            raise ValueError("Enabled token budgets require an explicit limit")
        if len(set(self.network_hosts)) != len(self.network_hosts) or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
            for host in self.network_hosts
        ):
            raise ValueError("Invalid network host list")
        return self


def _path(root):
    return Path(root) / "data" / "operator-settings.json"


def _reject_reparse(path):
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Invalid settings path")


def read_operator_settings(root):
    path = _path(root)
    _reject_reparse(path.parent)
    _reject_reparse(path)
    if not path.exists():
        return {"revision": 0, "config": None}
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("Invalid operator settings file")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or set(data) != {"revision", "config"}
        or type(data["revision"]) is not int
        or data["revision"] < 1
    ):
        raise ValueError("Invalid operator settings file")
    return {
        "revision": data["revision"],
        "config": OperatorConfig.model_validate(data["config"]).model_dump(),
    }


def write_operator_settings(root, expected_revision, config):
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Invalid settings revision")
    validated = OperatorConfig.model_validate(config).model_dump()
    with _lock:
        current = read_operator_settings(root)
        if current["revision"] != expected_revision:
            raise RuntimeError("Settings changed")
        path = _path(root)
        _reject_reparse(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"revision": expected_revision + 1, "config": validated}
        handle, temporary = tempfile.mkstemp(prefix=".forge-settings-", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return payload


def apply_operator_settings(root):
    for key, previous in tuple(_applied.items()):
        if os.environ.get(key) == previous:
            del os.environ[key]
    _applied.clear()
    data = read_operator_settings(root)
    config = data["config"]
    if config is None:
        return data
    provider = config["provider"]
    values = {
        "FORGE_ROUTER_CONTEXT_LIMIT": str(config["router_context_limit"]),
        "FORGE_ENFORCE_TOKEN_BUDGETS": str(config["enforce_token_budgets"]).lower(),
        "FORGE_MODEL_CONCURRENCY_LIMIT": str(config["model_concurrency_limit"]),
        "FORGE_TOOL_NETWORK_HOSTS_JSON": json.dumps(config["network_hosts"]),
    }
    if config["model_token_limit"] is not None:
        values["FORGE_MODEL_TOKEN_LIMIT"] = str(config["model_token_limit"])
    if provider:
        values.update(
            FORGE_PROVIDER_ID=provider["id"],
            FORGE_PROVIDER_KIND=provider["kind"],
            FORGE_PROVIDER_BASE_URL=provider["base_url"],
            FORGE_PROVIDER_CREDENTIAL_REF=provider["credential_ref"],
            FORGE_PROVIDER_SUPPORTS_TOOLS=str(provider["supports_tools"]).lower(),
            FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA=str(provider["supports_json_schema"]).lower(),
            FORGE_PROVIDER_ALLOW_INSECURE_LOOPBACK=str(provider["allow_insecure_loopback"]).lower(),
        )
    if config["router_model_id"]:
        values["FORGE_ROUTER_MODEL_ID"] = config["router_model_id"]
    for key, value in values.items():
        if key not in os.environ:
            os.environ[key] = value
            _applied[key] = value
    return data


def overridden_operator_keys():
    relevant = {
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
        "FORGE_PROVIDER_SUPPORTS_TOOLS",
        "FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA",
        "FORGE_PROVIDER_ALLOW_INSECURE_LOOPBACK",
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_ROUTER_CONTEXT_LIMIT",
        "FORGE_MODEL_TOKEN_LIMIT",
        "FORGE_ENFORCE_TOKEN_BUDGETS",
        "FORGE_MODEL_CONCURRENCY_LIMIT",
        "FORGE_TOOL_NETWORK_HOSTS_JSON",
    }
    return sorted(key for key in relevant if key in os.environ and key not in _applied)
