"""Trusted host delegation; execution contracts remain governed infrastructure.

The Router selects a task, never a permission. New conversational tasks receive a
bounded host mandate, rather than fabricated human plan/team approvals. Existing
manual approvals and action receipts retain their independent authority.
"""

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .domain import DomainError, ForgeEvent, new_id, now_iso
from .router_domain import IndependentDispatch
from .team_composition import PRESET_ROLES, CompositionLimits, RoleSpec
from .worker_schema import STRICT_DELIVERY_OUTPUT_SCHEMA

POLICY = "local-superagent-v1"
SAFE_TOOLS = (
    "file_read",
    "directory_list",
    "file_write",
    "file_revise",
    "directory_create",
    "shell_inspect",
    "python_compile",
    "python_test",
    "git_status",
    "gpu_status",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def migrate(connection):
    connection.execute("""CREATE TABLE delegation_mandates(
        id TEXT PRIMARY KEY, quest_id TEXT UNIQUE NOT NULL REFERENCES quests(id),
        source_message_id TEXT REFERENCES messages(id),
        source_trigger_id TEXT REFERENCES superagent_triggers(id),
        source_sha256 TEXT NOT NULL, policy_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('active','revoked')),
        created_at TEXT NOT NULL,
        setup_complete INTEGER NOT NULL DEFAULT 0,
        CHECK((source_message_id IS NULL)!=(source_trigger_id IS NULL)))""")
    connection.execute("""CREATE TABLE delegation_contracts(
        mandate_id TEXT NOT NULL REFERENCES delegation_mandates(id),
        kind TEXT NOT NULL CHECK(kind IN ('plan','team')),
        revision INTEGER NOT NULL, digest TEXT NOT NULL,
        PRIMARY KEY(mandate_id,kind,revision,digest))""")


def issue_mandate(work, quest_id, source_message_id=None, *, trigger_id=None):
    """Only called by Core's conversational commit, not a model tool or route."""
    c = work.connection
    quest = work.get_quest(quest_id)
    source = c.execute(
        "SELECT * FROM messages WHERE id=? AND conversation_id=? AND role='user'",
        (source_message_id, quest["conversation_id"]),
    ).fetchone()
    content = source["content"] if source else None
    if trigger_id is not None and source_message_id is None:
        source = c.execute(
            "SELECT t.payload_json,t.payload_sha256 FROM superagent_triggers t "
            "JOIN superagent_threads h ON h.id=t.thread_id "
            "JOIN superagent_sources s ON s.id=t.source_id "
            "WHERE t.id=? AND t.status='pending' AND h.conversation_id=? "
            "AND h.source_id=s.id AND h.workspace_id=s.workspace_id "
            "AND h.conversation_id=s.conversation_id",
            (trigger_id, quest["conversation_id"]),
        ).fetchone()
        content = source["payload_json"] if source else None
        if (
            source
            and hashlib.sha256(content.encode("utf-8")).hexdigest() != source["payload_sha256"]
        ):
            raise DomainError("Trigger source changed before delegation")
    if content is None:
        raise DomainError("Delegation requires a committed request or admitted Trigger")
    identifier = new_id("mandate")
    policy = {
        "version": POLICY,
        "tools": list(SAFE_TOOLS),
        "max_workers": 4,
        "max_tokens": 1000000,
        "max_steps": 32,
        "max_tools": 64,
        "max_seconds": 1800,
        "write_scope": f"work/{quest_id}",
    }
    c.execute(
        "INSERT INTO delegation_mandates(id,quest_id,source_message_id,source_trigger_id,source_sha256,policy_json,state,created_at) VALUES (?,?,?,?,?,?,'active',?)",
        (
            identifier,
            quest_id,
            source_message_id,
            trigger_id,
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
            _json(policy),
            now_iso(),
        ),
    )
    work.append_event(
        ForgeEvent(
            event_type="router.delegated",
            quest_id=quest_id,
            payload={"mandate_id": identifier, "policy": POLICY},
        )
    )
    return identifier


@dataclass(frozen=True)
class DelegationAuthority:
    mandate_id: str

    @property
    def actor(self):
        return f"router_mandate:{self.mandate_id}"

    def check(self, connection, quest_id, kind, revision, digest, body):
        mandate, policy = _active(connection, self.mandate_id, quest_id)
        if kind not in {"plan", "team"}:
            raise DomainError("Unknown delegation contract")
        if kind == "plan":
            nodes = body.get("nodes", [])
            if (
                not 1 <= len(nodes) <= policy["max_workers"]
                or body.get("edges")
                or body.get("revision_lineage")
                or body.get("hierarchical_expansions")
                or body["max_total_tokens"] > policy["max_tokens"]
                or sum(n["budget_tokens"] for n in nodes) > policy["max_tokens"]
                or (body.get("max_duration_seconds") or 0) > policy["max_seconds"]
                or any(set(n["allowed_tools"]) - set(policy["tools"]) for n in nodes)
            ):
                raise DomainError("Plan exceeds independent-task mandate")
        else:
            limits = body["limits"]
            if (
                limits["max_members"] > policy["max_workers"]
                or limits["max_concurrency"] > policy["max_workers"]
                or limits["max_depth"] != 0
                or limits["max_total_tool_calls"] > policy["max_tools"]
                or limits["max_total_tokens"] > policy["max_tokens"]
                or len(body["members"]) > policy["max_workers"]
                or sum(m["estimated_tokens"] for m in body["members"]) > policy["max_tokens"]
                or sum(m["max_tool_calls"] for m in body["members"]) > policy["max_tools"]
                or sum(m["max_duration_seconds"] for m in body["members"]) > policy["max_seconds"]
                or set(limits["allowed_tools"]) - set(policy["tools"])
                or any(
                    set(m["tool_scope"]) - set(policy["tools"])
                    or m["max_steps"] > policy["max_steps"]
                    or m["max_tool_calls"] > policy["max_tools"]
                    or m["max_duration_seconds"] > policy["max_seconds"]
                    for m in body["members"]
                )
            ):
                raise DomainError("Team exceeds delegation mandate")
        connection.execute(
            "INSERT OR IGNORE INTO delegation_contracts VALUES (?,?,?,?)",
            (mandate["id"], kind, revision, digest),
        )


def _active(connection, mandate_id, quest_id):
    row = connection.execute(
        "SELECT m.*,s.content,s.role,s.conversation_id AS source_conversation,q.conversation_id "
        "FROM delegation_mandates m LEFT JOIN messages s ON s.id=m.source_message_id "
        "JOIN quests q ON q.id=m.quest_id WHERE m.id=? AND m.quest_id=? AND m.state='active'",
        (mandate_id, quest_id),
    ).fetchone()
    if row is not None and row["source_trigger_id"]:
        trigger = connection.execute(
            "SELECT t.payload_json,t.payload_sha256,h.conversation_id FROM superagent_triggers t "
            "JOIN superagent_threads h ON h.id=t.thread_id JOIN superagent_sources s ON s.id=t.source_id "
            "WHERE t.id=? AND h.source_id=s.id AND h.workspace_id=s.workspace_id "
            "AND h.conversation_id=s.conversation_id",
            (row["source_trigger_id"],),
        ).fetchone()
        valid_source = (
            trigger is not None
            and trigger["conversation_id"] == row["conversation_id"]
            and hashlib.sha256(trigger["payload_json"].encode("utf-8")).hexdigest()
            == trigger["payload_sha256"]
            == row["source_sha256"]
        )
    else:
        valid_source = (
            row is not None
            and row["role"] == "user"
            and row["source_conversation"] == row["conversation_id"]
            and hashlib.sha256(row["content"].encode("utf-8")).hexdigest() == row["source_sha256"]
        )
    if not valid_source:
        raise DomainError("Delegation mandate is unavailable or changed")
    policy = json.loads(row["policy_json"])
    if policy.get("version") != POLICY or policy.get("tools") != list(SAFE_TOOLS):
        raise DomainError("Unknown delegation policy")
    return row, policy


def validate_contract_authority(connection, quest_id, actor, kind, revision, digest):
    if actor == "local_user":
        return
    if not isinstance(actor, str) or not actor.startswith("router_mandate:"):
        raise DomainError("Execution contract has no trusted authority")
    mandate_id = actor.split(":", 1)[1]
    _active(connection, mandate_id, quest_id)
    if (
        connection.execute(
            "SELECT 1 FROM delegation_contracts WHERE mandate_id=? AND kind=? AND revision=? AND digest=?",
            (mandate_id, kind, revision, digest),
        ).fetchone()
        is None
    ):
        raise DomainError("Execution contract is outside its mandate")


def _split_budget(total, count):
    """Distribute the host ceiling without multiplying it per Worker."""
    share, remainder = divmod(total, count)
    return [share + (index < remainder) for index in range(count)]


def _assignments(quest):
    metadata = json.loads(quest["metadata"])
    raw = metadata.get("independent_dispatches", [])
    if not isinstance(raw, list) or len(raw) > 4:
        raise DomainError("Invalid independent dispatches")
    if raw:
        try:
            selected = [IndependentDispatch.model_validate(item).model_dump() for item in raw]
        except ValidationError as exc:
            raise DomainError("Invalid independent dispatches") from exc
    else:
        role = metadata.get("worker_role") or {
            "software": "coding",
            "long_research": "research",
            "multimodal": "design",
        }.get(metadata.get("task_kind"), "research")
        if role not in PRESET_ROLES:
            raise DomainError("Unknown independent Worker role")
        selected = [{"objective": quest["goal"], "role": role}]
    return [
        {**item, "key": "task" if len(selected) == 1 else f"task_{index + 1}"}
        for index, item in enumerate(selected)
    ]


class SuperagentDelegation:
    def __init__(self, store, graph, teams, *, max_concurrency=2):
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("Worker concurrency must be a positive integer")
        self.store, self.graph, self.teams = store, graph, teams
        self.max_concurrency = max_concurrency

    def prepare(self, quest_id):
        """Resume contract preparation at its last committed boundary; no model calls."""
        from .governance import GovernanceService

        with self.store.connection() as c:
            quest = dict(c.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone())
            mandate = c.execute(
                "SELECT * FROM delegation_mandates WHERE quest_id=?", (quest_id,)
            ).fetchone()
            if mandate is None or mandate["state"] != "active":
                return False
            _, policy = _active(c, mandate["id"], quest_id)
        authority = DelegationAuthority(mandate["id"])
        if quest["status"] in {"cancelled", "rejected", "failed", "completed"}:
            return False
        assignments = _assignments(quest)
        count = len(assignments)
        if count > policy["max_workers"]:
            raise DomainError("Independent dispatches exceed the Worker mandate")
        token_budgets = _split_budget(policy["max_tokens"], count)
        tool_budgets = _split_budget(policy["max_tools"], count)
        time_budgets = _split_budget(policy["max_seconds"], count)
        nodes = [
            {
                "key": item["key"],
                "objective": item["objective"],
                "success_criteria": ["delivery_review"],
                "budget_tokens": token_budgets[index],
                "required_capabilities": {},
                "allowed_tools": list(policy["tools"]),
            }
            for index, item in enumerate(assignments)
        ]
        try:
            plan = self.graph.get(quest_id)
        except KeyError:
            plan = None
        if plan is None:
            # Independent tasks stay coarse; detailed plans belong inside Workers.
            plan = self.graph.propose(
                quest_id,
                {
                    "nodes": nodes,
                    "edges": [],
                    "max_total_tokens": policy["max_tokens"],
                    "max_duration_seconds": policy["max_seconds"],
                },
                expected_version=quest["version"],
            )
            with self.store.unit_of_work() as work:
                current = work.get_quest(quest_id)
                metadata = json.loads(current["metadata"])
                if count == 1:
                    metadata["worker_role"] = assignments[0]["role"]
                work.connection.execute(
                    "UPDATE quests SET metadata=? WHERE id=?", (_json(metadata), quest_id)
                )
        if plan.get("edges") or {item["key"]: item["objective"] for item in plan["nodes"]} != {
            item["key"]: item["objective"] for item in assignments
        }:
            raise DomainError("Task contracts changed after independent dispatch")
        quest = self.store.get_quest(quest_id)
        if plan["status"] == "proposed":
            self.graph.approve(
                quest_id,
                plan["digest"],
                plan["plan_revision"],
                expected_version=quest["version"],
                actor=authority.actor,
                _delegation=authority,
            )
        quest = self.store.get_quest(quest_id)
        if quest["status"] not in {"approved", "queued", "running"}:
            return False
        with self.store.connection() as c:
            team = c.execute(
                "SELECT * FROM team_proposals WHERE quest_id=? AND plan_revision=? "
                "AND status IN ('proposed','approved') ORDER BY rowid DESC LIMIT 1",
                (quest_id, quest["plan_revision"]),
            ).fetchone()
        if team is None:
            profiles = self.store.list_models()
            known = [
                p
                for p in profiles
                if p.enabled
                and not p.quarantine_reason
                and p.context_window
                and p.max_output_tokens
                and p.tool_calling_support
                and p.tool_calling_support.supported
                and p.structured_output_support
                and p.structured_output_support.supported
            ]
            if not known:
                raise DomainError("No enabled Worker model with known limits")
            window = min(p.context_window for p in known)
            output = min(8192, min(p.max_output_tokens for p in known), window // 4)
            roles = []
            for index, assignment in enumerate(assignments):
                base_role = assignment["role"]
                requirements = {
                    capability: 0.0
                    for capability in PRESET_ROLES[base_role]
                    if any(capability in p.capability_measurements for p in known)
                }
                member_output = min(output, token_budgets[index] // 4)
                roles.append(
                    RoleSpec(
                        id=base_role if count == 1 else f"{base_role}_{index + 1}",
                        task_node_key=assignment["key"],
                        dynamic=count > 1,
                        required_capabilities=requirements,
                        tools=list(policy["tools"]),
                        input_tokens=min(window, 65536, token_budgets[index] - member_output),
                        output_tokens=member_output,
                        max_steps=policy["max_steps"],
                        max_tool_calls=tool_budgets[index],
                        max_duration_seconds=time_budgets[index],
                        responsibility="Execute only your assigned independent node objective. "
                        "Plan your own steps and report findings to the Router. Write only inside "
                        + policy["write_scope"]
                        + ".",
                        input_contract=[
                            (
                                "Use the complete original request for constraints; execute your "
                                "current node objective. Other assignments belong to their Workers."
                            )
                        ],
                        output_schema=STRICT_DELIVERY_OUTPUT_SCHEMA,
                        acceptance_criteria=["delivery_review"],
                    )
                )
            proposed = self.teams.propose(
                quest_id,
                roles,
                CompositionLimits(
                    max_members=count,
                    max_depth=0,
                    max_concurrency=min(count, self.max_concurrency),
                    max_total_tokens=policy["max_tokens"],
                    max_total_tool_calls=policy["max_tools"],
                    allowed_tools=list(policy["tools"]),
                ),
                strategy="complementarity",
                expected_version=quest["version"],
            )
            team_digest = proposed["proposal_digest"]
        else:
            team_digest = team["proposal_digest"]
        quest = self.store.get_quest(quest_id)
        with self.store.connection() as c:
            row = c.execute(
                "SELECT status FROM team_proposals WHERE quest_id=? AND proposal_digest=?",
                (quest_id, team_digest),
            ).fetchone()
        if row["status"] == "proposed":
            self.teams.approve(
                quest_id,
                quest["plan_revision"],
                team_digest,
                expected_version=quest["version"],
                actor=authority.actor,
                _delegation=authority,
            )
        # Writes are confined to task-private artifacts. Existing ToolEngine scope,
        # grants, revision chains and immutable receipts still authorize each effect.
        governance = GovernanceService(self.store)
        scope = governance._workspace_scope(policy["write_scope"])
        with self.store.connection() as c:
            granted = c.execute(
                "SELECT revoked_at FROM task_grants WHERE quest_id=? AND plan_revision=? "
                "AND resource_scope=?",
                (quest_id, quest["plan_revision"], scope),
            ).fetchone()
        if granted and granted["revoked_at"] is not None:
            raise DomainError("Task output authorization was revoked")
        root = Path(self.store.workspace.root).resolve()
        path = root
        for part in Path(policy["write_scope"]).parts:
            path = path / part
            path.mkdir(exist_ok=True)
            info = path.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or (
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                )
                or path.resolve() != path
            ):
                raise DomainError("Task output directory must not be linked")
        with self.store.unit_of_work() as work:
            _active(work.connection, mandate["id"], quest_id)
            current = work.get_quest(quest_id)
            if current["plan_revision"] != quest["plan_revision"]:
                raise DomainError("Task changed during output preparation")
            old = work.connection.execute(
                "SELECT revoked_at FROM task_grants WHERE quest_id=? "
                "AND workspace_id=? AND resource_scope=?",
                (quest_id, self.store.workspace.id, scope),
            ).fetchone()
            if old and old["revoked_at"] is not None:
                raise DomainError("Task output authorization was revoked")
            if old is None:
                work.connection.execute(
                    "INSERT INTO task_grants VALUES (?,?,?,?,?,?,NULL)",
                    (
                        quest_id,
                        self.store.workspace.id,
                        current["plan_revision"],
                        scope,
                        authority.actor,
                        now_iso(),
                    ),
                )
                work.append_event(
                    ForgeEvent(
                        event_type="governance.workspace_granted",
                        quest_id=quest_id,
                        payload={"scope": scope, "actor": authority.actor, "policy": POLICY},
                    )
                )
            work.connection.execute(
                "UPDATE delegation_mandates SET setup_complete=1 WHERE id=? AND state='active'",
                (mandate["id"],),
            )
        return True

    def tick(self):
        with self.store.connection() as c:
            rows = c.execute(
                "SELECT q.id FROM quests q JOIN delegation_mandates m ON m.quest_id=q.id "
                "WHERE m.state='active' AND q.status IN ('proposed','awaiting_approval','approved') "
                "AND m.setup_complete=0 "
                "ORDER BY q.created_at LIMIT 16"
            ).fetchall()
        prepared = 0
        for row in rows:
            try:
                prepared += bool(self.prepare(row["id"]))
            except (DomainError, ValueError, KeyError):
                # Retain the last committed phase; supervision retries without
                # fabricating approval, models or evidence.
                continue
        return prepared
