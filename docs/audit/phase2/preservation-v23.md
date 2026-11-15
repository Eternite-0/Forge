# Phase 1 preservation audit against the original HEAD

Base: `bcc3592bfa820054079419456f3ba24afc81f785`. Mode: local, static, read-only; no Provider calls.

All **355 Phase 1 evidence files** remain byte-identical to Git HEAD, including the 340-file earlier subset and 15 hardening records. No file is missing or added in that directory. The count difference is explicit: 14 files under `milestone7/backend-hardening/` plus its aggregate verification.

All **364 original test function identities** remain. 349 have identical AST; 15 changed. No original function gained a skip/xfail decorator. Nine original Phase 1 report files also remain byte-identical.

Changed functions retain the original assertions except the explicitly listed replacements below. These changes are not described as byte-identical tests or proven semantic equivalence. Static preservation does not replace the final frozen-source test run.

| Original test | Assertion replacements and rationale |
| --- | --- |
| `backend/tests/test_live_context_probe_offline.py::test_probe_fixtures_restart_and_report_with_mock_gateway` | 2 removed AST forms, 3 added; Offline sourced-context fixture uses a finite 75000 ceiling in both branches. Invalid schema now spends the bounded second repair call; every attempt must settle. Original status, constraint, fixture-count, response, failure-code and redaction checks remain. |
| `backend/tests/test_migrations.py::test_each_migration_step` | 1 removed AST forms, 1 added; Expected migrated schema follows the current version; exact assertion changes are recorded; rollback/data-preservation assertions remain. |
| `backend/tests/test_migrations.py::test_each_migration_failure_rolls_back_a_fresh_database` | 1 removed AST forms, 1 added; Expected migrated schema follows the current version; exact assertion changes are recorded; rollback/data-preservation assertions remain. |
| `backend/tests/test_migrations.py::test_v8_component_failure_rolls_back_v7_data` | 1 removed AST forms, 1 added; Expected migrated schema follows the current version; exact assertion changes are recorded; rollback/data-preservation assertions remain. |
| `backend/tests/test_worker_runtime.py::test_model_can_request_bounded_help_without_spawning_or_gaining_tools` | 1 removed AST forms, 1 added; Settled help usage is exactly the fixture's 10 input + 5 output tokens instead of a stale reservation >15. Blocked state, zero executed tools, one Worker and one help event remain. |
| `backend/tests/test_workspace_backup_m7.py::test_v7_snapshot_is_migrated_in_staging_before_publish` | 1 removed AST forms, 1 added; Expected migrated schema follows the current version; exact assertion changes are recorded; rollback/data-preservation assertions remain. |

Other function changes update executable Mock Planner criteria, wait for the actual Planner lease, drain finite Dispatcher fixtures, or reconstruct older schema fixtures. Their full identities and exact assertion texts appear in the paired JSON.

Source manifest `docs/audit/phase2/source-manifest-v23.json` contains 231 files; changed paths at audit time: `[]`. This audit writes only new files under the Phase 2 audit directory. It does not modify historical evidence, application code or tests.

Reproduce from the repository root with a new output path:

```powershell
& backend/.venv/Scripts/python.exe docs/audit/phase2/audit_preservation_v6.py --base bcc3592bfa820054079419456f3ba24afc81f785 --manifest docs/audit/phase2/source-manifest-v23.json --output docs/audit/phase2/preservation-recheck.json --markdown docs/audit/phase2/preservation-recheck.md
```

Outputs use exclusive creation and refuse overwriting an existing audit record.
