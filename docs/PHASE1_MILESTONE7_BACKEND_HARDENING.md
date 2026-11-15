# Phase 1 Milestone 7 — Backend hardening

Status: **backend hardening verified locally; M7 in progress**.
The user deferred desktop acceptance and directed this round toward backend
robustness. This record is not M7 or Alpha acceptance. The companion [verification
record](audit/phase1/milestone7-backend-hardening-verification.json) contains
the frozen-source commands and historical preservation audit.

Referenced evidence is published as byte-for-byte copies under
`docs/audit/phase1/milestone7/backend-hardening/`. Original evidence files may
refer to their local collection paths; those paths are retained as recorded.

## Scope and findings

- **Worker admission pressure and zero-POST refunds** —
  `backend/src/forge_core/worker_runtime.py` and `backend/src/forge_core/providers/__init__.py`:
  transient local capacity pressure previously could block work or consume an
  attempt; it now persists a queued retry and refunds Worker reservations when
  no POST was issued. Unknown sent outcomes stay reserved and an old owner
  cannot refund a newer owner's reservation. Tests cover cancellation, restart,
  busy capacity and ownership.
- **Retry count** — `backend/src/forge_core/worker_runtime.py`: lease generation changes for fencing
  no longer exhaust the semantic failure-retry limit. Failure retry events are
  counted separately from authorization retries; limits and unresolved-action
  checks remain in force.
- **Cancellation regression:** preserve the original test's cancellation branch
  and assertions when expanding admission-pressure coverage. This is a test
  preservation requirement; the final historical audit found no missing original test.
- **Reviewer evidence** — `backend/src/forge_core/worker_runtime.py`,
  `backend/src/forge_core/tool_domain.py`, `backend/src/forge_core/evidence.py`:
  previously no governed receipt read was available to a dependent reviewer.
  Reads are restricted to approved direct predecessors, check plan and role
  scopes and preserve source execution mode; tool results and producer/reviewer
  message bindings are rechecked. References are bounded and content remains
  untrusted. `backend/tests/test_receipt_read_m7.py` exercises this boundary.
- **Core lifecycle recovery** — `backend/src/forge_core/lifecycle.py`,
  `backend/src/forge_api/main.py`, `backend/src/forge_api/desktop_host.py`,
  `backend/tests/test_lifecycle_m7.py` and
  `backend/tests/test_startup.py`: persisted start/stop and interrupted
  generations provide evidence for restart inspection. The public unauthenticated
  health response exposes liveness and challenge only; authenticated health also
  returns Router identity. A restart test now isolates business-event counts from
  lifecycle events.
- **Alpha checkpoint audit** — `backend/scripts/run_alpha_demo.py` and
  `backend/tests/test_alpha_demo_m7.py`: the checkpoint previously lacked a
  durable binding between restart observations and the intended task. It now
  binds a real persisted `core.started` generation, continuation of the same
  Quest and a subsequent `worker.result` after restart. The bounded tests are
  separate from the unperformed complete six-role Mock and paid Alpha scenarios.
- **Router Quest binding** — `backend/src/forge_core/router.py`,
  `backend/tests/test_persistent_router.py`: previously
  a caller had to infer a created Quest from a concurrent list difference. New
  turn results persist the actual new or explicitly
  targeted Quest ID with the turn in one transaction; chat, ambiguous and
  global-status turns have no target. Same-request retries return the stored
  result. Legacy stored turns remain unchanged and do not acquire a guessed ID.
- **Semantic evidence after interpreter relocation** — `backend/src/forge_core/semantic_evidence.py`,
  `backend/tests/test_semantic_evidence_m7.py`: historical revalidation previously depended on
  the current interpreter's absolute path. New semantic checks record
  versioned Core provenance, including the actual executable digest and command,
  suite, source, report and run bindings. Revalidation does not depend on the
  current interpreter path. Old records without proof fail closed. This record
  uses the existing trusted-database boundary; it is not cryptographic protection
  against an actor who can arbitrarily rewrite the database and all bindings.

## Verification and remaining evidence

On the frozen v3 source, from the repository root, the command
`.\backend\.venv\Scripts\python.exe -m pytest backend/tests -c backend/pyproject.toml -q`
completed with **488 passed, 1 warning in 107.62 seconds, exit 0**. The test child
environment had `FORGE_*` variables removed. The warning is Starlette TestClient's
`httpx` deprecation. The complete output is in
`docs/audit/phase1/milestone7/backend-hardening/backend-hardening-full-pytest-v3.log`.

An intermediate targeted Router and semantic suite run reported **36 passed**
and exit code 0; the associated Ruff check exited 0. The backend hardening
candidate run recorded **61 passed** in 25.30 seconds (exit 0) in
`docs/audit/phase1/milestone7/backend-hardening/hardening-candidate-targeted.log`; the separate
Alpha/receipt/lifecycle targeted run recorded **25 passed, 1 warning** in 8.46
seconds in `docs/audit/phase1/milestone7/backend-hardening/alpha-receipt-lifecycle-final.log`.
From `backend`, the 61-test command was `.\.venv\Scripts\python.exe -m pytest
tests/test_receipt_read_m7.py tests/test_worker_runtime.py
tests/test_desktop_host_m7.py tests/test_lifecycle_m7.py -q`. The 25-test command
was `.\.venv\Scripts\python.exe -m pytest tests/test_receipt_read_m7.py
tests/test_alpha_demo_m7.py tests/test_lifecycle_m7.py
tests/test_semantic_suite_m7.py -q` from the same directory.
These targeted results do not demonstrate a single complete six-role Mock chain
with source approval, Reviewer and restart. They predate the final combined
source and do not substitute for the full suite. `test_receipt_read_m7.py`
sets a scripted model stub's execution mode to `live` to exercise the gate; it is
not a real-provider call. Mock HTTP transports, real SQLite transactions and real
local subprocesses are distinct forms of evidence. Earlier failing
regressions and diagnostic outputs remain in the development tool transcript;
do not delete or overwrite retained logs.

On the v3 source snapshot, root and backend-directory Ruff checks, both Ruff
format checks (109 files each), and `pip check` all exited 0. Exact commands and
output are in `docs/audit/phase1/milestone7/backend-hardening/backend-hardening-static-v3.log`.

The first full backend run **failed: 6 failed, 482 passed, 1 warning in 117.50
seconds, exit code 1**. Its original log is retained at
`docs/audit/phase1/milestone7/backend-hardening/backend-hardening-full-pytest.log`. Three
cross-origin tests assumed an empty event log despite lifecycle events; two
transaction tests installed failure triggers before application lifespan; the
restart test counted all events and outbox rows as business events, although two
`core.started` events increased the total from two to four. Using the authenticated
conversation endpoint to read identity is a separate test change, not the failure
cause. This earlier result was not a passing full suite; subsequent v3 results
are recorded above.

The next full run reported **488 passed, 1 warning in 115.49 seconds, exit 0**
(`docs/audit/phase1/milestone7/backend-hardening/backend-hardening-full-pytest-v2.log`), but its separate format check failed on
two test files (`docs/audit/phase1/milestone7/backend-hardening/backend-hardening-static-v2.log`, exit 1). Those two files were
formatted for v3; `docs/audit/phase1/milestone7/backend-hardening/format-ast-before.txt` and `docs/audit/phase1/milestone7/backend-hardening/format-ast-after.txt`
record their unchanged AST. The v2 result is not the final combined result.

The final preservation comparison reports **367 historical reports/evidence/ADR
files with no hash changes** and **282 original test functions with none
missing**. Seven assertion cases were inspected:

- `test_migrations.py::test_each_migration_step`: expected schema version 8 → 9.
- `test_migrations.py::test_each_migration_failure_rolls_back_a_fresh_database`:
  expected schema version 8 → 9.
- `test_migrations.py::test_v8_component_failure_rolls_back_v7_data`: expected
  schema version 8 → 9 after the full chain completes; the rollback assertion
  remains part of the test.
- `test_api_trust.py::test_approval_and_grants_http_are_user_controlled`: an
  approval decision missing the now-required digest and plan revision returns
  422 validation error rather than 409; this reflects the stricter input contract.
- `test_security_adversarial.py::test_cross_origin_simple_post_cannot_create_worker`:
  checks request-scoped state and complete lifecycle events instead of assuming
  an empty event/outbox list.
- `test_transactions.py::test_worker_proposal_and_event_failure_rolls_back`:
  isolates failure triggers from lifespan and verifies lifecycle events/outbox.
- `test_startup.py::test_core_launcher_restart_preserves_workspace_and_rejects_stale_state`:
  distinguishes two business events from two startup events and checks outbox mappings.

The first preservation scan detected a missing original cancellation test; its
failure remains in `docs/audit/phase1/milestone7/backend-hardening/backend-hardening-preservation.json`. The test was restored,
and the v3 scan is in `docs/audit/phase1/milestone7/backend-hardening/backend-hardening-preservation-v3.json`. The recorded
112-file source manifest SHA-256 is
`377a63dfc99291296dc41ed4ae2eafb77397954f2b9cc3ca8e646693a81c6f50`; the M7
baseline manifest SHA-256 is
`cb8911cf1e7d7f28a4dc63eb6845c345f55e8d0d2653aae59109e9c5b5891555`.
Post-test integrity verification exited 0 and confirmed all 112 source files and
367 historical files unchanged; see `docs/audit/phase1/milestone7/backend-hardening/backend-hardening-posttest-integrity.json`.
These checks apply to the v3 frozen source snapshot.

The single six-role source-approval, Reviewer and restart Mock chain remains
unverified. The paid Alpha, desktop acceptance and NSIS installer acceptance have
not been performed in this round. Prior M5/M6 bounded live evidence remains
described in its original reports.
