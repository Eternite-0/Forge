"""Real persistence and deterministic routing; these do not claim model quality."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_worker_runtime import MockApprovedTeam, MockToolEngine, ScriptedMock

from forge_core.dispatcher import Dispatcher
from forge_core.domain import DomainError, Quest
from forge_core.provider_pool import ProviderPool
from forge_core.quest_graph import QuestGraphService
from forge_core.settings import Workspace
from forge_core.store import Store
from forge_core.superagent_events import TriggerService, dispatch_worker_trigger_tool
from forge_core.superagent_sources import LocalTriggerSources
from forge_core.worker_runtime import WorkerManager


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "events.sqlite3", workspace=Workspace.from_path(tmp_path))


def worker(store):
    quest = Quest(title="Private task", goal="Read events and work")
    store.save_quest(quest)
    graphs = QuestGraphService(store)
    plan = graphs.propose(
        quest.id,
        {
            "nodes": [
                {
                    "key": "n",
                    "objective": "Work",
                    "success_criteria": ["verified"],
                    "budget_tokens": 4000,
                }
            ],
            "edges": [],
        },
        expected_version=0,
    )
    graphs.approve(
        quest.id, plan["digest"], plan["plan_revision"], expected_version=1, actor="local_user"
    )

    class UniqueApprovedTeam(MockApprovedTeam):
        def get_approved(self, quest_id, plan_revision, proposal_digest, *, connection):
            team = super().get_approved(quest_id, plan_revision, "team", connection=connection)
            team["proposal_digest"] = proposal_digest
            return team

    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=UniqueApprovedTeam(plan),
    )
    return manager.start(quest.id, plan["id"], "n", team_digest=quest.id, member_index=0)


def test_fast_filter_and_external_deduplication_are_durable(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message", mode="explicit", allowed_actors=["alice"])
    denied = service.receive("chat", "1", "topic", {"text": "hello"}, actor="bob", addressed=True)
    assert denied["status"] == "filtered" and denied["reason"] == "actor_not_allowed"
    assert service.pending() == []
    quiet = service.receive("chat", "2", "topic", {"text": "hello"}, actor="alice")
    assert quiet["reason"] == "not_addressed"
    accepted = service.receive(
        "chat", "3", "topic", {"text": "do work"}, actor="alice", addressed=True
    )
    reopened = TriggerService(Store(store.path, workspace=store.workspace))
    assert reopened.receive(
        "chat", "3", "topic", {"text": "do work"}, actor="alice", addressed=True
    )["duplicate"]
    assert [item["id"] for item in reopened.pending()] == [accepted["id"]]
    with pytest.raises(DomainError, match="conflicts"):
        reopened.receive("chat", "3", "topic", {"text": "changed"}, actor="alice", addressed=True)
    with store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM events WHERE event_type='trigger.received'").fetchone()[
                0
            ]
            == 3
        )
        assert c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 3


def test_many_to_many_pass_through_and_exact_large_payload_reads(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    first = service.receive("chat", "1", "topic", {"text": "task"})
    a, b = worker(store), worker(store)
    service.subscribe(first["thread_id"], a["id"])
    service.subscribe(first["thread_id"], b["id"])
    payload = {"text": "原始上下文😊" * 9000, "quoted": "untrusted; no grants"}
    second = service.receive("chat", "2", "topic", payload)
    assert second["status"] == "passed_through" and len(second["deliveries"]) == 2
    assert service.pending() == []
    body, offset = [], 0
    while True:
        page = service.read_trigger(
            second["id"], run_id=a["id"], offset_chars=offset, max_chars=2048
        )
        body.append(page["text"])
        if page["next_offset_chars"] is None:
            break
        offset = page["next_offset_chars"]
    assert json.loads("".join(body)) == payload
    with store.connection() as c:
        rows = c.execute(
            "SELECT payload_json FROM worker_inbox WHERE event_id=?", (second["event_id"],)
        ).fetchall()
        assert len(rows) == 2 and all(len(row[0].encode()) < 4096 for row in rows)
        assert all(json.loads(row[0])["authority"] == "context_only" for row in rows)
    service.receive("chat", "2", "topic", payload)
    service.subscribe(first["thread_id"], a["id"])
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM worker_inbox WHERE event_id=?", (second["event_id"],)
            ).fetchone()[0]
            == 2
        )


def test_concurrent_receive_has_one_fanout_and_one_outbox(store):
    service = TriggerService(store)
    service.register_source("external", kind="webhook")
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(
            pool.map(lambda _: service.receive("external", "same", "topic", {"value": 1}), range(2))
        )
    assert len({row["id"] for row in rows}) == 1
    assert sorted(row["duplicate"] for row in rows) == [False, True]
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM superagent_triggers").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1


def test_scope_checks_reject_cross_conversation_workspace_and_undelivered_reads(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "1", "topic", {"text": "private"})
    run = worker(store)
    with pytest.raises(DomainError, match="not received"):
        service.read_trigger(trigger["id"], run_id=run["id"])
    with pytest.raises(DomainError, match="workspace"):
        service.receive("chat", "2", "topic", {}, workspace_id="other")
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO conversations VALUES ('other','now')")
    service.register_source("other", kind="message", conversation_id="other")
    other = service.receive("other", "1", "topic", {}, conversation_id="other")
    with pytest.raises(DomainError, match="different scope"):
        service.subscribe(other["thread_id"], run["id"])
    with pytest.raises(DomainError, match="conversation"):
        service.receive("other", "2", "topic", {})
    with pytest.raises(DomainError, match="not registered"):
        service.receive("missing", "1", "topic", {})


def test_handback_waits_for_all_owners_and_does_not_redeliver(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "1", "topic", {"text": "initial"})
    a, b = worker(store), worker(store)
    service.subscribe(trigger["thread_id"], a["id"])
    service.subscribe(trigger["thread_id"], b["id"])
    later = service.receive("chat", "2", "topic", {"text": "outside scope"})
    assert service.handback(a["id"], later["id"], "different task")
    assert service.pending() == []
    assert service.handback(b["id"], later["id"], "different task")
    assert [item["id"] for item in service.pending()] == [later["id"]]
    assert not service.handback(b["id"], later["id"], "different task")
    Dispatcher(store).tick()
    assert [item["id"] for item in service.pending()] == [later["id"]]
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='trigger.handed_back'"
            ).fetchone()[0]
            == 1
        )


def test_dispatcher_retries_backpressure_without_routing_new_task(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "0", "topic", {"text": "initial"})
    run = worker(store)
    service.subscribe(trigger["thread_id"], run["id"])
    for number in range(1, 65):
        service.receive("chat", str(number), "topic", {"number": number})
    assert service.pending() == []
    with store.unit_of_work() as work:
        c = work.connection
        assert (
            c.execute(
                "SELECT COUNT(*) FROM worker_inbox WHERE run_id=? AND consumed=0", (run["id"],)
            ).fetchone()[0]
            == 64
        )
        c.execute("UPDATE worker_inbox SET consumed=1 WHERE run_id=?", (run["id"],))
    Dispatcher(store).tick()
    with store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM worker_inbox WHERE run_id=?", (run["id"],)).fetchone()[
                0
            ]
            == 65
        )


def test_worker_flush_recovers_deferred_subscription_without_dispatcher_and_rolls_back(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "0", "topic", {"text": "initial"})
    run = worker(store)
    service.subscribe(trigger["thread_id"], run["id"])
    for number in range(1, 65):
        deferred = service.receive("chat", str(number), "topic", {"number": number})
    assert deferred["deliveries"] == []
    assert service.flush_run(run["id"]) == {"delivered": 0, "pending": True}
    with pytest.raises(RuntimeError, match="rollback"), store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_inbox SET consumed=1 WHERE run_id=?", (run["id"],))
        assert service.flush_run(run["id"], connection=work.connection) == {
            "delivered": 1,
            "pending": False,
        }
        raise RuntimeError("rollback")
    assert service.read_trigger(deferred["id"])["deliveries"] == []
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_inbox SET consumed=1 WHERE run_id=?", (run["id"],))
        assert service.flush_run(run["id"], connection=work.connection) == {
            "delivered": 1,
            "pending": False,
        }
    assert service.flush_run(run["id"]) == {"delivered": 0, "pending": False}
    assert service.read_trigger(deferred["id"], run_id=run["id"])["deliveries"] == [
        {"run_id": run["id"], "handed_back": 0, "handback_reason": None}
    ]


def test_handback_waits_for_deferred_owner_and_records_each_owner_once(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "0", "topic", {})
    a, b = worker(store), worker(store)
    service.subscribe(trigger["thread_id"], a["id"])
    service.subscribe(trigger["thread_id"], b["id"])
    with store.unit_of_work() as work:
        for number in range(63):
            work.connection.execute(
                "INSERT INTO worker_inbox(run_id,event_id,payload_json) VALUES (?,?,?)",
                (b["id"], f"control-{number}", "{}"),
            )
    deferred = service.receive("chat", "1", "topic", {"text": "outside scope"})
    assert len(deferred["deliveries"]) == 1
    assert service.handback(a["id"], deferred["id"], "other owner should inspect")
    assert service.pending() == []
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_inbox SET consumed=1 WHERE run_id=?", (b["id"],))
        assert service.flush_run(b["id"], connection=work.connection)["delivered"] == 1
    assert service.handback(b["id"], deferred["id"], "separate task")
    assert not service.handback(b["id"], deferred["id"], "separate task")
    assert [row["id"] for row in service.pending()] == [deferred["id"]]
    events = [
        row for row in store.list_events() if row["event_type"] == "trigger.delivery_handed_back"
    ]
    assert len(events) == 2
    assert {json.loads(row["payload"])["run_id"] for row in events} == {a["id"], b["id"]}
    assert {(row["quest_id"], row["worker_id"]) for row in events} == {
        (a["quest_id"], a["worker_id"]),
        (b["quest_id"], b["worker_id"]),
    }


def test_pending_claim_is_fenced_after_restart_and_scope_bound(store):
    now = [100.0]
    service = TriggerService(store, clock=lambda: now[0])
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "1", "topic", {})
    first = service.claim_pending(lease_seconds=5)[0]
    assert service.claim_pending() == []
    now[0] += 6
    renewed = TriggerService(Store(store.path, workspace=store.workspace), clock=lambda: now[0])
    second = renewed.claim_pending()[0]
    with pytest.raises(DomainError, match="stale"):
        service.mark_routed(trigger["id"], lease_token=first["lease_token"])
    renewed.mark_routed(trigger["id"], ignored=True, lease_token=second["lease_token"])
    assert service.pending() == []


def test_pending_claim_never_mixes_conversations(store):
    service = TriggerService(store)
    service.register_source("main-source", kind="message")
    with store.unit_of_work() as work:
        work.connection.execute("INSERT INTO conversations VALUES ('other','now')")
    service.register_source("other-source", kind="message", conversation_id="other")
    main = service.receive("main-source", "1", "topic", {})
    other = service.receive("other-source", "1", "topic", {}, conversation_id="other")
    assert [row["id"] for row in service.pending()] == [main["id"]]
    assert [row["id"] for row in service.claim_pending()] == [main["id"]]
    assert [row["id"] for row in service.claim_pending(conversation_id="other")] == [other["id"]]


def test_integrity_failure_blocks_read_claim_and_subscription(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "1", "topic", {"text": "trusted source"})
    run = worker(store)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE superagent_triggers SET payload_json='{}' WHERE id=?", (trigger["id"],)
        )
    with pytest.raises(DomainError, match="integrity"):
        service.read_trigger(trigger["id"])
    with pytest.raises(DomainError, match="integrity"):
        service.claim_pending()
    with pytest.raises(DomainError, match="integrity"):
        service.subscribe(trigger["thread_id"], run["id"])
    with store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM worker_inbox WHERE run_id=?", (run["id"],)).fetchone()[
                0
            ]
            == 0
        )


def test_claim_invalidated_by_pass_through_cannot_overwrite_owner(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "1", "topic", {})
    claimed = service.claim_pending()[0]
    run = worker(store)
    service.subscribe(trigger["thread_id"], run["id"])
    with pytest.raises(DomainError, match="stale"):
        service.mark_routed(trigger["id"], ignored=True, lease_token=claimed["lease_token"])
    assert service.read_trigger(trigger["id"])["status"] == "passed_through"


def test_worker_tool_adapter_binds_run_and_handback(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    trigger = service.receive("chat", "1", "topic", {"text": "task"})
    run = worker(store)
    service.subscribe(trigger["thread_id"], run["id"])
    with store.unit_of_work() as work:
        read = dispatch_worker_trigger_tool(
            store, work.connection, run["id"], "read_trigger", {"trigger_id": trigger["id"]}
        )
        assert json.loads(read["text"]) == {"text": "task"}
        result = dispatch_worker_trigger_tool(
            store,
            work.connection,
            run["id"],
            "handback_trigger",
            {"trigger_id": trigger["id"], "reason": "other task"},
        )
        assert result == {"handed_back": True}


def test_subscription_context_does_not_grant_new_cold_start_authority(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message", allowed_actors=["alice"])
    first = service.receive("chat", "1", "topic", {}, actor="alice")
    run = worker(store)
    service.subscribe(first["thread_id"], run["id"])
    followup = service.receive(
        "chat", "2", "topic", {"text": "new unrelated task"}, actor="outsider"
    )
    assert followup["status"] == "passed_through"
    service.handback(run["id"], followup["id"], "outside the authorized task")
    assert service.pending() == []
    assert service.read_trigger(followup["id"])["reason"] == "actor_not_allowed"


def test_terminal_worker_never_wakes_and_shared_worker_can_follow_two_threads(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    first = service.receive("chat", "1", "one", {})
    second = service.receive("chat", "2", "two", {})
    run = worker(store)
    for item in (first, second):
        service.subscribe(item["thread_id"], run["id"])
    assert service.receive("chat", "3", "one", {})["status"] == "passed_through"
    assert service.receive("chat", "4", "two", {})["status"] == "passed_through"
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE worker_runs SET state='cancelled' WHERE id=?", (run["id"],))
    later = service.receive("chat", "5", "one", {})
    assert later["status"] == "pending" and later["deliveries"] == []
    with pytest.raises(DomainError, match="stopped"):
        service.subscribe(first["thread_id"], run["id"])


def test_timer_persistence_coalesces_restart_backlog_and_deduplicates(store):
    now = [100.0]
    events = TriggerService(store)
    events.register_source("clock", kind="timer")
    sources = LocalTriggerSources(store, clock=lambda: now[0])
    sources.schedule(
        "clock", "reminder", {"text": "check"}, due_at=90, interval_seconds=10, timer_id="daily"
    )
    first = sources.poll_due()
    assert len(first) == 1 and sources.poll_due() == []
    now[0] = 10000.0
    recovered = LocalTriggerSources(
        Store(store.path, workspace=store.workspace), clock=lambda: now[0]
    )
    assert len(recovered.poll_due()) == 1
    assert recovered.poll_due() == []
    with store.connection() as c:
        assert c.execute("SELECT occurrence FROM superagent_timers").fetchone()[0] == 2


def test_file_source_is_exact_detects_changes_and_survives_restart(store):
    events = TriggerService(store)
    events.register_source("files", kind="file")
    path = store.workspace.root / "input.bin"
    path.write_bytes(b"first")
    sources = LocalTriggerSources(store)
    sources.watch_file("files", "input.bin")
    assert sources.poll_files() == []
    path.write_bytes(bytes(range(256)))
    recovered = LocalTriggerSources(Store(store.path, workspace=store.workspace))
    trigger = recovered.poll_files()[0]
    body = json.loads(events.read_trigger(trigger["id"], max_chars=4096)["text"])
    assert base64.b64decode(body["content_base64"]) == bytes(range(256))
    assert recovered.poll_files() == []
    path.unlink()
    deletion = recovered.poll_files()[0]
    assert json.loads(events.read_trigger(deletion["id"])["text"])["exists"] is False


@pytest.mark.parametrize(
    "path",
    ["../outside", ".env", ".env.local", ".git/config", ".codex/auth.json", "events.sqlite3"],
)
def test_file_sources_reject_protected_paths(store, path):
    TriggerService(store).register_source("files", kind="file")
    with pytest.raises(DomainError):
        LocalTriggerSources(store).watch_file("files", path)


def test_file_source_rejects_link_replaced_after_registration(store):
    TriggerService(store).register_source("files", kind="file")
    target = store.workspace.root / "target.txt"
    target.write_text("original", encoding="utf-8")
    link = store.workspace.root / "watched.txt"
    link.write_text("safe", encoding="utf-8")
    sources = LocalTriggerSources(store)
    watch = sources.watch_file("files", "watched.txt")
    link.unlink()
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("OS user cannot create symbolic links")
    assert sources.poll_files() == []
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT last_error FROM superagent_file_watches WHERE id=?", (watch["id"],)
            ).fetchone()[0]
            == "source_unavailable"
        )


def test_ignored_source_never_delivers_even_with_subscription(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message", mode="ignore")
    first = service.receive("chat", "1", "topic", {})
    run = worker(store)
    service.subscribe(first["thread_id"], run["id"])
    later = service.receive("chat", "2", "topic", {"text": "must stay ignored"})
    assert later["status"] == "filtered"
    Dispatcher(store).tick()
    with store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM worker_inbox WHERE run_id=?", (run["id"],)).fetchone()[
                0
            ]
            == 0
        )


def test_transaction_rollback_removes_trigger_event_outbox_and_fanout(store):
    service = TriggerService(store)
    service.register_source("chat", kind="message")
    with pytest.raises(RuntimeError), store.unit_of_work() as work:
        service.receive("chat", "1", "topic", {"text": "task"}, connection=work.connection)
        raise RuntimeError("crash before commit")
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM superagent_triggers").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
