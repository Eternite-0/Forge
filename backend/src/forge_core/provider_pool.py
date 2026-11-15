"""Explicit provider selection; no implicit fallback or agent backend."""

import json
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PriceBound:
    """Operator-supplied worst-case microunits per million tokens, not an estimate."""

    provider_id: str
    model_id: str
    currency: str
    input_microunits_per_million: int
    output_microunits_per_million: int
    version: str
    covers_all_input_cache_and_fees: bool
    covers_all_output_and_fees: bool
    source: str = "operator"

    def __post_init__(self):
        if (
            not self.provider_id
            or not self.model_id
            or not isinstance(self.currency, str)
            or not re.fullmatch(r"[A-Z]{3}", self.currency)
            or not self.version
            or self.source != "operator"
            or self.covers_all_input_cache_and_fees is not True
            or self.covers_all_output_and_fees is not True
            or any(
                type(x) is not int or x < 0
                for x in (self.input_microunits_per_million, self.output_microunits_per_million)
            )
        ):
            raise ValueError("Invalid operator price bound")


class ProviderPool:
    def __init__(self, providers):
        self._providers = dict(providers)

    def get(self, provider_id):
        provider = self._providers.get(provider_id)
        if provider is None or getattr(provider, "execution_mode", None) not in {"mock", "live"}:
            raise ValueError("Provider is unavailable or lacks provenance")
        return provider

    def with_provider(self, provider_id, provider):
        if provider_id in self._providers and self._providers[provider_id] is not provider:
            raise ValueError("Duplicate provider identity")
        return ProviderPool({**self._providers, provider_id: provider})

    @classmethod
    def from_environment(cls, *, observer=None, budget_ledger=None):
        """Read explicit non-secret provider definitions and operator price ceilings."""
        from .providers import ProviderConfig, ProviderGateway

        try:
            configs = json.loads(os.environ.get("FORGE_PROVIDERS_JSON", "[]"))
            prices = json.loads(os.environ.get("FORGE_PRICE_BOUNDS_JSON", "[]"))
            if (
                not isinstance(configs, list)
                or len(configs) > 16
                or not isinstance(prices, list)
                or len(prices) > 128
            ):
                raise ValueError
            providers = {}
            for item in configs:
                config = ProviderConfig(**item)
                if config.id in providers:
                    raise ValueError
                providers[config.id] = ProviderGateway(
                    config, observer=observer, budget_ledger=budget_ledger
                )
            bounds = {}
            for item in prices:
                bound = PriceBound(**item)
                key = (bound.provider_id, bound.model_id)
                if key in bounds:
                    raise ValueError
                bounds[key] = bound
            return cls(providers), bounds
        except (ValueError, TypeError, KeyError):
            raise ValueError("Invalid provider pool configuration") from None
