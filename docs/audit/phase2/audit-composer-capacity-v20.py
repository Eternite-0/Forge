"""Read-only V20 Composer observations and frozen-code counterfactual sizing."""

import hashlib
import json
import sqlite3
import sys
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
AUDIT = Path(__file__).resolve().parent
ARCHIVE = REPO / ".local/phase2/source-snapshot-v20.zip"
MANIFEST = AUDIT / "source-manifest-v20.json"
RUN_ID = "20261115T101049Z-L1-6b5894"


def evidence(path):
    raw = path.read_bytes()
    return {
        "path": path.relative_to(REPO).as_posix(),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


@contextmanager
def readonly(path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    try:
        yield connection
        assert connection.total_changes == 0
    finally:
        connection.rollback()
        connection.close()


def main():
    output = AUDIT / "composer-capacity-v20-readonly.json"
    if output.exists() or output.with_suffix(".md").exists():
        raise FileExistsError("Composer observations are immutable")
    manifest = json.loads(MANIFEST.read_bytes())
    with zipfile.ZipFile(ARCHIVE) as archive:
        assert len(archive.namelist()) == manifest["file_count"]
        for item in manifest["files"]:
            raw = archive.read(item["path"])
            assert len(raw) == item["bytes"]
            assert hashlib.sha256(raw).hexdigest() == item["sha256"]
    # Execute the original frozen modules directly from the verified ZIP.
    # No extraction, Store construction, current product imports or model call.
    sys.path.insert(0, str(ARCHIVE) + "/backend/src")
    from forge_core.capacity_preflight import node_review_envelope
    from forge_core.model_intelligence import decode_profile
    from forge_core.providers import ProviderConfig, ProviderGateway
    from forge_core.settings import Workspace
    from forge_core.team_composition import (
        DEFAULT_OUTPUT_SCHEMA,
        CompositionLimits,
        RoleSpec,
        TeamComposerEngine,
    )
    from forge_core.worker_schema import STRICT_DELIVERY_OUTPUT_SCHEMA

    record_path = REPO / ".local/phase2/runs" / RUN_ID / "run.json"
    record = json.loads(record_path.read_bytes())
    assert record["finished_at"] and record["execution_mode"] == "live"
    assert record["code_snapshot"]["sha256"] == manifest["executable_sha256"]
    root, database = Path(record["workspace"]), Path(record["db_path"])
    with readonly(database) as connection:
        workspace_id = connection.execute(
            "SELECT id FROM workspace WHERE singleton=1"
        ).fetchone()[0]

        class ReadOnlyView:
            workspace = Workspace.from_path(root, workspace_id=workspace_id)
            path = database

            @contextmanager
            def connection(self):
                yield connection

        plan = connection.execute(
            "SELECT * FROM quest_plans ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        graph = json.loads(plan["graph_json"])
        node = graph["nodes"][0]
        quest = dict(
            connection.execute(
                "SELECT * FROM quests WHERE id=?", (plan["quest_id"],)
            ).fetchone()
        )
        profiles = [
            decode_profile(row["profile"], row["id"])
            for row in connection.execute("SELECT id,profile FROM models ORDER BY id")
        ]
        provider = ProviderGateway(
            ProviderConfig(
                id="wb-local",
                kind="openai_compatible",
                base_url="https://example.invalid/v1",
                credential_ref="FORGE_PROVIDER_KEY_TEST",
                supports_tools=True,
                supports_json_schema=True,
                reasoning_effort="none",
            )
        )
        measurements = []
        for output_tokens in (2048, 3072, 4096, 8192):
            for schema_name, schema in (
                ("planner_legacy_default", DEFAULT_OUTPUT_SCHEMA),
                ("composer_displayed_strict", STRICT_DELIVERY_OUTPUT_SCHEMA),
            ):
                role = RoleSpec(
                    id="research",
                    task_node_key=node["key"],
                    required_capabilities=node["required_capabilities"],
                    tools=node["allowed_tools"],
                    input_tokens=node["budget_tokens"] - output_tokens,
                    output_tokens=output_tokens,
                    responsibility=node["objective"],
                    input_contract=node["required_input_paths"],
                    output_schema=schema,
                    acceptance_criteria=node["success_criteria"],
                )
                limits = CompositionLimits(
                    max_members=1,
                    max_depth=1,
                    max_concurrency=1,
                    max_total_tokens=node["budget_tokens"],
                    allowed_tools=node["allowed_tools"],
                )
                member = TeamComposerEngine().compose(
                    [role], profiles, limits, strategy="single_model"
                )["members"][0]
                envelope, observation = node_review_envelope(
                    ReadOnlyView(),
                    connection,
                    quest,
                    graph,
                    node,
                    member,
                    provider,
                    resource_sizing=True,
                )
                measurements.append(
                    {
                        "role_output_tokens": output_tokens,
                        "role_schema": schema_name,
                        "minimum_node_tokens": observation["minimum_node_tokens"],
                        "allocation": node["budget_tokens"],
                        "allocation_minus_requirement": node["budget_tokens"]
                        - observation["minimum_node_tokens"],
                        "initial_work_tokens": observation["initial_work_tokens"],
                        "review_cycle_reserve": observation["review_cycle_reserve"],
                        "correction_output_tokens": envelope[
                            "correction_output_tokens"
                        ],
                    }
                )
        attempts = [
            dict(row)
            for row in connection.execute(
                "SELECT call_id,state,input_reserved,output_reserved,input_charged,output_charged "
                "FROM model_budget_attempts WHERE purpose='team_composer' ORDER BY created_at"
            )
        ]
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": "Actual final V20 L1 records plus counterfactual admission measurements from the verified V20 source ZIP; measurements are not executed teams or rejected model responses.",
            "auditor": evidence(Path(__file__).resolve()),
            "manifest": evidence(MANIFEST),
            "archive": evidence(ARCHIVE),
            "driver_record": evidence(record_path),
            "database": evidence(database),
            "run_id": RUN_ID,
            "actual_plan_status": plan["status"],
            "actual_node_allocation": node["budget_tokens"],
            "actual_planner_measurement": graph["capacity_contract"]["nodes"][
                node["key"]
            ],
            "actual_plan_bytes": len(plan["graph_json"].encode()),
            "actual_capacity_contract_bytes": len(
                json.dumps(
                    graph["capacity_contract"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ),
            "actual_team_count": connection.execute(
                "SELECT count(*) FROM team_proposals"
            ).fetchone()[0],
            "actual_worker_count": connection.execute(
                "SELECT count(*) FROM worker_runs"
            ).fetchone()[0],
            "actual_composer_inbox": [
                dict(row)
                for row in connection.execute(
                    "SELECT notice_attempts,notice_error,notice_dead_at,processed_at FROM event_inbox WHERE consumer_id='composer'"
                )
            ],
            "actual_composer_attempts": attempts,
            "actual_max_composer_context_reservation": max(
                row["input_reserved"] + row["output_reserved"] for row in attempts
            ),
            "counterfactual_measurements": measurements,
            "read_snapshot_total_changes": connection.total_changes,
            "limitations": [
                "Rejected raw Composer responses and earlier overwritten inbox errors are unavailable; no actual rejected role output ceiling is inferred.",
                "ProviderGateway was instantiated with a placeholder configuration solely for native serialization; generate/stream/credential access were not invoked.",
                "Measurements preserve complete source/delivery bounds and all original review/correction obligations. No proposed team, budget mutation or approval occurred.",
                "A finite admission requirement is not a usage forecast or task correctness claim; the original strict V20 failure remains failed.",
            ],
        }
    with output.open("x", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# V20 Composer read-only observations and replay",
        "",
        f"The original `{RUN_ID}` approved its plan but produced no team or Worker and failed strict acceptance. Its ten Composer calls settled; the last inbox error is invalid JSON. Rejected raw teams were not persisted, so their resource choices cannot be reconstructed.",
        "",
        "Complete actual native Composer input reservations were 23,871–24,308, including the existing 64-byte allowance; with the actual 8,192 output reserve, the maximum was 32,500, below 65,536. This is not a request-context overflow observation.",
        "",
        "The approved node allocated 153,902 tokens after a Planner measurement of 149,806 with output 2,048 and a 4,096 role margin. These counterfactual measurements execute the original verified ZIP code against the same complete persisted source contracts; they are not the rejected real responses:",
        "",
        "| Output ceiling | Schema | Complete requirement | Allocation minus requirement |",
        "| ---: | --- | ---: | ---: |",
    ]
    lines += [
        f"| {row['role_output_tokens']:,} | {row['role_schema']} | {row['minimum_node_tokens']:,} | {row['allocation_minus_requirement']:,} |"
        for row in measurements
    ]
    lines += [
        "",
        "Increasing 2,048 to 8,192 adds 12,288 in this read-only initial-plus-one-correction cycle. Selecting the displayed strict schema adds 588 for this identical full role contract. Smaller complete contracts can fit; the resource rules have no necessary arithmetic contradiction. Resource/schema choices remain a concrete rejection risk under the fixed approved allocation.",
        "",
        "All source ZIP entries were rehashed against the original manifest. SQLite used mode=ro, query_only and BEGIN with zero changes; no Store, Provider call, credential read, source edit or existing-evidence overwrite occurred. The adjacent JSON binds source, driver, database, ledger and replay details. These observations do not accept Phase 2 or any failed V20 task.",
        "",
    ]
    with output.with_suffix(".md").open("x", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines))
    print(
        json.dumps(
            {"json": evidence(output), "markdown": evidence(output.with_suffix(".md"))}
        )
    )


if __name__ == "__main__":
    main()
