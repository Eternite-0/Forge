# Phase 2 — Provider timing and request-source regressions

Date: 2026-11-15 (Asia/Shanghai). This subtask adds tests only; the integration
owner implements the production fixes. All Provider responses use HTTP
MockTransport or scripted Mock responses. No live call was made here.

`test_phase2_provider_config.py` contains 21 cases. It checks process-environment
defaults (30-second timeout, one additional retry), valid timing/retry boundaries
and malformed, non-finite, negative or excessive values. A delayed async
MockTransport handler validates both the request's actual HTTP timeout extension
and the Gateway's outer deadline. A one-second request override completes a
0.15-second reply despite a 0.05-second Provider default; without an override the
default times out and the started model attempt remains reserved with unknown
usage. MockTransport does not enforce the HTTP timeout itself, so the extension
assertion is necessary to catch the prior mismatch. This tests client behavior,
not whether a real upstream request stopped.

Seven new cases in `test_router_intelligence_phase2.py` verify that updating a
proposed goal preserves the original request ID/hash, records a separate update
ID/hash and event, and gives Planner both originals after compaction and restart.
Changing, deleting or replacing either source with an assistant message blocks
planning before the model call. Existing Router/Planner tests remain retained.

Command from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase2_provider_config.py tests/test_router_intelligence_phase2.py -q --junitxml=../docs/audit/phase2/provider-source-tests.xml
```

Result: **42 passed in 4.56 seconds**, including 14 pre-existing Router/Planner
cases. [JUnit](provider-source-tests.xml). Ruff lint and format checks pass for
both test files. These focused results overlap the earlier B/F group and must
not be added into a full-suite total. The full frozen-source result and live
failure/rerun metrics remain the root integration owner's responsibility.
