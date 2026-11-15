"""Provider configuration contains credential references, never credential values."""

import math
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProviderConfig:
    id: str
    kind: str
    base_url: str
    credential_ref: str
    max_retries: int = 1
    timeout_seconds: float = 30.0
    concurrency: int = 4
    min_interval_seconds: float = 0.0
    supports_tools: bool = False
    supports_json_schema: bool = False
    allow_insecure_loopback: bool = False
    input_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    currency: str | None = None
    reasoning_effort: str | None = None

    def __post_init__(self):
        parsed = urlsplit(self.base_url)
        if self.kind not in {"openai_compatible", "deepseek", "anthropic"}:
            raise ValueError("Unknown provider kind")
        if self.reasoning_effort is not None and (
            self.kind not in {"openai_compatible", "deepseek"}
            or not isinstance(self.reasoning_effort, str)
            or self.reasoning_effort
            not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        ):
            raise ValueError("Invalid provider reasoning effort configuration")
        numeric_loopback = parsed.hostname in {"127.0.0.1", "::1"}
        if (
            (
                parsed.scheme != "https"
                and not (
                    parsed.scheme == "http"
                    and self.allow_insecure_loopback is True
                    and numeric_loopback
                )
            )
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Provider base URL must be HTTPS without credentials or query")
        if (
            not self.id
            or not re.fullmatch(r"FORGE_PROVIDER_KEY_[A-Z0-9_]+", self.credential_ref)
            or type(self.max_retries) is not int
            or not 0 <= self.max_retries <= 3
            or type(self.concurrency) is not int
            or not 1 <= self.concurrency <= 64
        ):
            raise ValueError("Invalid provider configuration")
        if (
            type(self.supports_tools) is not bool
            or type(self.supports_json_schema) is not bool
            or type(self.allow_insecure_loopback) is not bool
        ):
            raise ValueError("Provider capability flags must be boolean")
        if (
            any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in (self.timeout_seconds, self.min_interval_seconds)
            )
            or not 0 < self.timeout_seconds <= 600
            or not 0 <= self.min_interval_seconds <= 60
        ):
            raise ValueError("Invalid provider timing configuration")
        for price in (
            self.input_price_per_million,
            self.cached_input_price_per_million,
            self.output_price_per_million,
        ):
            if price is not None and (
                type(price) not in (int, float) or not 0 <= price < float("inf")
            ):
                raise ValueError("Invalid provider pricing")
        if any(
            price is not None
            for price in (
                self.input_price_per_million,
                self.cached_input_price_per_million,
                self.output_price_per_million,
            )
        ) and (not isinstance(self.currency, str) or not self.currency.strip()):
            raise ValueError("Provider pricing requires a currency")

    @classmethod
    def from_env(cls):
        """One explicit provider; credentials resolved later from the named process variable."""
        names = (
            "FORGE_PROVIDER_ID",
            "FORGE_PROVIDER_KIND",
            "FORGE_PROVIDER_BASE_URL",
            "FORGE_PROVIDER_CREDENTIAL_REF",
        )
        values = [os.environ.get(name) for name in names]
        if not all(values):
            raise ValueError("Provider configuration is incomplete")

        def flag(name):
            value = os.environ.get(name, "false").lower()
            if value not in {"true", "false"}:
                raise ValueError(f"{name} must be true or false")
            return value == "true"

        return cls(
            *values,
            timeout_seconds=float(os.environ.get("FORGE_PROVIDER_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.environ.get("FORGE_PROVIDER_MAX_RETRIES", "1")),
            supports_tools=flag("FORGE_PROVIDER_SUPPORTS_TOOLS"),
            supports_json_schema=flag("FORGE_PROVIDER_SUPPORTS_JSON_SCHEMA"),
            allow_insecure_loopback=flag("FORGE_PROVIDER_ALLOW_INSECURE_LOOPBACK"),
            reasoning_effort=os.environ.get("FORGE_PROVIDER_REASONING_EFFORT"),
        )


def environment_credential(ref):
    return os.environ.get(ref)
