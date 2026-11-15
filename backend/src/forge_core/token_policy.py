"""Trusted operator policy for cumulative token limits, separate from context size."""

import os


def token_budgets_enforced():
    value = os.environ.get("FORGE_ENFORCE_TOKEN_BUDGETS", "false").lower()
    if value not in {"true", "false"}:
        raise ValueError("FORGE_ENFORCE_TOKEN_BUDGETS must be true or false")
    return value == "true"
