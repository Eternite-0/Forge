"""Real SQLite tests for durable, per-consumer event delivery."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from forge_core.dispatcher import Dispatcher, LeaseError
from forge_core.domain import ForgeEvent, Quest, WorkerSession
from forge_core.settings import Workspace
from forge_core.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "dispatcher.sqlite3", workspace=Workspace.from_path(tmp_path))


def test_replay_is_per_consumer_and_fenced(store):
    quest = Quest(title="q", goal="g")
    store.save_quest(quest)
    worker = WorkerSession(quest_id=quest.id, role="test")
    store.save_worker(worker)
    first = ForgeEvent(
        event_type="worker.result",
        quest_id=quest.id,
        worker_id=worker.id,
        payload={"status": "ready"},
    )
    store.append_event(first)
    now = [100.0]
    d = Dispatcher(store, clock=lambda: now[0], lease_seconds=5)
    d.register("router")
    lease = d.claim("router")
    assert lease.event_id == first.id
    now[0] += 6
    renewed = d.claim("router")
    assert renewed.token != lease.token
    with pytest.raises(LeaseError):
        d.deliver(lease)
    d.deliver(renewed)
    assert d.inbox("router")[0]["event_id"] == first.id
    d.register("another-router")
    assert d.claim("another-router").event_id == first.id
    assert store.list_messages() == []


def test_transaction_rollback_retries_and_deduplicates(store):
    event = ForgeEvent(event_type="quest.updated", payload={"revision": 1})
    store.append_event(event)
    d = Dispatcher(store, clock=lambda: 100.0, lease_seconds=10)
    d.register("router")
    lease = d.claim("router")

    def fail(connection, item):
        connection.execute("CREATE TABLE temporary_effect(value TEXT)")
        connection.execute("INSERT INTO temporary_effect VALUES ('x')")
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        d.deliver(lease, handler=fail)
    assert d.inbox("router") == []
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='temporary_effect'"
            ).fetchone()
            is None
        )
    d.deliver(lease)
    assert len(d.inbox("router")) == 1
    with pytest.raises(LeaseError):
        d.deliver(lease)


def test_retry_dead_letter_and_ordered_cursor(store):
    first, second = ForgeEvent(event_type="a"), ForgeEvent(event_type="b")
    store.append_event(first)
    store.append_event(second)
    now = [0.0]
    d = Dispatcher(store, clock=lambda: now[0], max_attempts=2, base_backoff_seconds=5)
    d.register("router")
    lease = d.claim("router")
    d.fail(lease, "transient")
    assert d.claim("router") is None
    now[0] += 5
    retry = d.claim("router")
    assert retry.event_id == first.id and retry.attempt == 2
    d.fail(retry, "transient")
    assert d.dead_letters("router")[0]["event_id"] == first.id
    assert d.claim("router").event_id == second.id


def test_concurrent_claim_one_winner(store):
    store.append_event(ForgeEvent(event_type="test"))
    dispatcher = Dispatcher(store, clock=lambda: 100.0)
    dispatcher.register("router")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: dispatcher.claim("router"), range(2)))
    assert sum(claim is not None for claim in claims) == 1


def test_worker_result_not_sent_to_workers(store):
    quest = Quest(title="q", goal="g")
    store.save_quest(quest)
    worker = WorkerSession(quest_id=quest.id, role="test")
    store.save_worker(worker)
    store.append_event(
        ForgeEvent(
            event_type="worker.result",
            quest_id=quest.id,
            worker_id=worker.id,
            payload={"result": "done"},
        )
    )
    d = Dispatcher(store)
    d.register("router", kind="router")
    d.register(f"worker:{worker.id}", kind="worker", worker_id=worker.id)
    assert d.claim(f"worker:{worker.id}") is None
    assert d.claim("router") is not None


def test_worker_association_forgery_denied(store):
    quest = Quest(title="q", goal="g")
    store.save_quest(quest)
    store.append_event(
        ForgeEvent(
            event_type="worker.result",
            quest_id=quest.id,
            worker_id="missing",
            payload={"result": "done"},
        )
    )
    d = Dispatcher(store)
    d.register("router")
    lease = d.claim("router")
    with pytest.raises(ValueError):
        d.deliver(lease)
    assert d.inbox("router") == []


def test_repeated_expired_crashes_reach_dead_letter(store):
    store.append_event(ForgeEvent(event_type="test"))
    now = [1.0]
    d = Dispatcher(store, clock=lambda: now[0], lease_seconds=2, max_attempts=2)
    d.register("router")
    assert d.claim("router").attempt == 1
    now[0] += 3
    assert d.claim("router").attempt == 2
    now[0] += 3
    assert d.claim("router") is None
    assert d.dead_letters("router")[0]["last_error"] == "lease_expired"


def test_critical_preempts_backoff_without_skipping_cursor(store):
    first = ForgeEvent(event_type="worker.proposed")
    critical = ForgeEvent(event_type="approval.requested")
    store.append_event(first)
    now = [100.0]
    d = Dispatcher(store, clock=lambda: now[0], base_backoff_seconds=5)
    d.register("router")
    original = d.claim("router")
    d.fail(original, "retry")
    store.append_event(critical)
    urgent = d.claim("router")
    assert urgent.event_id == critical.id
    d.deliver(urgent)
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT cursor_seq FROM event_consumers WHERE id='router'"
            ).fetchone()[0]
            == 0
        )
    now[0] += 5
    d.deliver(d.claim("router"))
    with store.connection() as connection:
        cursor = connection.execute(
            "SELECT cursor_seq FROM event_consumers WHERE id='router'"
        ).fetchone()[0]
        assert cursor == urgent.seq


def test_tick_routes_planner_events_to_independent_consumer(store):
    proposed = ForgeEvent(event_type="quest.proposed", payload={"objective": "design"})
    ordinary = ForgeEvent(event_type="ordinary", payload={"info": "x"})
    store.append_event(proposed)
    store.append_event(ordinary)
    d = Dispatcher(store)
    for _ in range(3):
        d.tick()
    assert [item["event_id"] for item in d.inbox("planner")] == [proposed.id]
    assert {item["event_id"] for item in d.inbox("router")} == {proposed.id, ordinary.id}


def test_tick_routes_composer_only_approved_plan_events(store):
    approved = ForgeEvent(event_type="quest.plan_approved", payload={"plan_id": "p"})
    ordinary = ForgeEvent(event_type="ordinary")
    store.append_event(approved)
    store.append_event(ordinary)
    d = Dispatcher(store)
    for _ in range(3):
        d.tick()
    assert [item["event_id"] for item in d.inbox("composer")] == [approved.id]


def test_dead_letter_replay_is_audited_and_fenced(store):
    event = ForgeEvent(event_type="test")
    store.append_event(event)
    d = Dispatcher(store, max_attempts=1, clock=lambda: 10.0)
    d.register("router")
    lease = d.claim("router")
    d.fail(lease, "failure")
    assert d.dead_letters("router")[0]["event_id"] == event.id
    d.replay_dead_letter("router", event.id, actor="local_user", reason_code="retry")
    retry = d.claim("router")
    assert retry.event_id == event.id and retry.token != lease.token and retry.attempt == 1
    with pytest.raises(LeaseError):
        d.deliver(lease)
    d.deliver(retry)
    with store.connection() as connection:
        audit = connection.execute("SELECT * FROM event_delivery_replays").fetchall()
        assert len(audit) == 1 and audit[0]["actor"] == "local_user"
        assert audit[0]["previous_error"] == "failure"


def test_incremental_materialization_and_priority_across_batch(store):
    with store.unit_of_work() as work:
        for index in range(205):
            work.append_event(ForgeEvent(event_type="worker.proposed", payload={"i": index}))
        critical = ForgeEvent(event_type="approval.requested")
        work.append_event(critical)
    d = Dispatcher(store)
    d.register("router")
    first = d.claim("router")
    assert first.event_id == critical.id
    d.deliver(first)
    with store.connection() as connection:
        consumer = connection.execute(
            "SELECT cursor_seq,scan_seq FROM event_consumers WHERE id='router'"
        ).fetchone()
        count = connection.execute(
            "SELECT count(*) FROM event_deliveries WHERE consumer_id='router'"
        ).fetchone()[0]
    assert consumer["cursor_seq"] == 0
    assert consumer["scan_seq"] == 100
    assert count == 101


def test_handler_lease_expires_before_commit_rolls_back(store):
    store.append_event(ForgeEvent(event_type="test"))
    now = [100.0]
    d = Dispatcher(store, clock=lambda: now[0], lease_seconds=2)
    d.register("router")
    lease = d.claim("router")

    def expire(connection, item):
        now[0] += 3

    with pytest.raises(LeaseError):
        d.deliver(lease, handler=expire)
    assert d.inbox("router") == []


def test_unassigned_dag_node_cannot_be_forged_as_worker_result(store):
    quest = Quest(title="q", goal="g")
    store.save_quest(quest)
    worker = WorkerSession(quest_id=quest.id, role="test")
    store.save_worker(worker)
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO quest_plans(id,quest_id,revision,digest,status,graph_json,created_at) VALUES (?,?,?,?,?,?,?)",
            ("p", quest.id, 1, "digest", "proposed", "{}", "now"),
        )
        work.connection.execute(
            "INSERT INTO task_nodes(plan_id,node_key,objective,criteria_json,budget_tokens,required_capabilities,max_attempts) VALUES (?,?,?,?,?,?,?)",
            ("p", "n", "objective", "[]", 10, "[]", 1),
        )
    store.append_event(
        ForgeEvent(
            event_type="worker.result",
            quest_id=quest.id,
            worker_id=worker.id,
            payload={"plan_id": "p", "task_node_key": "n", "result": "done"},
        )
    )
    d = Dispatcher(store)
    d.register("router")
    with pytest.raises(ValueError, match="association"):
        d.deliver(d.claim("router"))
    assert d.inbox("router") == []


def test_recovery_after_claiming_process_exits(store):
    import subprocess
    import sys

    event = ForgeEvent(event_type="test")
    store.append_event(event)
    dispatcher = Dispatcher(store, clock=lambda: 10.0, lease_seconds=2)
    dispatcher.register("router")
    code = """import sys
from forge_core.dispatcher import Dispatcher
from forge_core.settings import Workspace
from forge_core.store import Store
store = Store(sys.argv[1], workspace=Workspace.from_path(sys.argv[2]))
lease = Dispatcher(store, clock=lambda: 10.0, lease_seconds=2).claim('router')
assert lease is not None
"""
    child = subprocess.run(
        [sys.executable, "-c", code, store.path, str(store.workspace.root)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert child.returncode == 0
    restarted = Dispatcher(store, clock=lambda: 13.0, lease_seconds=2)
    lease = restarted.claim("router")
    assert lease.event_id == event.id and lease.attempt == 2
    restarted.deliver(lease)
    assert len(restarted.inbox("router")) == 1


def test_help_requested_is_urgent_and_keeps_cursor_gap(store):
    quest = Quest(title="q", goal="g")
    store.save_quest(quest)
    worker = WorkerSession(quest_id=quest.id, role="test")
    store.save_worker(worker)
    with store.unit_of_work() as work:
        for index in range(105):
            work.append_event(ForgeEvent(event_type="ordinary", payload={"index": index}))
        urgent = ForgeEvent(
            event_type="worker.help_requested",
            quest_id=quest.id,
            worker_id=worker.id,
            payload={"reason": "blocked"},
        )
        work.append_event(urgent)
    d = Dispatcher(store)
    d.register("router")
    lease = d.claim("router")
    assert lease.event_id == urgent.id
    d.deliver(lease)
    with store.connection() as connection:
        assert (
            connection.execute(
                "SELECT cursor_seq FROM event_consumers WHERE id='router'"
            ).fetchone()[0]
            == 0
        )
