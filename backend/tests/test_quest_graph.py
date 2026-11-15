import pytest

from forge_core.domain import DomainError, Quest
from forge_core.quest_graph import QuestGraphService
from forge_core.store import Store


def graph():
    return {
        "nodes": [
            {"key": "a", "objective": "A", "success_criteria": ["A done"], "budget_tokens": 10},
            {"key": "b", "objective": "B", "success_criteria": ["B done"], "budget_tokens": 10},
        ],
        "edges": [{"source": "a", "target": "b", "condition": "success"}],
    }


def setup(tmp_path):
    store = Store(tmp_path / "graph.sqlite3")
    quest = Quest(title="Graph", goal="Goal")
    store.save_quest(quest)
    return store, quest, QuestGraphService(store)


def test_dag_create_approve_and_history(tmp_path):
    store, quest, service = setup(tmp_path)
    proposal = service.propose(quest.id, graph(), expected_version=0)
    assert len(proposal["nodes"]) == 2
    approved = service.approve(
        quest.id,
        proposal["digest"],
        proposal["plan_revision"],
        expected_version=1,
        actor="local_user",
    )
    assert approved["status"] == "approved"
    assert service.get(quest.id)["digest"] == proposal["digest"]
    assert len(store.list_events()) == len(store.list_outbox()) == 2


@pytest.mark.parametrize(
    "invalid",
    [
        {"nodes": [], "edges": []},
        {
            "nodes": [
                {"key": "a", "objective": "A", "success_criteria": ["ok"], "budget_tokens": 1}
            ],
            "edges": [{"source": "a", "target": "a", "condition": "success"}],
        },
        {
            "nodes": [
                {"key": "a", "objective": "A", "success_criteria": ["ok"], "budget_tokens": 1}
            ],
            "edges": [{"source": "a", "target": "missing", "condition": "success"}],
        },
    ],
)
def test_invalid_graph_is_atomic(tmp_path, invalid):
    store, quest, service = setup(tmp_path)
    with pytest.raises(DomainError):
        service.propose(quest.id, invalid, expected_version=0)
    assert store.get_quest(quest.id)["version"] == 0
    assert store.list_events() == []


def test_plan_change_invalidates_approval_and_stale_cas(tmp_path):
    store, quest, service = setup(tmp_path)
    first = service.propose(quest.id, graph(), expected_version=0)
    with pytest.raises(DomainError):
        service.approve(
            quest.id,
            first["digest"],
            first["plan_revision"],
            expected_version=0,
            actor="local_user",
        )
    changed = graph()
    changed["nodes"][0]["objective"] = "changed"
    second = service.propose(quest.id, changed, expected_version=1)
    with pytest.raises(DomainError):
        service.approve(
            quest.id,
            first["digest"],
            first["plan_revision"],
            expected_version=2,
            actor="local_user",
        )
    assert second["plan_revision"] > first["plan_revision"]
    assert store.get_quest(quest.id)["status"] == "awaiting_approval"


@pytest.mark.parametrize("table", ["task_nodes", "task_edges", "events", "outbox"])
def test_sql_failure_rolls_back_plan_and_quest(tmp_path, table):
    import sqlite3

    store, quest, service = setup(tmp_path)
    with store.connection() as connection:
        connection.execute(
            f"CREATE TRIGGER graph_abort BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'real failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.propose(quest.id, graph(), expected_version=0)
    assert store.get_quest(quest.id)["version"] == 0
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM quest_plans").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM task_nodes").fetchone()[0] == 0
    assert store.list_events() == []
    assert store.list_outbox() == []


def test_real_approve_race_has_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, quest, service = setup(tmp_path)
    plan = service.propose(quest.id, graph(), expected_version=0)
    barrier = Barrier(2)

    def compete(_):
        barrier.wait(timeout=5)
        try:
            QuestGraphService(store).approve(
                quest.id,
                plan["digest"],
                plan["plan_revision"],
                expected_version=1,
                actor="local_user",
            )
            return "committed"
        except DomainError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(compete, range(2))) == ["committed", "conflict"]
    assert len(store.list_events()) == len(store.list_outbox()) == 2


@pytest.mark.parametrize("table", ["quest_plans", "events", "outbox"])
def test_approve_sql_failure_rolls_back_version_and_decision(tmp_path, table):
    import sqlite3

    store, quest, service = setup(tmp_path)
    plan = service.propose(quest.id, graph(), expected_version=0)
    operation = "UPDATE" if table == "quest_plans" else "INSERT"
    with store.connection() as connection:
        connection.execute(
            f"CREATE TRIGGER approval_abort BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'approve failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.approve(
            quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
        )
    assert service.get(quest.id)["status"] == "proposed"
    assert store.get_quest(quest.id)["version"] == 1
    assert len(store.list_events()) == len(store.list_outbox()) == 1


def test_failure_condition_edge_is_ready_after_failed_predecessor(tmp_path):
    store, quest, service = setup(tmp_path)
    proposal = graph()
    proposal["edges"][0]["condition"] = "failure"
    plan = service.propose(quest.id, proposal, expected_version=0)
    service.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE task_nodes SET status='failed' WHERE plan_id=? AND node_key='a'", (plan["id"],)
        )
    assert service.readiness(quest.id)["b"] == "ready"


def test_v5_to_v6_component_failure_rolls_back_and_preserves_state(tmp_path):
    import sqlite3
    from contextlib import closing

    from forge_core.migrations import MIGRATIONS, migrate

    path = tmp_path / "old.sqlite3"
    with closing(sqlite3.connect(path, isolation_level=None)) as connection:
        migrate(connection, target_version=5)
        connection.execute("INSERT INTO conversations VALUES ('main','then')")
        connection.execute(
            "INSERT INTO router_state(conversation_id,router_session_id) VALUES ('main','router-old')"
        )

        def fail_after_graph(conn):
            MIGRATIONS[6](conn)
            conn.execute("INSERT INTO nonexistent_table VALUES (1)")

        steps = dict(MIGRATIONS)
        steps[6] = fail_after_graph
        with pytest.raises(sqlite3.OperationalError):
            migrate(connection, migrations=steps)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 5
        assert (
            connection.execute("SELECT router_session_id FROM router_state").fetchone()[0]
            == "router-old"
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='quest_plans'"
            ).fetchone()[0]
            == 0
        )
        migrate(connection, target_version=6)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 6
        assert (
            connection.execute("SELECT router_session_id FROM router_state").fetchone()[0]
            == "router-old"
        )
        migrate(connection, target_version=7)
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 7


def test_retry_concurrency_preserves_failed_quest_and_creates_one_successor(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, quest, service = setup(tmp_path)
    plan = service.propose(quest.id, graph(), expected_version=0)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='failed' WHERE id=?", (quest.id,))
        work.connection.execute("UPDATE quest_plans SET status='failed' WHERE id=?", (plan["id"],))
    barrier = Barrier(2)

    def retry(_):
        barrier.wait(timeout=5)
        try:
            return QuestGraphService(store).control(
                quest.id, "retry", expected_version=1, actor="local_user"
            )["quest_id"]
        except DomainError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(retry, range(2)))
    assert len([item for item in results if item]) == 1
    assert len(store.list_quests()) == 2
    assert store.get_quest(quest.id)["status"] == "failed"
    assert store.get_quest(quest.id)["version"] == 2


def test_retry_outbox_failure_rolls_back_old_and_new_quests(tmp_path):
    import sqlite3

    store, quest, service = setup(tmp_path)
    plan = service.propose(quest.id, graph(), expected_version=0)
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE quests SET status='failed' WHERE id=?", (quest.id,))
        work.connection.execute("UPDATE quest_plans SET status='failed' WHERE id=?", (plan["id"],))
        work.connection.execute(
            "CREATE TRIGGER abort_retry BEFORE INSERT ON outbox BEGIN SELECT RAISE(ABORT, 'outbox failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.control(quest.id, "retry", expected_version=1, actor="local_user")
    assert store.get_quest(quest.id)["version"] == 1
    assert store.get_quest(quest.id)["status"] == "failed"
    assert len(store.list_quests()) == 1


def test_planner_small_context_fails_before_provider_call(tmp_path):
    import asyncio

    store, quest, service = setup(tmp_path)

    class Provider:
        calls = 0

        async def generate(self, request):
            self.calls += 1
            raise AssertionError("provider must not be called")

    provider = Provider()
    with pytest.raises(DomainError, match="budget"):
        asyncio.run(
            service.generate(
                quest.id,
                expected_version=0,
                provider=provider,
                model_id="mock",
                provider_id="mock",
                capabilities={"json_schema": True},
                capability_sources={"json_schema": "mock"},
                context_limit=1000,
                output_limit=512,
            )
        )
    assert provider.calls == 0
    assert store.get_quest(quest.id)["version"] == 0
    assert store.list_events() == []
