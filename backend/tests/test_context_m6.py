import json

import pytest

pytest_plugins = ["test_worker_runtime"]

from forge_core.context_service import ContextService
from forge_core.domain import ConversationMessage, DomainError
from forge_core.store import Store


def test_router_compaction_archives_without_deleting_and_rebuilds(tmp_path):
    store = Store(tmp_path / "context.sqlite3")
    for i in range(8):
        store.add_message(ConversationMessage(role="user", content=f"message {i} " + "x" * 100))
    service = ContextService(store)
    result = service.compact_router(keep_recent=2)
    assert result["source_count"] == 6
    assert len(store.list_messages()) == 8
    rebuilt = ContextService(store).rebuild_router()
    assert len(rebuilt["recent_messages"]) == 2
    assert rebuilt["capsule"]["source_event_cursor"] == 0
    assert rebuilt["capsule"]["hypotheses"]
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE messages SET content='tampered' WHERE id=?", (result["source_ids"][0],)
        )
    with pytest.raises(DomainError, match="integrity"):
        service.rebuild_router()


def test_compaction_rejects_stale_snapshot_and_preserves_atomicity(tmp_path):
    store = Store(tmp_path / "cas.sqlite3")
    store.add_message(ConversationMessage(role="user", content="original"))
    service = ContextService(store)
    snapshot = service.router_snapshot()
    store.add_message(ConversationMessage(role="user", content="new"))
    with pytest.raises(DomainError, match="changed"):
        service.compact_router(keep_recent=0, snapshot=snapshot)
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM context_archives").fetchone()[0] == 0


def test_archive_and_index_roll_back_when_router_cas_write_fails(tmp_path):
    import sqlite3

    store = Store(tmp_path / "rollback.sqlite3")
    store.add_message(ConversationMessage(role="user", content="keep"))
    before = store.ensure_router()
    with store.unit_of_work() as work:
        work.connection.execute(
            "CREATE TRIGGER fail_context_update BEFORE UPDATE ON router_state BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        ContextService(store).compact_router(keep_recent=0)
    with store.connection() as connection:
        for table in ("context_archives", "context_archive_sources", "context_archive_heads"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert store.ensure_router() == before


def test_capsule_contains_all_continuity_fields(tmp_path):
    store = Store(tmp_path / "fields.sqlite3")
    store.add_message(ConversationMessage(role="assistant", content="unverified claim"))
    service = ContextService(store)
    service.compact_router(keep_recent=0)
    capsule = service.rebuild_router()["capsule"]
    assert {
        "goal",
        "success_criteria",
        "facts",
        "hypotheses",
        "decisions",
        "completed_steps",
        "pending_steps",
        "blockers",
        "constraints",
        "paths",
        "artifacts",
        "evidence_refs",
        "next_action",
        "source_event_cursor",
    } <= capsule.keys()
    assert capsule["facts"] == []
    assert "unverified claim" in json.dumps(capsule["hypotheses"])


def test_router_retrieves_old_quest_for_real_model_path(tmp_path):
    import asyncio

    from forge_core.domain import Quest
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "recall.sqlite3")
    old = Quest(title="Zephyr algorithm", goal="Zephyr experiment")
    store.save_quest(old)
    for i in range(105):
        store.save_quest(Quest(title=f"other {i}", goal=f"other {i}"))

    class Model:
        execution_mode = "mock"

        async def generate(self, request):
            context = json.loads(request.messages[1]["content"])
            assert old.id in {q["id"] for q in context["quests"]}
            assert any(old.id in m["related_quest_ids"] for m in context["memory"])
            decision = {
                "intent": "continue_quest",
                "reply": "continue",
                "title": None,
                "goal": None,
                "quest_id": old.id,
                "candidates": [old.id],
            }
            return type("Response", (), {"text": json.dumps(decision), "usage": {}})()

    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=Model(), model_id="mock"
    )
    result = asyncio.run(router.handle_user_message("Zephyr", request_id="recall"))
    assert result["intent"] == "continue_quest"


def test_router_chinese_recall_with_two_old_candidates_asks_to_disambiguate(tmp_path):
    import asyncio

    from forge_core.domain import Quest
    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "ambiguous-recall.sqlite3")
    first = Quest(title="智能体算法实验甲", goal="研究智能体算法实验")
    second = Quest(title="智能体算法实验乙", goal="复现智能体算法实验")
    store.save_quest(first)
    store.save_quest(second)
    for i in range(105):
        q = Quest(title=f"其他任务{i}", goal=f"普通任务{i}")
        store.save_quest(q)
        with store.unit_of_work() as work:
            work.connection.execute("UPDATE quests SET status='cancelled' WHERE id=?", (q.id,))

    class Model:
        execution_mode = "mock"

        async def generate(self, request):
            return type(
                "Response",
                (),
                {
                    "text": json.dumps(
                        {
                            "intent": "continue_quest",
                            "reply": "continue",
                            "title": None,
                            "goal": None,
                            "quest_id": first.id,
                            "candidates": [first.id],
                        }
                    ),
                    "usage": {},
                },
            )()

    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=Model(), model_id="mock"
    )
    result = asyncio.run(
        router.handle_user_message("接着之前那个智能体算法实验", request_id="ambiguous-recall")
    )
    assert result["intent"] == "ambiguous"


@pytest.mark.parametrize("framing_boundary", [False, True])
def test_worker_compaction_keeps_pending_tool_pair(graph, framing_boundary):
    from test_worker_runtime import MockApprovedTeam, MockToolEngine, ScriptedMock

    from forge_core.provider_pool import ProviderPool
    from forge_core.worker_runtime import WorkerManager

    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    run = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        messages = [
            {"role": "assistant", "content": "old " * 500},
            {"role": "assistant", "content": "old " * 500},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "pending",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "pending", "content": "result"},
        ]
        for ordinal, message in enumerate(messages, 2):
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, message["role"], json.dumps(message)),
            )
    service = ContextService(store)
    framing = 0
    budget = 3500
    if framing_boundary:
        from forge_core.context_service import _json
        from forge_core.worker_prompts import WORKER_MESSAGE_FRAMING_BYTES

        unframed = service.build_worker_context(run["id"], input_budget=20000)
        budget = len(_json(unframed).encode("utf-8"))
        # Serialized content alone fits exactly; framing must trigger safe archival.
        assert service.build_worker_context(run["id"], input_budget=budget) == unframed
        framing = WORKER_MESSAGE_FRAMING_BYTES
    context = service.build_worker_context(
        run["id"], input_budget=budget, per_message_overhead=framing
    )
    if framing_boundary:
        assert len(_json(context).encode("utf-8")) + framing * len(context) <= budget
        assert len(context) < len(unframed)
    assert sum(m["role"] == "system" for m in context) == 1
    assert sum(m["role"] == "user" and m.get("content", "").startswith("{") for m in context) == 1
    assert any(m.get("tool_calls") for m in context)
    assert any(m.get("tool_call_id") == "pending" for m in context)
    assert len(
        ContextService(store).build_worker_context(
            run["id"], input_budget=budget, per_message_overhead=framing
        )
    ) == len(context)
    source = ContextService(Store(store.path, workspace=store.workspace)).read_source(
        "worker", run["id"], 2
    )
    assert json.loads(source["message_json"])["content"] == "old " * 500
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,?,?,?)",
            (run["id"], "internal", "pending", "read", "{}", "succeeded", None, "{}"),
        )
        for ordinal in (6, 7):
            message = {"role": "assistant", "content": "later " * 500}
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, "assistant", json.dumps(message)),
            )
        for ordinal in (8, 9):
            message = {"role": "assistant", "content": "recent"}
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, "assistant", json.dumps(message)),
            )
    second = ContextService(Store(store.path, workspace=store.workspace)).build_worker_context(
        run["id"], input_budget=5000
    )
    assert sum(m["role"] == "system" for m in second) == 1
    assert sum(m["role"] == "user" and m.get("content", "").startswith("{") for m in second) == 1


def test_router_auto_compacts_and_rebuilds_over_fifty_user_constraints(tmp_path):
    import asyncio

    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    path = tmp_path / "restart.sqlite3"
    store = Store(path)
    for i in range(60):
        store.add_message(
            ConversationMessage(role="user", content=f"constraint {i}: keep exact wording")
        )
        store.add_message(ConversationMessage(role="assistant", content="unverified " * 45))

    class Model:
        execution_mode = "mock"

        async def generate(self, request):
            data = json.loads(request.messages[1]["content"])
            capsule = json.loads(data["capsule"])
            assert any(
                item["text"] == "constraint 0: keep exact wording" for item in capsule["hypotheses"]
            )
            return type(
                "Response",
                (),
                {
                    "text": json.dumps(
                        {
                            "intent": "chat",
                            "reply": "ok",
                            "title": None,
                            "goal": None,
                            "quest_id": None,
                            "candidates": [],
                        }
                    ),
                    "usage": {},
                },
            )()

    restarted = Store(path)
    router = RouterService(
        restarted,
        TeamComposer(ModelRegistry(restarted)),
        provider=Model(),
        model_id="mock",
        context_limit=65536,
    )
    asyncio.run(router.handle_user_message("continue", request_id="after-restart"))
    assert len(restarted.list_messages()) == 122
    assert ContextService(Store(path)).rebuild_router()["capsule"]["hypotheses"]


def test_two_compactions_preserve_long_constraint_and_core_quests(graph):
    from forge_core.domain import Quest

    store, quest, _plan = graph
    second = Quest(title="second", goal="second goal", metadata={"constraint": "do not delete"})
    store.save_quest(second)
    long_constraint = "x" * 600 + " exact constraint after 513"
    store.add_message(ConversationMessage(role="user", content=long_constraint))
    store.add_message(ConversationMessage(role="assistant", content="claim only"))
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO quest_requirements VALUES (?,?,?,?,?)",
            (second.id, 0, json.dumps(["criterion"]), "[]", "[]"),
        )
        work.connection.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
            ("ev-claim", second.id, "model_claim", 0, "unverified claim", "now"),
        )
        work.connection.execute(
            "INSERT INTO evidence VALUES (?,?,?,?,?,?)",
            ("ev-trusted", second.id, "test", 1, "test output", "now"),
        )
    service = ContextService(store)
    service.compact_router(keep_recent=0)
    store.add_message(ConversationMessage(role="user", content="another constraint"))
    service.compact_router(keep_recent=0)
    capsule = ContextService(Store(store.path, workspace=store.workspace)).rebuild_router()[
        "capsule"
    ]
    assert any(item["text"] == long_constraint for item in capsule["hypotheses"])
    tasks = {task["quest_id"]: task for task in capsule["tasks"]}
    assert tasks[quest.id]["pending_steps"][0]["success_criteria"] == ["verified"]
    assert tasks[second.id]["success_criteria"] == ["criterion"]
    assert tasks[second.id]["constraints"]["constraint"] == "do not delete"
    assert tasks[second.id]["evidence_refs"] == ["ev-trusted"]
    assert tasks[second.id]["hypotheses"][0]["statement"] == "unverified claim"
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET goal=?,version=version+1 WHERE id=?", ("revised goal", second.id)
        )
        work.connection.execute(
            "UPDATE quest_requirements SET criteria_json=? WHERE quest_id=?",
            (json.dumps(["new criterion"]), second.id),
        )
    updated = {
        task["quest_id"]: task
        for task in ContextService(store).rebuild_router()["capsule"]["tasks"]
    }
    assert updated[second.id]["goal"] == "revised goal"
    assert updated[second.id]["success_criteria"] == ["new criterion"]


def test_capsule_never_treats_superseded_plan_as_current(graph):
    store, quest, plan = graph
    store.add_message(ConversationMessage(role="user", content="start"))
    service = ContextService(store)
    service.compact_router(keep_recent=0)
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE quests SET plan_revision=plan_revision+1,status='awaiting_approval' WHERE id=?",
            (quest.id,),
        )
        work.connection.execute(
            "UPDATE quest_plans SET status='superseded' WHERE id=?", (plan["id"],)
        )
    task = next(
        item
        for item in service.rebuild_router()["capsule"]["tasks"]
        if item["quest_id"] == quest.id
    )
    assert task["plan_id"] is None
    assert task["plan_state"] == "pending"
    assert task["pending_steps"] == []
    with store.unit_of_work() as work:
        work.connection.execute(
            "INSERT INTO quest_plans(id,quest_id,revision,digest,status,graph_json,created_at) VALUES (?,?,2,'new','proposed','{}','now')",
            ("new-plan", quest.id),
        )
    task = next(
        item
        for item in service.rebuild_router()["capsule"]["tasks"]
        if item["quest_id"] == quest.id
    )
    assert task["plan_state"] == "pending"
    assert task["pending_plan_id"] == "new-plan"
    assert task["plan_id"] is None and task["decisions"] == []


def test_router_reads_only_its_archived_source_and_enforces_bounds(tmp_path):
    import asyncio

    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "source.sqlite3")
    message = ConversationMessage(role="assistant", content="important original context")
    store.add_message(message)
    ContextService(store).compact_router(keep_recent=0)

    class Model:
        execution_mode = "mock"

        def __init__(self):
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                assert any(tool["name"] == "read_context_source" for tool in request.tools)
                return type(
                    "Response",
                    (),
                    {
                        "text": "",
                        "tool_calls": [
                            {
                                "id": "lookup",
                                "name": "read_context_source",
                                "arguments": {"source_id": message.id},
                            }
                        ],
                    },
                )()
            assert "important original context" in request.messages[-1]["content"]
            return type(
                "Response",
                (),
                {
                    "text": json.dumps(
                        {
                            "intent": "chat",
                            "reply": "ok",
                            "title": None,
                            "goal": None,
                            "quest_id": None,
                            "candidates": [],
                        }
                    ),
                    "usage": {},
                },
            )()

    model = Model()
    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=model, model_id="mock"
    )
    assert (
        asyncio.run(router.handle_user_message("continue", request_id="readback"))["intent"]
        == "chat"
    )


@pytest.mark.parametrize(
    "arguments,oversize",
    [
        ({"source_id": "missing"}, False),
        ({"source_id": "valid", "run_id": "other"}, False),
        ({"source_id": "valid", "max_chars": 2049}, True),
    ],
)
def test_router_readback_rejects_unknown_scope_and_large_sources(tmp_path, arguments, oversize):
    import asyncio

    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "denied.sqlite3")
    message = ConversationMessage(role="assistant", content="x" * (20000 if oversize else 10))
    store.add_message(message)
    ContextService(store).compact_router(keep_recent=0)
    arguments = {key: message.id if value == "valid" else value for key, value in arguments.items()}

    class Model:
        execution_mode = "mock"

        async def generate(self, request):
            return type(
                "Response",
                (),
                {
                    "text": "",
                    "tool_calls": [
                        {"id": "read", "name": "read_context_source", "arguments": arguments}
                    ],
                },
            )()

    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=Model(), model_id="mock"
    )
    with pytest.raises(DomainError):
        asyncio.run(router.handle_user_message("read", request_id="denied"))
    assert len(store.list_messages()) == 1


def test_router_reads_large_unicode_archive_in_bounded_chunks(tmp_path):
    import asyncio
    import hashlib

    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "chunks.sqlite3")
    original = "算法路径" * 1800
    message = ConversationMessage(role="assistant", content=original)
    store.add_message(message)
    ContextService(store).compact_router(keep_recent=0)

    class Model:
        execution_mode = "mock"

        def __init__(self):
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            if self.calls == 1:
                return type(
                    "Response",
                    (),
                    {
                        "text": "",
                        "tool_calls": [
                            {
                                "id": f"part{i}",
                                "name": "read_context_source",
                                "arguments": {
                                    "source_id": message.id,
                                    "offset_chars": offset,
                                    "max_chars": 2048,
                                },
                            }
                            for i, offset in enumerate((0, 2048, 4096, 6144))
                        ],
                    },
                )()
            chunks = [
                json.loads(item["content"]) for item in request.messages if item["role"] == "tool"
            ]
            assert "".join(item["content"] for item in chunks) == original
            assert [item["next_offset_chars"] for item in chunks] == [2048, 4096, 6144, None]
            assert all(
                item["source_sha256"] == hashlib.sha256(original.encode()).hexdigest()
                for item in chunks
            )
            return type(
                "Response",
                (),
                {
                    "text": json.dumps(
                        {
                            "intent": "chat",
                            "reply": "ok",
                            "title": None,
                            "goal": None,
                            "quest_id": None,
                            "candidates": [],
                        }
                    ),
                    "usage": {},
                },
            )()

    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=Model(), model_id="mock"
    )
    assert asyncio.run(router.handle_user_message("read", request_id="chunks"))["intent"] == "chat"


def test_router_batches_large_legacy_assistant_backlog(tmp_path):
    import asyncio

    from forge_core.registry import ModelRegistry, TeamComposer
    from forge_core.router import RouterService

    store = Store(tmp_path / "backlog.sqlite3")
    with store.unit_of_work() as work:
        work.connection.executemany(
            "INSERT INTO messages VALUES (?,?,?,?,?,?)",
            [(f"m{i}", "main", "assistant", "old log", "now", "{}") for i in range(5100)],
        )

    class Model:
        execution_mode = "mock"

        async def generate(self, request):
            return type(
                "Response",
                (),
                {
                    "text": json.dumps(
                        {
                            "intent": "chat",
                            "reply": "ok",
                            "title": None,
                            "goal": None,
                            "quest_id": None,
                            "candidates": [],
                        }
                    ),
                    "usage": {},
                },
            )()

    router = RouterService(
        store, TeamComposer(ModelRegistry(store)), provider=Model(), model_id="mock"
    )
    assert (
        asyncio.run(router.handle_user_message("continue", request_id="backlog"))["intent"]
        == "chat"
    )
    assert len(ContextService(store).rebuild_router()["recent_messages"]) <= 12
    assert ContextService(store).read_source("router", "main", "m0")["content"] == "old log"


def test_worker_completed_tool_history_is_compacted_with_receipt(graph):
    from test_worker_runtime import MockApprovedTeam, MockToolEngine, ScriptedMock

    from forge_core.provider_pool import ProviderPool
    from forge_core.worker_runtime import WorkerManager

    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    run = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        c = work.connection
        c.execute(
            "INSERT INTO action_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "intent",
                "call",
                quest.id,
                run["worker_id"],
                run["id"],
                plan["id"],
                plan["plan_revision"],
                "read",
                "{}",
                "digest",
                "project/result.json",
                "R0",
                None,
                "mock",
                "succeeded",
                "now",
                "now",
            ),
        )
        c.execute(
            "INSERT INTO action_receipts VALUES (?,?,?,?,?,?)",
            ("receipt", "intent", "mock", "{}", "resultdigest", "now"),
        )
        c.execute(
            "INSERT INTO worker_tool_calls VALUES (?,?,?,?,?,?,?,?)",
            (run["id"], "call", "model-call", "read", "{}", "succeeded", "receipt", "{}"),
        )
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "model-call",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "model-call", "content": "large result " * 1000},
            {"role": "assistant", "content": "next step"},
            {"role": "user", "content": "continue"},
        ]
        for ordinal, message in enumerate(messages, 2):
            c.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, message["role"], json.dumps(message)),
            )
    service = ContextService(store)
    rebuilt = service.build_worker_context(run["id"], input_budget=4000)
    assert len(json.dumps(rebuilt).encode()) < 4000
    capsule = json.loads(rebuilt[1]["content"].split(": ", 1)[1])
    assert capsule["completed_steps"][0]["receipt_id"] == "receipt"
    assert capsule["completed_steps"][0]["resource_scope"] == "project/result.json"
    assert (
        json.loads(service.read_source("worker", run["id"], 3)["message_json"])["content"]
        == "large result " * 1000
    )
    with store.unit_of_work() as work:
        work.connection.execute(
            "UPDATE worker_messages SET message_json='{}' WHERE run_id=? AND ordinal=2",
            (run["id"],),
        )
    with pytest.raises(DomainError, match="integrity"):
        service.build_worker_context(run["id"], input_budget=4000)


def test_worker_archive_rolls_back_on_checkpoint_failure(graph):
    import sqlite3

    from test_worker_runtime import MockApprovedTeam, MockToolEngine, ScriptedMock

    from forge_core.provider_pool import ProviderPool
    from forge_core.worker_runtime import WorkerManager

    store, quest, plan = graph
    manager = WorkerManager(
        store,
        ProviderPool({"mock-provider": ScriptedMock()}),
        MockToolEngine(),
        team_authority=MockApprovedTeam(plan),
    )
    run = manager.start(quest.id, plan["id"], "n", team_digest="team", member_index=0)
    with store.unit_of_work() as work:
        for ordinal in range(2, 6):
            message = {"role": "assistant", "content": "old " * 1000}
            work.connection.execute(
                "INSERT INTO worker_messages VALUES (?,?,?,?)",
                (run["id"], ordinal, "assistant", json.dumps(message)),
            )
        work.connection.execute(
            "CREATE TRIGGER fail_worker_context BEFORE UPDATE ON worker_capsules BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        ContextService(store).build_worker_context(run["id"], input_budget=12000)
    with store.connection() as connection:
        for table in ("context_archives", "context_archive_sources", "context_archive_heads"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT revision FROM worker_capsules WHERE run_id=?", (run["id"],)
            ).fetchone()[0]
            == 0
        )
