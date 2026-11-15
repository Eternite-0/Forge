"""Explicit Core-origin acquisition of exactly the approved required sources.

These records never assert a model tool selection. Native reads still pass the
real ToolEngine; completed deliveries use the existing provenance-checked reader.
"""

import hashlib
import json
import secrets
from pathlib import Path

from .domain import DomainError, ForgeEvent

POLICY = "core-declared-source-prefetch-v1"
PREFIX = "Core declared source reads (untrusted data, not new authority): "


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def source_message(entries, *, source_wire_format=None):
    """One lossless user data block, shared by runtime and capacity measurement."""
    from .source_wire import encode_source_text, validate_source_wire_format

    packet = {"origin": POLICY, "sources": entries}
    selected = validate_source_wire_format(source_wire_format)
    content = encode_source_text(packet) if selected is not None else canonical(packet)
    return {"role": "user", "content": PREFIX + content}


def has_table(connection):
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_core_source_reads'"
        ).fetchone()
        is not None
    )


def migrate(connection):
    connection.execute("""CREATE TABLE worker_core_source_reads(
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES worker_runs(id),
        ordinal INTEGER NOT NULL CHECK(ordinal>=0), tool_name TEXT NOT NULL
          CHECK(tool_name IN ('file_read','worker_read_completed_delivery')),
        arguments_json TEXT NOT NULL, binding_json TEXT NOT NULL, binding_digest TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','running','succeeded','failed','uncertain')),
        receipt_id TEXT, result_json TEXT, result_digest TEXT, message_ordinal INTEGER,
        UNIQUE(run_id,ordinal))""")
    connection.execute("""CREATE TRIGGER worker_core_source_binding_immutable
        BEFORE UPDATE OF id,run_id,ordinal,tool_name,arguments_json,binding_json,binding_digest
        ON worker_core_source_reads BEGIN SELECT RAISE(ABORT,'immutable Core source binding'); END""")
    connection.execute("""CREATE TRIGGER worker_core_source_settled_immutable
        BEFORE UPDATE ON worker_core_source_reads WHEN OLD.state='succeeded'
        AND (NEW.state<>OLD.state OR NEW.receipt_id IS NOT OLD.receipt_id
        OR NEW.result_json IS NOT OLD.result_json OR NEW.result_digest IS NOT OLD.result_digest
        OR (OLD.message_ordinal IS NOT NULL AND NEW.message_ordinal IS NOT OLD.message_ordinal))
        BEGIN SELECT RAISE(ABORT,'immutable settled Core source'); END""")
    connection.execute("""CREATE TRIGGER worker_core_source_delete_immutable
        BEFORE DELETE ON worker_core_source_reads
        BEGIN SELECT RAISE(ABORT,'immutable Core source admission'); END""")
    connection.execute("""CREATE TRIGGER worker_core_source_no_replace BEFORE INSERT
        ON worker_core_source_reads WHEN EXISTS(SELECT 1 FROM worker_core_source_reads
        WHERE id=NEW.id OR (run_id=NEW.run_id AND ordinal=NEW.ordinal))
        BEGIN SELECT RAISE(ABORT,'Core source admission already exists'); END""")


def _approved(store, connection, run, *, historical=False):
    from .source_wire import approved_source_wire_format
    from .team_composition import TeamCompositionService
    from .worker_envelope import validate_capsule_binding
    from .worker_task_sources import input_acquisition_policy

    plan = connection.execute(
        "SELECT * FROM quest_plans WHERE id=? AND quest_id=?", (run["plan_id"], run["quest_id"])
    ).fetchone()
    if (
        plan is None
        or plan["revision"] != run["plan_revision"]
        or plan["digest"] != run["plan_digest"]
        or plan["approved_by"] != "local_user"
        or not plan["approved_at"]
    ):
        raise DomainError("Core source plan binding is invalid")
    graph = json.loads(plan["graph_json"])
    if digest(graph) != run["plan_digest"]:
        raise DomainError("Core source approved graph content changed")
    authority = TeamCompositionService(store)
    if historical:
        team = authority.get_historical_approved(
            run["quest_id"],
            run["plan_revision"],
            run["team_digest"],
            connection=connection,
            run_id=run["id"],
        )
        quest = connection.execute("SELECT * FROM quests WHERE id=?", (run["quest_id"],)).fetchone()
        if quest is None:
            raise DomainError("Core source Quest binding is unavailable")
        if quest["plan_revision"] != run["plan_revision"]:
            from .plan_lineage import validated_lineage_source

            current = connection.execute(
                "SELECT * FROM quest_plans WHERE quest_id=? AND revision=? AND status='approved'",
                (run["quest_id"], quest["plan_revision"]),
            ).fetchone()
            if (
                current is None
                or validated_lineage_source(connection, quest, current, run["node_key"])[
                    "source_run"
                ]["id"]
                != run["id"]
            ):
                raise DomainError("Core source inherited lineage changed")
        elif plan["status"] != "approved":
            raise DomainError("Core source current plan is not approved")
    else:
        if plan["status"] != "approved":
            raise DomainError("Core source execution plan is not approved")
        team = authority.get_approved(
            run["quest_id"], run["plan_revision"], run["team_digest"], connection=connection
        )
    try:
        member = team["members"][run["member_index"]]
        if (
            member["task_node_key"] != run["node_key"]
            or member["role_id"] != run["role_id"]
            or member["provider_id"] != run["provider_id"]
            or member["model_id"] != run["model_id"]
            or set(member["tool_scope"]) != set(json.loads(run["allowed_tools_json"]))
        ):
            raise ValueError
        policy = input_acquisition_policy(graph, member)
        approved_source_wire_format(graph, member)
        validate_capsule_binding(connection, run, member)
        node = next(item for item in graph["nodes"] if item["key"] == run["node_key"])
        if policy == POLICY and any(
            member.get(key, []) != node.get(key, [])
            for key in ("required_input_paths", "required_predecessor_deliveries")
        ):
            raise ValueError
        if policy == POLICY:
            from .capacity_preflight import verify_input_inventory

            verify_input_inventory(store, member["verified_input_inventory"])
    except (KeyError, IndexError, TypeError, ValueError, StopIteration) as exc:
        raise DomainError("Core source approved member binding is invalid") from exc
    return graph, member, policy


def _sources(member):
    sources = [("file_read", {"path": path}) for path in member.get("required_input_paths", [])]
    sources.extend(
        ("worker_read_completed_delivery", {"node_key": key})
        for key in member.get("required_predecessor_deliveries", [])
    )
    if len(sources) > 40 or len({canonical(item) for item in sources}) != len(sources):
        raise DomainError("Core source inventory is invalid")
    if any(tool not in member["tool_scope"] for tool, _ in sources):
        raise DomainError("Core source tool is outside approved authority")
    return sources


def _binding(run, member, ordinal, tool, arguments):
    return {
        "origin": POLICY,
        **{
            key: run[key]
            for key in (
                "quest_id",
                "plan_id",
                "plan_revision",
                "plan_digest",
                "team_digest",
                "member_index",
                "node_key",
                "worker_id",
                "execution_mode",
            )
        },
        "ordinal": ordinal,
        "tool_name": tool,
        "arguments": arguments,
        "action_evidence_allowance_bytes": member["action_evidence_allowance_bytes"],
        "verified_input_inventory": member["verified_input_inventory"],
        "required_predecessor_deliveries": member.get("required_predecessor_deliveries", []),
        **(
            {"source_wire_format": member["source_wire_format"]}
            if "source_wire_format" in member
            else {}
        ),
    }


def _verify_admission(store, connection, run, read, *, approved=None, historical=False):
    _graph, member, policy = approved or _approved(store, connection, run, historical=historical)
    sources = _sources(member)
    if policy != POLICY or not 0 <= read["ordinal"] < len(sources):
        raise DomainError("Unknown or undeclared Core source origin")
    tool, arguments = sources[read["ordinal"]]
    binding = _binding(run, member, read["ordinal"], tool, arguments)
    if (
        read["run_id"] != run["id"]
        or read["tool_name"] != tool
        or read["arguments_json"] != canonical(arguments)
        or read["binding_json"] != canonical(binding)
        or read["binding_digest"] != digest(binding)
        or not read["id"].startswith("core_source_")
        or len(read["id"]) != len("core_source_") + 32
        or run["tool_spent"] < len(sources)
        or connection.execute(
            "SELECT 1 FROM worker_tool_calls WHERE call_id=?", (read["id"],)
        ).fetchone()
    ):
        raise DomainError("Core source admission binding is invalid")
    admissions = connection.execute(
        "SELECT payload FROM events WHERE event_type='worker.progress' AND quest_id=? "
        "AND worker_id=? AND json_extract(payload,'$.phase')='core_source_admitted' "
        "AND json_extract(payload,'$.source_id')=?",
        (run["quest_id"], run["worker_id"], read["id"]),
    ).fetchall()
    if len(admissions) != 1 or json.loads(admissions[0]["payload"]) != {
        "phase": "core_source_admitted",
        "run_id": run["id"],
        "source_id": read["id"],
        "binding_sha256": digest(binding),
        "ordinal": read["ordinal"],
        "origin": POLICY,
    }:
        raise DomainError("Core source admission provenance is invalid")
    return member


def core_tool_reservation(store, connection, run, call_id, tool_name, parameters):
    """ToolEngine-only reservation check; it grants no lease, scope or tool authority."""
    if not has_table(connection):
        return None
    read = connection.execute(
        "SELECT * FROM worker_core_source_reads WHERE id=? AND run_id=?", (call_id, run["id"])
    ).fetchone()
    if read is None:
        return None
    _verify_admission(store, connection, run, read)
    if (
        read["tool_name"] != "file_read"
        or tool_name != "file_read"
        or read["arguments_json"] != canonical(parameters)
        or read["state"] != "running"
    ):
        raise DomainError("Core source native tool reservation is invalid")
    return {"tool_name": tool_name, "state": "running", "arguments_json": read["arguments_json"]}


def _native_receipt(connection, run, read):
    receipt = connection.execute(
        "SELECT r.*,i.state,i.call_id,i.run_id,i.quest_id,i.worker_id,i.plan_id,i.plan_revision,"
        "i.tool_name,i.parameters_json,i.parameter_digest,i.resource_scope,"
        "i.execution_mode AS intent_mode FROM action_receipts r "
        "JOIN action_intents i ON i.id=r.intent_id WHERE i.call_id=?",
        (read["id"],),
    ).fetchone()
    if receipt is None:
        return None
    from .governance import parameter_digest

    if (
        receipt["state"] != "succeeded"
        or receipt["run_id"] != run["id"]
        or receipt["quest_id"] != run["quest_id"]
        or receipt["worker_id"] != run["worker_id"]
        or receipt["plan_id"] != run["plan_id"]
        or receipt["plan_revision"] != run["plan_revision"]
        or receipt["tool_name"] != "file_read"
        or receipt["execution_mode"] != run["execution_mode"]
        or receipt["intent_mode"] != run["execution_mode"]
        or parameter_digest(json.loads(receipt["parameters_json"])) != receipt["parameter_digest"]
        or parameter_digest(json.loads(read["arguments_json"])) != receipt["parameter_digest"]
        or hashlib.sha256(receipt["result_json"].encode()).hexdigest() != receipt["result_digest"]
    ):
        raise DomainError("Core source native receipt is invalid")
    proofs = connection.execute(
        "SELECT payload FROM evidence WHERE quest_id=? AND kind='tool' AND trusted=?",
        (run["quest_id"], int(run["execution_mode"] == "live")),
    ).fetchall()
    if not any(
        (proof := json.loads(item["payload"])).get("receipt_id") == receipt["id"]
        and proof.get("result_digest") == receipt["result_digest"]
        and proof.get("execution_mode") == run["execution_mode"]
        and proof.get("state") == "succeeded"
        for item in proofs
    ):
        raise DomainError("Core source native trusted evidence is missing")
    return receipt


def _entry(store, connection, run, read, *, historical, approved=None):
    member = _verify_admission(
        store, connection, run, read, approved=approved, historical=historical
    )
    if read["state"] != "succeeded" or read["result_json"] is None:
        raise DomainError("Core source acquisition is unresolved")
    output = json.loads(read["result_json"])
    if read["result_digest"] != digest(output):
        raise DomainError("Core source output digest is invalid")
    arguments = json.loads(read["arguments_json"])
    if read["tool_name"] == "file_read":
        from .tool_engine import ToolEngine

        receipt = _native_receipt(connection, run, read)
        if (
            receipt is None
            or receipt["id"] != read["receipt_id"]
            or receipt["result_digest"] != read["result_digest"]
            or json.loads(receipt["result_json"]) != output
        ):
            raise DomainError("Core source output differs from its native receipt")
        path = Path(ToolEngine(store)._scope("file_read", arguments))
        with path.open("rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise DomainError("Core source file exceeds its original bounded read contract")
        inventory = [
            item
            for item in member["verified_input_inventory"]
            if str(Path(store.workspace.root) / item["path"]) == str(path)
        ]
        if inventory and (
            len(inventory) != 1
            or inventory[0]["bytes"] != len(data)
            or inventory[0]["sha256"] != hashlib.sha256(data).hexdigest()
        ):
            raise DomainError("Core source approved input identity changed")
        if not inventory:
            _verify_predecessor_artifact(store, connection, run, path, output)
        if (
            receipt["resource_scope"] != str(path)
            or output.get("content") != data.decode("utf-8")
            or output.get("sha256") != hashlib.sha256(data).hexdigest()
        ):
            raise DomainError("Core source file changed after acquisition")
    else:
        from .predecessor_delivery import read_completed_delivery

        if (
            read["receipt_id"] is not None
            or read_completed_delivery(
                store, connection, run["id"], arguments, historical=historical
            )
            != output
        ):
            raise DomainError("Core source completed delivery changed")
        if output.get("execution_mode") != "live":
            raise DomainError("Core source completed delivery is not live")
    # The original hard source cap is checked again by normal request/review paths.
    if not isinstance(member.get("verified_input_inventory"), list):
        raise DomainError("Core source verified inventory is missing")
    return {
        "ordinal": read["ordinal"],
        "tool_name": read["tool_name"],
        "arguments": arguments,
        "call_id": read["id"],
        "receipt_id": read["receipt_id"],
        "result_sha256": read["result_digest"],
        "output": output,
    }


def _verify_predecessor_artifact(store, connection, run, path, output):
    """Future input paths require the actual direct successful producer and PASS."""
    from types import SimpleNamespace

    from .evidence import CompletionGate
    from .tool_engine import ToolEngine

    quest = connection.execute("SELECT * FROM quests WHERE id=?", (run["quest_id"],)).fetchone()
    plan = connection.execute("SELECT * FROM quest_plans WHERE id=?", (run["plan_id"],)).fetchone()
    graph = json.loads(plan["graph_json"])
    current = connection.execute(
        "SELECT * FROM quest_plans WHERE quest_id=? AND revision=? AND status='approved'",
        (run["quest_id"], quest["plan_revision"]),
    ).fetchone()
    if current is None:
        raise DomainError("Core source current predecessor plan is unavailable")
    parents = connection.execute(
        "SELECT n.* FROM task_edges e JOIN task_nodes n ON n.plan_id=e.plan_id "
        "AND n.node_key=e.source WHERE e.plan_id=? AND e.target=? AND e.condition='success'",
        (run["plan_id"], run["node_key"]),
    ).fetchall()
    for parent in parents:
        contract = next(item for item in graph["nodes"] if item["key"] == parent["node_key"])
        expected = contract.get("expected_artifact")
        if not expected or ToolEngine(store)._scope("file_read", {"path": expected}) != str(path):
            continue
        current_parent = connection.execute(
            "SELECT * FROM task_nodes WHERE plan_id=? AND node_key=?",
            (current["id"], parent["node_key"]),
        ).fetchone()
        if (
            current_parent is None
            or current_parent["worker_id"] != parent["worker_id"]
            or current_parent["status"] != "succeeded"
        ):
            raise DomainError("Core source direct predecessor lineage changed")
        producer = CompletionGate(store)._check_live_node(
            SimpleNamespace(connection=connection), quest, current, current_parent
        )
        receipt = connection.execute(
            "SELECT r.result_json,r.execution_mode FROM action_receipts r "
            "JOIN action_intents i ON i.id=r.intent_id WHERE i.run_id=? AND i.resource_scope=? "
            "AND i.tool_name IN ('file_write','file_revise') AND i.state='succeeded' "
            "ORDER BY i.rowid DESC LIMIT 1",
            (producer["id"], str(path)),
        ).fetchone()
        if (
            receipt
            and receipt["execution_mode"] == "live"
            and json.loads(receipt["result_json"]).get("sha256") == output.get("sha256")
        ):
            return
    raise DomainError("Core source future artifact lacks a verified direct predecessor")


def verified_sources(store, connection, run, *, historical=True):
    """Reconstruct all complete sources and their immutable user block, or reject."""
    try:
        return _verified_sources(store, connection, run, historical=historical)
    except DomainError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, OSError) as exc:
        raise DomainError("Core source provenance is invalid") from exc


def _verified_sources(store, connection, run, *, historical):
    capsule = connection.execute(
        "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (run["id"],)
    ).fetchone()
    policy = json.loads(capsule[0]).get("input_acquisition_policy") if capsule else None
    plan = connection.execute(
        "SELECT graph_json FROM quest_plans WHERE id=?", (run["plan_id"],)
    ).fetchone()
    declared_policy = (
        (json.loads(plan[0]).get("capacity_contract") or {}).get("input_acquisition_policy")
        if plan
        else None
    )
    if not has_table(connection):
        if policy == POLICY or declared_policy == POLICY:
            raise DomainError("Core source admission table is unavailable")
        return []
    reads = connection.execute(
        "SELECT * FROM worker_core_source_reads WHERE run_id=? ORDER BY ordinal", (run["id"],)
    ).fetchall()
    if not reads and policy != POLICY and declared_policy != POLICY:
        return []
    approved = _approved(store, connection, run, historical=historical)
    _graph, member, policy = approved
    if policy != POLICY:
        if reads:
            raise DomainError("Legacy Worker cannot acquire Core source origin")
        return []
    if len(reads) != len(_sources(member)):
        raise DomainError("Core source inventory is incomplete")
    if not reads:
        return []
    entries = [
        _entry(store, connection, run, read, historical=historical, approved=approved)
        for read in reads
    ]
    ordinals = {read["message_ordinal"] for read in reads}
    if None in ordinals or len(ordinals) != 1:
        raise DomainError("Core source message binding is incomplete")
    message = connection.execute(
        "SELECT role,message_json FROM worker_messages WHERE run_id=? AND ordinal=?",
        (run["id"], ordinals.pop()),
    ).fetchone()
    if (
        message is None
        or message["role"] != "user"
        or json.loads(message["message_json"])
        != source_message(entries, source_wire_format=member.get("source_wire_format"))
    ):
        raise DomainError("Core source user block differs from original sources")
    return entries


def native_receipt_sources(store, connection, run):
    entries = verified_sources(store, connection, run)
    sources = []
    for entry in entries:
        if entry["tool_name"] != "file_read":
            continue
        receipt = connection.execute(
            "SELECT r.id,i.tool_name,i.resource_scope,r.result_digest,r.execution_mode "
            "FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id WHERE r.id=?",
            (entry["receipt_id"],),
        ).fetchone()
        sources.append(dict(receipt) | {"output": entry["output"]})
    return sources


def completed_delivery_sources(store, connection, run):
    return [
        {"call_id": entry["call_id"], "output": entry["output"]}
        for entry in verified_sources(store, connection, run)
        if entry["tool_name"] == "worker_read_completed_delivery"
    ]


def unresolved_sources(connection, run_id):
    """An unfinished native operation cannot be replayed after lease loss."""
    if not has_table(connection):
        return []
    rows = connection.execute(
        "SELECT * FROM worker_core_source_reads WHERE run_id=? AND state IN ('running','failed','uncertain')",
        (run_id,),
    ).fetchall()
    unresolved = []
    for read in rows:
        receipt = connection.execute(
            "SELECT 1 FROM action_receipts r JOIN action_intents i ON i.id=r.intent_id "
            "WHERE i.call_id=? AND i.run_id=? AND i.state='succeeded'",
            (read["id"], run_id),
        ).fetchone()
        if read["state"] != "running" or read["tool_name"] != "file_read" or not receipt:
            unresolved.append(read)
    return unresolved


async def acquire(manager, run_id, token):
    """Acquire each approved source once, then persist its complete lossless user block."""
    from .worker_envelope import validate_batch

    with manager.store.unit_of_work() as work:
        run = manager._fenced(work.connection, run_id, token)
        capsule_row = work.connection.execute(
            "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (run_id,)
        ).fetchone()
        if (
            capsule_row is None
            or json.loads(capsule_row[0]).get("input_acquisition_policy") != POLICY
        ):
            if work.connection.execute(
                "SELECT 1 FROM worker_core_source_reads WHERE run_id=?", (run_id,)
            ).fetchone():
                raise DomainError("Legacy Worker acquired a Core source record")
            return
        approved = _approved(manager.store, work.connection, run)
        _graph, member, policy = approved
        if policy != POLICY:
            if work.connection.execute(
                "SELECT 1 FROM worker_core_source_reads WHERE run_id=?", (run_id,)
            ).fetchone():
                raise DomainError("Legacy Worker acquired a Core source record")
            return
        sources = _sources(member)
        existing = work.connection.execute(
            "SELECT * FROM worker_core_source_reads WHERE run_id=? ORDER BY ordinal", (run_id,)
        ).fetchall()
        if existing:
            if len(existing) != len(sources):
                raise DomainError("Core source admission set changed")
            for read in existing:
                _verify_admission(manager.store, work.connection, run, read, approved=approved)
        elif sources:
            if run["tool_spent"] + len(sources) > run["tool_limit"]:
                raise DomainError("Core source original tool budget exhausted")
            capsule = json.loads(
                work.connection.execute(
                    "SELECT capsule_json FROM worker_capsules WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            )
            validate_batch(
                manager.store,
                work.connection,
                run,
                capsule,
                [{"name": tool, "arguments": arguments} for tool, arguments in sources],
            )
            work.connection.execute(
                "UPDATE worker_runs SET tool_spent=tool_spent+? WHERE id=?", (len(sources), run_id)
            )
            for ordinal, (tool, arguments) in enumerate(sources):
                identity = "core_source_" + secrets.token_hex(16)
                binding = _binding(run, member, ordinal, tool, arguments)
                work.connection.execute(
                    "INSERT INTO worker_core_source_reads(id,run_id,ordinal,tool_name,arguments_json,"
                    "binding_json,binding_digest,state) VALUES (?,?,?,?,?,?,?,'pending')",
                    (
                        identity,
                        run_id,
                        ordinal,
                        tool,
                        canonical(arguments),
                        canonical(binding),
                        digest(binding),
                    ),
                )
                work.append_event(
                    ForgeEvent(
                        event_type="worker.progress",
                        quest_id=run["quest_id"],
                        worker_id=run["worker_id"],
                        payload={
                            "phase": "core_source_admitted",
                            "run_id": run_id,
                            "source_id": identity,
                            "binding_sha256": digest(binding),
                            "ordinal": ordinal,
                            "origin": POLICY,
                        },
                    )
                )
    for ordinal in range(len(sources)):
        with manager.store.unit_of_work() as work:
            run = manager._fenced(work.connection, run_id, token)
            read = work.connection.execute(
                "SELECT * FROM worker_core_source_reads WHERE run_id=? AND ordinal=?",
                (run_id, ordinal),
            ).fetchone()
            _verify_admission(manager.store, work.connection, run, read)
            if read["state"] == "succeeded":
                _entry(manager.store, work.connection, run, read, historical=False)
                continue
            receipt = (
                _native_receipt(work.connection, run, read)
                if read["tool_name"] == "file_read"
                else None
            )
            if read["state"] != "pending" and receipt is None:
                raise DomainError("Unresolved Core source cannot be replayed")
            if receipt is not None:
                output, receipt_id = json.loads(receipt["result_json"]), receipt["id"]
                native = False
            elif read["tool_name"] == "worker_read_completed_delivery":
                from .predecessor_delivery import read_completed_delivery

                output = read_completed_delivery(
                    manager.store, work.connection, run_id, json.loads(read["arguments_json"])
                )
                receipt_id, native = None, False
            else:
                work.connection.execute(
                    "UPDATE worker_core_source_reads SET state='running' WHERE id=? AND state='pending'",
                    (read["id"],),
                )
                native = True
        if native:
            result = await manager.tools.execute(
                call_id=read["id"],
                quest_id=run["quest_id"],
                worker_id=run["worker_id"],
                run_id=run_id,
                tool_name="file_read",
                parameters=json.loads(read["arguments_json"]),
                lease_token=token,
            )
            if result.get("state") != "succeeded":
                with manager.store.unit_of_work() as work:
                    manager._fenced(work.connection, run_id, token)
                    work.connection.execute(
                        "UPDATE worker_core_source_reads SET state=? WHERE id=?",
                        ("failed" if result.get("state") == "failed" else "uncertain", read["id"]),
                    )
                raise DomainError("Core source native read did not settle successfully")
            output, receipt_id = result["output"], result["receipt_id"]
        with manager.store.unit_of_work() as work:
            run = manager._fenced(work.connection, run_id, token)
            work.connection.execute(
                "UPDATE worker_core_source_reads SET state='succeeded',receipt_id=?,result_json=?,result_digest=? "
                "WHERE id=? AND state IN ('pending','running')",
                (receipt_id, canonical(output), digest(output), read["id"]),
            )
            current = work.connection.execute(
                "SELECT * FROM worker_core_source_reads WHERE id=?", (read["id"],)
            ).fetchone()
            _entry(manager.store, work.connection, run, current, historical=False)
    if not sources:
        return
    with manager.store.unit_of_work() as work:
        run = manager._fenced(work.connection, run_id, token)
        reads = work.connection.execute(
            "SELECT * FROM worker_core_source_reads WHERE run_id=? ORDER BY ordinal", (run_id,)
        ).fetchall()
        if all(read["message_ordinal"] is not None for read in reads):
            verified_sources(manager.store, work.connection, run, historical=False)
            return
        if any(read["message_ordinal"] is not None for read in reads):
            raise DomainError("Partial Core source message checkpoint")
        entries = [
            _entry(manager.store, work.connection, run, read, historical=False) for read in reads
        ]
        ordinal = work.connection.execute(
            "SELECT coalesce(max(ordinal),-1)+1 FROM worker_messages WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        work.connection.execute(
            "INSERT INTO worker_messages VALUES (?,?,'user',?)",
            (
                run_id,
                ordinal,
                canonical(
                    source_message(entries, source_wire_format=member.get("source_wire_format"))
                ),
            ),
        )
        work.connection.execute(
            "UPDATE worker_core_source_reads SET message_ordinal=? WHERE run_id=?",
            (ordinal, run_id),
        )
        refs_row = work.connection.execute(
            "SELECT evidence_refs FROM worker_capsules WHERE run_id=?", (run_id,)
        ).fetchone()
        refs = json.loads(refs_row[0])
        refs.extend(
            {"receipt_id": entry["receipt_id"], "execution_mode": run["execution_mode"]}
            for entry in entries
            if entry["receipt_id"]
        )
        work.connection.execute(
            "UPDATE worker_capsules SET evidence_refs=?,revision=revision+1 WHERE run_id=?",
            (canonical(refs), run_id),
        )
        verified_sources(manager.store, work.connection, run, historical=False)
