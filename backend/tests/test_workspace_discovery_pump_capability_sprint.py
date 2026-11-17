"""Programmatic Core pump regression; no Provider calls or task-quality claims."""

import asyncio
import importlib
import json
import secrets

from forge_core.domain import ConversationMessage, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.superagent_delegation import issue_mandate
from forge_core.workspace_discovery import WorkspaceDiscovery


def test_worker_pump_rechecks_prepared_automatic_identity_before_scheduling(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_CORE_SECRET", secrets.token_hex(32))
    monkeypatch.setenv("FORGE_WORKSPACE_ROOT", str(tmp_path))
    for key in (
        "FORGE_PROVIDER_ID",
        "FORGE_PROVIDER_KIND",
        "FORGE_PROVIDER_BASE_URL",
        "FORGE_PROVIDER_CREDENTIAL_REF",
        "FORGE_PROVIDERS_JSON",
        "FORGE_PRICE_BOUNDS_JSON",
        "FORGE_ROUTER_MODEL_ID",
        "FORGE_ROUTER_CONTEXT_LIMIT",
    ):
        monkeypatch.delenv(key, raising=False)
    from forge_api import main

    importlib.reload(main)
    monkeypatch.setattr(
        main.ProviderPool, "from_environment", lambda **_kwargs: (ProviderPool({}), {})
    )
    store = main.store
    discovery = WorkspaceDiscovery(store)
    roots = [tmp_path / "project_alpha", tmp_path / "project_beta"]
    for root in roots:
        root.mkdir()
        (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")

    def task(index, *, automatic=True, state="active", shape="unique", linked=True):
        content = (
            f"Inspect `{roots[0]}` and report issues."
            if shape == "unique"
            else f"Compare `{roots[0]}` with `{roots[1]}`."
            if shape == "multiple"
            else "Inspect the selected project."
        )
        quest = Quest(id=f"quest_pump_{index:02d}", title="Inspect project", goal="Mutable summary")
        source = ConversationMessage(role="user", content=content)
        with store.unit_of_work() as work:
            work.add_message(source)
            work.save_quest(quest)
            work.connection.execute("UPDATE quests SET status='running' WHERE id=?", (quest.id,))
            if automatic:
                mandate = issue_mandate(work, quest.id, source.id)
                work.connection.execute(
                    "UPDATE delegation_mandates SET setup_complete=1,state=? WHERE id=?",
                    (state, mandate),
                )
        if linked:
            discovery.associate(quest.id, roots[1])
        return quest.id

    # More than one batch verifies that long-running automatic tasks cannot
    # permanently occupy the first sixteen discovery slots.
    automatic = [task(index) for index in range(17)]
    manual = task(17, automatic=False)
    revoked = task(18, state="revoked")
    ambiguous = task(19, shape="multiple")
    unnamed = task(20, shape="none")
    unlinked_manual = task(21, automatic=False, linked=False)
    expected_calls = {*automatic, ambiguous, unnamed, unlinked_manual}
    authorization_tables = (
        "delegation_mandates",
        "delegation_contracts",
        "task_grants",
        "approvals",
        "messages",
    )

    def authorization_snapshot():
        with store.connection() as connection:
            return {
                table: [
                    tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")
                ]
                for table in authorization_tables
            }

    before = authorization_snapshot()
    calls, scheduled = [], []
    original_associate = main.WorkspaceDiscovery.associate_request

    def observed_associate(self, quest_id):
        calls.append(quest_id)
        return original_associate(self, quest_id)

    async def scheduler_tick(_scheduler):
        # The same pump must apply identity correction before invoking execution.
        scheduled.append({quest_id: discovery.for_quest(quest_id) for quest_id in calls})

    monkeypatch.setattr(main.WorkspaceDiscovery, "associate_request", observed_associate)
    monkeypatch.setattr(main.WorkerScheduler, "tick", scheduler_tick)

    async def run_core():
        async with main._core_lifespan(main.app):
            for _ in range(160):
                if expected_calls <= set(calls) and scheduled:
                    break
                await asyncio.sleep(0.025)

    asyncio.run(run_core())
    assert expected_calls <= set(calls)
    assert manual not in calls and revoked not in calls
    assert scheduled
    for quest_id in automatic:
        assert discovery.for_quest(quest_id)["root"] == str(roots[0])
        assert all(
            snapshot[quest_id]["root"] == str(roots[0])
            for snapshot in scheduled
            if quest_id in snapshot
        )
    for quest_id in (manual, revoked, ambiguous, unnamed):
        assert discovery.for_quest(quest_id)["root"] == str(roots[1])
    assert discovery.for_quest(unlinked_manual)["root"] == str(store.workspace.root)
    assert authorization_snapshot() == before
    assert store.list_workers() == []
    assert all(task.done() for task in main.app.state.background_tasks)
    events = [
        row for row in store.list_events() if row["event_type"] == "workspace.task_associated"
    ]
    for quest_id in automatic:
        changes = [row for row in events if row["quest_id"] == quest_id]
        assert len(changes) == 2
        payload = json.loads(changes[-1]["payload"])
        assert payload["project_id"] == discovery.for_quest(quest_id)["id"]
