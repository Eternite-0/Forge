# V8 continuity and live failure audit

This is a read-only inspection of frozen v8 source and retained live evidence. No source, runtime state, approval, receipt or historical run was changed; no provider request was made. The frozen manifest is `92bb6067ff7bc1dadfa6aa9b5994089969603b53b3980a9a5601d0a140e6b9e3` (156 files, `source-manifest-v8.json`). This audit does not add acceptance requirements.

## What the continuity harness actually checks

`backend/scripts/phase2/live_e2e.py:328` implements a real, quiescent restart during a Quest. The normal trigger at line 718 requires a completed node and remaining nodes. It pauses the Quest through authenticated plan control, waits until no model attempts, governed actions or Workers remain in flight, stops Core, compacts Router context, starts Core against the same workspace, observes Router identity, compares before/after durable anchors and resumes through authenticated control. It refuses a restart with unresolved execution. This establishes the intended scope of the experiment; it makes no claim about arbitrary crashes during unresolved external effects.

`continuity_snapshot` at line 201 reads SQLite in `mode=ro` within a transaction. Its anchors include node identities/status, action-receipt digests/mode, model-attempt states/charged usage and Worker token/step/tool counters. `compare_continuity` at line 315 requires an actual succeeded node rather than a vacuous empty set, and compares the retained anchors.

`backend/scripts/phase2/acceptance_checks.py:466` independently rechecks the recorded before/after snapshots against final durable rows. It requires both completed and remaining work, quiet snapshots, retained receipts and settled/rejected attempts, nondecreasing final Worker counters, matching plan/node identities and successful resume. `_continuity` at line 563 compares both recorded Router identities with the actual stored identity. Missing observations remain unknown; a pre-execution restart does not satisfy the mid-execution scope.

`compact_stopped_core` at `live_e2e.py:165` calls the actual `ContextService.compact_router(keep_recent=1)`, rebuilds Router context and reads every archived source through `read_source`. Each restored complete message row must equal its original database row. The final evaluator checks that a real archive and rebuilt capsule exist, rereads source hashes and requires this Quest's integrity-bound original user message among the verified sources. A no-op archive or invented source cannot pass. Existing driver/evaluator tests explicitly exercise these negative cases; they are test fixtures, not substitute live evidence.

`ContextService.compact_router` and `rebuild_router` at `backend/src/forge_core/context_service.py:328` and line 426 preserve source rows, verify archive/source integrity, update the cursor transactionally and refresh active task/constraint context. Worker context compaction is a separate path from the stopped-Core Router compaction experiment.

## Actual frozen-v8 live observations

The three retained records are `.local/phase2/runs/20261115T020604Z-L1-dceb58/run.json`, `20261115T020604Z-L2-7f6370/run.json` and `20261115T020604Z-L3-30acf0/run.json`. All three failed acceptance. No downstream Worker started in L2 or L3, and no required Core restart/stopped-Core Router compaction was reached.

| Run | Actual blocking condition | Worker counters | Continuity evidence |
| --- | --- | --- | --- |
| L1, 136.466 seconds | `worker.blocked` seq 63, `tool_budget`, after independent `NEEDS_REVISION` | 12/16 steps, 14/16 tools, 114,794/160,000 Worker tokens, 1 repair | Two actual Worker archives, containing 17 and 6 source messages; task still failed. No Core restart/Router compaction observation. |
| L2, 99.597 seconds | `worker.blocked` seq 61, `tool_budget`, on first `research-router-worker` node after independent `NEEDS_REVISION` | 11/16 steps, 15/16 tools, 83,018/110,000 Worker tokens, 1 repair | Four actual Worker archives, containing 9, 6, 6 and 4 source messages; task still failed. No successor or Core restart. |
| L3, 103.132 seconds | `review.blocked` seq 30, generic `review_contract_or_budget_failed`; direct read-only `_packet` reconstruction identifies `DomainError("Artifact cannot be read")` | First `research` node remains `review_pending`, 6/16 steps, 6/16 tools, 50,827/120,000 Worker tokens, 1 repair | No Worker archives, successor, Core restart or Router compaction. Required continuity checks correctly remain unknown. |

### L2: actual revision loop, not a predecessor failure

The initial research node successfully read the manifest and Router/Worker excerpts and wrote `work/l2-router-worker.json` (10,521 bytes). Independent review requested factual/citation corrections and a more compact artifact consistent with the approved objective. After that review the Worker repeatedly reread its artifact and the two original excerpts, with one additional manifest read. It never called `file_revise` and emitted no revised delivery. The tool-budget guard at `backend/src/forge_core/worker_runtime.py:2267` rejects a response when its requested tool batch exceeds the remaining allowance; the exact rejected batch is not persisted, so this audit does not assign it an invented count.

Every later node remained `proposed`. The live evidence therefore cannot establish whether a successor would receive or use predecessor evidence. It also does not establish the cause of the repeated-read behavior merely from the existence of Worker archives.

### L3: approved deliverable/tool mismatch

The model-generated approved research node explicitly says to write `work/l3-research.json` and has `json_review:work/l3-research.json` plus that expected artifact. Its approved node tools are only `file_read` and `directory_list`. Six successful calls consist of manifest/excerpt reads and directory listings; the workspace `work/` directory contains no output file. Fresh read-only packet construction fails at actual artifact readback, before packet/context reservation is evaluated. This failure is not attributed to Reviewer context size or budget.

The unexecuted mathematics and architecture nodes also require writing intermediate JSON while their node tools contain only `file_read` and `worker_read_action_receipt`. The unexecuted review node asks to record `work/l3-review.json` without a writing tool. These are retained plan/tool-contract inconsistencies; this audit makes no change to them.

### Usage accounting remains incomplete where the records say so

L1 and L2 each retain one unresolved `router_notice` model-budget attempt when the driver stopped the blocked frontier. Their charged sums are respectively 128,890 input / 14,443 output and 109,862 input / 9,567 output, with `unknown_attempts=1` in each record. The unresolved reservations are L1 10,555 input + 4,096 output and L2 10,290 input + 4,096 output. These are reservations, not actual charged usage. Scenario total-token/accounted-usage checks correctly remain unknown; settled sums cannot prove total actual usage or cost. L3 retains 84,067 input / 12,506 output and zero unknown attempts.

## Predecessor evidence implementation versus observation

`backend/src/forge_core/worker_runtime.py:572` exposes bounded references to successful direct-success-edge predecessor receipts only when the new Worker's approved tools permit `worker_read_action_receipt`. The prompt and capsule both contain those references. `_read_action_receipt` at line 87 verifies same Quest/plan/revision, successful producer/node/intent/call, the actual direct edge, historical tool authority, receipt/argument/result digests and the original tool-message correspondence. It returns the verified provenance with explicitly untrusted output content. The reference envelope is bounded to 32 items/8 KiB; individual receipt readback is bounded to 8 KiB. A compact write receipt can identify a larger artifact for a separately governed `file_read` when permitted.

This is an implemented transfer mechanism with explicit bounds. The new L2/L3 runs provide no live successor observation because both stop at the first node. No claim of a demonstrated predecessor-evidence failure or successful cross-node handoff is made from these runs.
