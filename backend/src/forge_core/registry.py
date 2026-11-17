import hashlib
import json
from datetime import UTC, datetime

from .domain import RoleTemplate

DEFAULT_ROLES = [
    RoleTemplate(
        id="research",
        name="Research",
        description="Research and synthesis",
        required_capabilities={"research": 75, "writing": 55},
    ),
    RoleTemplate(
        id="math",
        name="Math",
        description="Formal reasoning and quantitative analysis",
        required_capabilities={"mathematics": 80, "planning": 75},
    ),
    RoleTemplate(
        id="coding",
        name="Coding",
        description="Implement and debug software",
        required_capabilities={"coding": 80, "tool_calling": 65},
    ),
    RoleTemplate(
        id="design",
        name="Design",
        description="Visual and interaction design",
        required_capabilities={"design": 75, "vision": 60},
    ),
    RoleTemplate(
        id="review",
        name="Reviewer",
        description="Independent verification",
        required_capabilities={"planning": 75, "instruction_following": 75},
    ),
]


class ModelRegistry:
    def __init__(self, store):
        self.store = store

    def upsert(self, profile):
        self.store.save_model(profile)
        return profile

    def list(self):
        return self.store.list_models()

    async def discover(self, gateway):
        from .domain import ModelProfile, ProtocolSupport

        mode = getattr(gateway, "execution_mode", None)
        if mode not in {"mock", "live"}:
            raise ValueError("Discovery requires gateway provenance")
        discovered = await gateway.discover()
        existing = {model.id: model for model in self.list()}
        added = []
        for item in discovered:
            provider = item.get("provider_id")
            model_id = item.get("id")
            if (
                not isinstance(provider, str)
                or not provider
                or not isinstance(model_id, str)
                or not model_id
            ):
                raise ValueError("Invalid discovered model identity")
            identity = json.dumps([provider, model_id], ensure_ascii=False, separators=(",", ":"))
            key = "discovered_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
            if key not in existing:
                declared = {}
                for name in ("context_window", "max_output_tokens"):
                    if type(item.get(name)) is int and item[name] > 0:
                        declared[name] = item[name]
                for field, name in (
                    ("supports_tool_calls", "tool_calling_support"),
                    ("supports_structured_output", "structured_output_support"),
                ):
                    if type(item.get(field)) is bool:
                        declared[name] = ProtocolSupport(
                            supported=item[field], source="provider_declared"
                        )
                provenance = {"discovery": {"source": "provider", "mode": mode}}
                if declared:
                    provenance["provider_declared"] = {
                        "source": "provider_declared",
                        "observed_at": datetime.now(UTC).isoformat(),
                        "scope": "Provider-advertised limits/protocols; not benchmark measurements",
                        "fields": item.get("declaration_provenance", {}).get("fields", {}),
                    }
                profile = ModelProfile(
                    id=key,
                    provider=provider,
                    model_id=model_id,
                    display_name=model_id,
                    provenance=provenance,
                    **declared,
                )
                self.upsert(profile)
                existing[key] = profile
                added.append(profile)
        return added

    def candidates(self, required):
        ranked = []
        for model in self.list():
            if not model.enabled or model.quarantine_reason:
                continue
            if not required:
                fit = 50.0
            else:
                values = {k: model.capability_measurements.get(k) for k in required}
                if any(
                    values[k] is None or values[k].value < threshold
                    for k, threshold in required.items()
                ):
                    continue
                fit = sum(
                    values[k].value * min(v, 100.0) / 100.0 for k, v in required.items()
                ) / len(required)
            ranked.append((model, round(fit, 2)))
        return sorted(ranked, key=lambda item: item[1], reverse=True)


class TeamComposer:
    """Explainable baseline. Research-grade complementarity optimization is a later milestone."""

    def __init__(self, registry):
        self.registry = registry

    def compose(self, roles):
        proposals = []
        for role in roles:
            candidates = self.registry.candidates(role.required_capabilities)
            proposals.append(
                {
                    "role_id": role.id,
                    "role_name": role.name,
                    "required_capabilities": role.required_capabilities,
                    "model_candidates": [
                        {"model_id": model.id, "provider": model.provider, "fit_score": score}
                        for model, score in candidates[:3]
                    ],
                    "status": "proposed",
                }
            )
        return proposals
