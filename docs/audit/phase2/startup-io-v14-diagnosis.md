# Windows startup test diagnosis

The unchanged original `test_core_launcher_restart_preserves_workspace_and_rejects_stale_state`
failed twice while reopening its database after terminating two Core child
processes. The failure occurred in the parent's `PRAGMA journal_mode=WAL` with
`sqlite3.OperationalError: disk I/O error`. The original traces do not retain the
extended SQLite error code, so the exact filesystem or shared-memory operation
remains unknown.

The retained database subsequently opened in both read-only and read-write mode;
`quick_check` returned `ok`. Free disk space was sufficient. These observations
do not prove that the earlier I/O operation succeeded or identify its cause.

Additional diagnostics used new isolated directories and the unchanged original
test. Direct invocations in D: and C: temporary directories passed. One genuine
pytest invocation with in-memory error-code instrumentation failed earlier during
the startup HTTP poll with `ConnectionResetError` / Windows error 10054. Two
subsequent instrumented pytest invocations passed, and a final invocation using
only a temporary `pytest_exception_interact` reporting plugin passed. The plugin
did not patch Store, SQLite or the test.

| Record | Outcome |
| --- | --- |
| `frozen-tests-v13.xml` | Original startup I/O failure retained; full run had 976 passed, two failed |
| `v13-failure-regressions-v14.xml` | Original startup I/O failure retained; 21 other cases passed |
| `startup-io-v14-instrumented-tests.xml` | Startup HTTP reset, one failed, 2.91 seconds |
| `startup-io-v14-instrumented-repeat.xml` | One passed, 3.74 seconds |
| `startup-io-v14-instrumented-repeat2.xml` | One passed, 2.47 seconds |
| `startup-io-v14-plugin-tests.xml` | One passed, 2.51 seconds |

No source, original test, historical database, WAL/SHM sidecar or Provider setting
was modified. No Provider credential was read and no live model was called. New
Core processes used the original test's explicitly labelled mock Provider.

The opening failure was not reproduced with an extended SQLite code. No generic
I/O retry or test relaxation is proposed on this evidence. Keep the original
startup test as a final full-suite gate and retain the unresolved intermittent
Windows startup failure in the acceptance limitations. Passing these reruns does
not establish a source-level fix.
