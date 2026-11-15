import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def explicit_legacy_token_budget_mode(monkeypatch):
    """Existing budget regressions exercise the opt-in enforced policy explicitly.

    Tests for the normal unbounded policy override this value in their own body.
    Child-process legacy tests inherit the same explicit operator setting.
    """
    monkeypatch.setenv("FORGE_ENFORCE_TOKEN_BUDGETS", "true")


class RouterMockProvider:
    """Explicit deterministic test-only provider, never production fallback."""

    execution_mode = "mock"

    def __init__(self, intent):
        self.intent = intent

    async def generate(self, request):
        decision = {
            "intent": self.intent,
            "reply": "测试回复",
            "title": "测试目标" if self.intent == "create_quest" else None,
            "goal": "测试目标" if self.intent == "create_quest" else None,
            "quest_id": None,
            "candidates": [],
        }
        return type(
            "MockResponse", (), {"text": json.dumps(decision), "tool_calls": [], "usage": {}}
        )()


@pytest.fixture
def legacy_database(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    fixture = Path(__file__).parent / "fixtures" / "legacy_v1.sql"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(fixture.read_text(encoding="utf-8"))
    return path
