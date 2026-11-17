"""Offline equivalence and query-count checks for bounded milestone pages."""

import json
import sqlite3
from contextlib import contextmanager

import pytest
from test_subagent_activity import activity as activity  # noqa: PLC0414
from test_subagent_activity import delegated as delegated  # noqa: PLC0414
from test_subagent_phase import seed_persisted_history

import forge_core.subagent_activity as activity_read


def add_event(connection, run, suffix, **overrides):
    values = {
        "id": run["id"] + suffix,
        "event_type": "worker.result",
        "conversation_id": "main",
        "quest_id": run["quest_id"],
        "worker_id": run["worker_id"],
        "payload": json.dumps({"run_id": run["id"]}),
        "created_at": "2026-11-17T00:00:00Z",
        **overrides,
    }
    connection.execute(
        "INSERT INTO events (" + ",".join(values) + ") VALUES (?,?,?,?,?,?,?)",
        tuple(values.values()),
    )


@pytest.mark.parametrize("phase", ["all", "active", "terminal"])
@pytest.mark.parametrize("limit", [1, 50, 100])
def test_pages_match_individual_queries_with_exact_fences(activity, phase, limit):
    store, _, runs = activity
    seed_persisted_history(store, runs[0]["id"], ["failed"] * 105)
    with store.unit_of_work() as work:
        rows = work.connection.execute("SELECT * FROM worker_runs").fetchall()
        for row in rows:
            for index in range(8):
                add_event(work.connection, row, f"good{index}")
            for key, value in (
                ("conversation_id", "foreign"),
                ("quest_id", "foreign"),
                ("worker_id", "foreign"),
                ("payload", '{"run_id":"foreign"}'),
                ("payload", "{"),
                ("event_type", "review.result"),
            ):
                add_event(work.connection, row, f"bad{key}{value}", **{key: value})
    cursor = None
    seen = set()
    while True:
        page = activity_read.read_subagents(store, "main", phase=phase, limit=limit, before=cursor)
        with store.connection() as connection:
            for item in page["items"]:
                row = {**item, "id": item["run_id"], "conversation_id": "main"}
                assert item["milestones"] == activity_read._milestones(connection, row, 5)
                assert [m["event_id"] for m in item["milestones"]] == [
                    item["run_id"] + f"good{i}" for i in range(3, 8)
                ]
                assert item["run_id"] not in seen
                seen.add(item["run_id"])
        if not page["has_more"]:
            break
        cursor = page["next_before"]
    assert len(seen) == {"all": 108, "active": 3, "terminal": 105}[phase]


def test_list_one_event_query_detail_keeps_twenty_and_no_payload(activity, monkeypatch):
    store, _, runs = activity
    with store.unit_of_work() as work:
        for index in range(25):
            add_event(work.connection, runs[0], f"event{index}")
    statements = []
    original = store.connection

    @contextmanager
    def traced():
        with original() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(store, "connection", traced)
    page = activity_read.read_subagents(store, "main")
    assert len(page["items"]) == 3
    event_queries = [sql for sql in statements if "FROM events" in sql]
    assert len(event_queries) == 1 and "ROW_NUMBER()" in event_queries[0]
    statements.clear()
    detail = activity_read.read_subagents(store, "main", run_id=runs[0]["id"])
    assert len(detail["item"]["milestones"]) == 20
    assert len([sql for sql in statements if "FROM events" in sql]) == 1
    assert all(
        set(m) == {"event_id", "event_type", "title", "created_at", "seq"}
        for m in detail["item"]["milestones"]
    )


def test_corrupt_sources_keep_cursor_without_milestone_reads(activity, monkeypatch):
    store, _, _ = activity
    with store.unit_of_work() as work:
        work.connection.execute("UPDATE messages SET content='invalid source'")

    def forbidden(*args):
        raise AssertionError("Invalid sources must not query events")

    monkeypatch.setattr(activity_read, "_milestones", forbidden)
    # Empty batches short circuit before execute; no fabricated fallback results.
    assert activity_read._page_milestones(None, [], 5) == {}
    page = activity_read.read_subagents(store, "main", limit=1)
    assert page["items"] == [] and page["has_more"] and page["next_before"]


def test_sqlite_runtime_supports_materialized_window_queries():
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("Older SQLite uses the bounded single-run fallback")
    with sqlite3.connect(":memory:") as connection:
        assert connection.execute(
            "WITH x AS MATERIALIZED (SELECT 1 AS n) SELECT ROW_NUMBER() OVER(ORDER BY n) FROM x"
        ).fetchone() == (1,)


def test_old_sqlite_fallback_keeps_full_page_equal(activity, monkeypatch):
    store, _, _ = activity
    expected = activity_read.read_subagents(store, "main")
    monkeypatch.setattr(activity_read.sqlite3, "sqlite_version_info", (3, 34, 0))
    assert activity_read.read_subagents(store, "main") == expected


def test_new_sqlite_query_errors_are_not_retried_as_compatibility(activity):
    store, _, _ = activity
    with store.connection() as connection:
        rows = activity_read._scoped_rows(connection, store, "main", count=1)

    class BrokenConnection:
        calls = 0

        def execute(self, *args):
            self.calls += 1
            raise sqlite3.OperationalError("fixture query failure")

    broken = BrokenConnection()
    with pytest.raises(sqlite3.OperationalError, match="fixture query failure"):
        activity_read._page_milestones(broken, rows, 5)
    assert broken.calls == 1


def test_reused_worker_keeps_each_run_separate_and_empty_means_empty(activity):
    store, _, runs = activity
    ids = seed_persisted_history(store, runs[0]["id"], ["failed", "cancelled"])
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_runs SET worker_id=? WHERE id IN (?,?)",
            (runs[0]["worker_id"], *ids),
        )
        row = work.connection.execute("SELECT * FROM worker_runs WHERE id=?", (ids[0],)).fetchone()
        for index in range(8):
            add_event(work.connection, row, f"shared{index}")
    page = activity_read.read_subagents(store, "main", phase="terminal")
    by_id = {item["run_id"]: item["milestones"] for item in page["items"]}
    assert by_id[ids[1]] == []
    assert [item["event_id"] for item in by_id[ids[0]]] == [
        ids[0] + f"shared{i}" for i in range(3, 8)
    ]


def test_unverified_candidate_is_not_batched_or_replaced(activity, monkeypatch):
    store, _, runs = activity
    original = activity_read._source
    batch = activity_read._page_milestones
    observed = []

    def source(connection, row, workspace_id):
        return None if row["id"] == runs[-1]["id"] else original(connection, row, workspace_id)

    def capture(connection, rows, limit):
        observed.extend(row["id"] for row in rows)
        return batch(connection, rows, limit)

    monkeypatch.setattr(activity_read, "_source", source)
    monkeypatch.setattr(activity_read, "_page_milestones", capture)
    page = activity_read.read_subagents(store, "main", limit=2)
    assert observed == [runs[1]["id"]]
    assert [item["run_id"] for item in page["items"]] == observed
    assert page["has_more"] and page["next_before"]
