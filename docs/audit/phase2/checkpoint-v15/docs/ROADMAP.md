# Detailed Implementation Roadmap

Current acceptance — v15, 2026-11-15: **1,031 backend tests passed**, one
existing TestClient warning, 227.13 seconds. Ruff, formatting of 164 Python files,
dependency and whitespace checks passed on the unchanged 167-file frozen source.
The three current-source strict live runs **all failed**: L1 requested help and
its approved concurrency exceeded the scenario cap; L2 lacked cumulative review budget;
L3 revised after real NEEDS_REVISION, then exhausted review capacity. Neither
complex run reached its mid-execution restart. Phase 2 remains incomplete.
See [this round's evidence](audit/phase2/acceptance-v15.md) and
[machine-readable verification](audit/phase2/verification-v15.json).
The following earlier-round results are historical; the original v12 verification
and documents remain byte-identical under `audit/phase2/checkpoint-v12/`.


## Current Phase 2 Agent Intelligence sprint — 2026-11-15

Phase 2 is in progress; the requested complex live-task acceptance is pending.
This sprint's Phase 2 label is distinct from the original feature-phase numbering
retained below. See [Phase 2 status](PHASE2_STATUS.md), [report](PHASE2_AGENT_INTELLIGENCE_REPORT.md)
and [experiments](PHASE2_EXPERIMENTS.md).

- A: Audit the executable chain and probe the existing Provider/Gateway. Recorded: 11/11 small live protocol cases with the proxy default; 10/11 with explicit `reasoning_effort=none`, retaining a failed exact-text dialogue check. The setting does not prove upstream internal reasoning behavior; actual upstream identity, service maxima and price remain unknown.
- B: Separate Router/Planner, preserve original request/constraint sources and bound repair/DAG generation. Implemented with local/Mock coverage; complex live planning acceptance pending.
- C: Reuse bounded composition strategies and source online performance into new proposals. Local/Mock and synthetic comparisons recorded; general live-quality improvement unproven.
- D/E: Connect native repair, independent fresh-context review, concrete bounded revision and evidence-gated completion. Explicitly approved own-artifact `file_revise`, lossless full-source review inputs, labelled Core parser/fixed-suite feedback, idempotent persisted-feedback recovery, settled-truncation review retry and fenced between-step lease renewal now have local/Mock coverage. Original packet/PASS bindings, context limits and source/authority/budget/unresolved-outcome guards remain enforced. V12 frozen backend checks passed; final complex live failure/rework acceptance remains unmet.
- General code execution/editing remains open: current Workers have fixed diagnostics, syntax checks, numeric AST and create-only `file_write`; `file_revise` is limited to the same Worker's latest proven artifact with immutable history. General repository editing/test execution and safe running-DAG replanning require further work; current team replacement retains the existing DAG.
- F/G: Bound context highlights while retaining original-source recovery; project typed Agent messages over the existing durable transport and preserve conservative recovery. Semantic retrieval, exhaustive message payload schemas and arbitrary in-flight recovery remain open.
- H: One strict L1 live run passed with review revision and Worker self-repair, without compaction or restart. The retained L2 attempt failed at review scope/context and L3 before team/Worker execution. Later L2/L3 reruns also failed before review/restart at raw/context/cumulative-token limits; L2 declared concurrency three above cap two, while L3 produced a legal seven-role/concurrency-two team. V5 passed 820 tests / one warning / 219.96 seconds before v6 changes. V6 full testing retained 848 passed, one failed, one warning / 283.77 seconds: Worker concurrency two was incorrectly tied to Gateway concurrency one. All three v6 live reruns failed at original repair or review capacity limits. V7 restores independent Worker/Gateway limits, extends soft guidance to delivery handoffs, centralizes citations and guides cumulative review allocation without expanding original authority. V7 full testing passed 849 / one warning / 216.87 seconds, while all three live attempts failed: L1 truncated output/repair exhaustion, L2 missing required specialists plus context limit, L3 real NEEDS_REVISION followed by ten-step exhaustion. V8 removes the 2048 preference, preserves whole-task/specialist scope, strengthens explicit-null native delivery and configures shared repair max four within original limits. V8 full testing passed 886 / one warning / 283.46 seconds on 156 files, while all three live attempts failed: L1/L2 reviewed then reread files to tool-budget exhaustion, with real Worker archives but one unsettled Router notice each; L3 had a required file artifact with read-only tools and no artifact. V9 rejects generated artifact/tool mismatches and guides remaining-budget/archive reuse with bounded batch repair; limits remain unchanged. V9 full testing passed 895 / one warning / 237.83 seconds on 157 files, with Ruff/154-file formatting/pip checks passing. V9 L2/L3 still failed before any review call: actual 15,235/10,788-byte artifacts yielded 68,207/68,967-byte raw packets above the original 65,536 limit; no successor/restart ran, and L3 retains one unresolved Router notice. All 39 finished driver records are published in snapshot-v2 with original failures preserved. V9 budget hints only apply at per-request allowance ≥8192. V10 full testing passed 915 / one warning / 279.54 seconds on 158 files, with Ruff/149-file formatting/pip/diff passing; its three strict live attempts still failed. L2 did show actual research PASS/succeeded, settled mid-execution Core restart with two-source Router archive/readback and preserved identity/receipts/counters, and continuation of an architect already started before pause; it later blocked on an 11,211-byte predecessor receipt above the original 8192 cap. L1 used a plan ID as receipt ID; L3 revised after real NEEDS_REVISION, then Core safely BLOCKED a 70,657-byte packet without enough original capacity-repair budget. All three final unknown counts are zero. Forty-two driver records are published in snapshot-v3. V11 bounded local receipt-read correction and Planner operator-budget enforcement passed full testing: 941 / one warning / 199.39 seconds, manifest eec443f0…/160 files, Ruff/157-file formatting/pip/diff passing. L1 strict passed after two real review revisions. L2 research/analysis revisions passed, actual report existed, and preserved restart/Router archive/readback continued through successors, but final review Worker hit context_budget after two steps; the first whole-tail archive lacked a readback tool declared in that request. L3 was falsely blocked by summing serial reservation floors (76,891) instead of checking the largest single floor (49,827) against 70,248 remaining; this was not actual Gateway budget exhaustion. All three final unknown counts are zero, and snapshot-v4 publishes 45 finished driver records. V12 corrected serial-capacity/first-archive logic passed the frozen 955-test suite / one warning / 251.43 seconds, manifest ccc5efe6…/162 files, Ruff/159-file formatting/pip/diff. Its L1 strict run passed after real revision; L2 preserved another actual PASS/restart/readback/successor artifact but the draft lacked complete review budget; L3 revision enlarged artifact/delivery, leaving 40,238 below the corrected 51,105 native floor, so Core BLOCKED. All three final unknown counts are zero. Snapshot-v5 publishes 48 records. Two further same-source L2/L3 samples also failed; snapshot-v6 publishes 50 records and preserves all predecessors. The last L3 research passed, retained a one-source Router archive/readback and original identity/receipts/counters through a settled mid-Core restart, and continued the already-started math successor; its remaining 40,038 could not fit 39,871 native input plus minimum verdict 1,024. This demonstrates a bounded continuity segment, while the core complex-task goal remains unmet. Nine historical unresolved attempts are a known lower bound, with six earlier records missing usage statistics, so true aggregate usage/cost remain unknown. The v12 verification record preserves measured partial state, not Phase 2 completion. Proposed next work is complete Core capacity preview and a new explicit artifact/revision budget contract within original totals; these are suggestions, not implemented features. The read-only reviewer-claim diagnostic also demonstrates an incorrect same-model revision goal; fixed native semantic gates and real artifacts remain required; complex-task continuity still requires actual tools/artifacts, independent checks, compaction and Core restart.
- I: Preserve fixed team/context/governance baselines and ablations. Offline mechanism results exist; same-budget real model-quality and Reviewer comparisons remain open.

Completion requires independently evidenced complex-task continuity through failure,
repair, review revision, compaction and restart. Protocol success, local tests or
proposal creation alone do not satisfy it. Desktop packaging remains deferred.

## Historical continuous engineering milestones

- Milestone 1：数据可靠性本机验证完成，历史交付保留。
- Milestone 2：治理、Core 本地信任边界、证据门槛基础及依赖锁通过阶段审查；当时的范围见 [报告](PHASE1_MILESTONE2_REPORT.md)。
- Milestone 3：Provider Gateway、Persistent Router 和模型画像基础完成本机 Mock 协议测试；当时的范围见 [报告](PHASE1_MILESTONE3_REPORT.md)。后续有限真实供应商验证见 M5、M6 报告。
- Milestone 4：Quest Graph、事件投递和应用生命周期接线已完成本机 Mock/SQLite 验证，详见 [报告](PHASE1_MILESTONE4_REPORT.md)。
- Milestone 5：Native Worker、动态团队、固定工具治理与可信执行证据已完成本机冻结验收（282 项测试）；真实供应商通过有限的数值与 JSON 产物演示，范围见 [报告](PHASE1_MILESTONE5_REPORT.md)。
- Milestone 6：Context 归档、来源 Recall 与 Gateway 模型调用统一预算通过本机验证（372 passed）；真实供应商一次有界连续任务恢复成功，前五次诊断失败保留。容量和范围见 [报告](PHASE1_MILESTONE6_REPORT.md)。
- Milestone 7：桌面 Alpha 与 Tauri Core 生命周期正在集成；早期隔离原生启动已观察，最终原生联调、安装包和 Alpha 验收仍待完成。见 [报告草稿](PHASE1_MILESTONE7_REPORT.md)。

以下是原始目标清单，保留作为功能范围参考，而非当前完成状态的逐项核对；它的 Phase 编号与上述连续开发 Milestone 编号不同。具体已验证范围和剩余限制以上述报告及 [STATUS](STATUS.md) 为准。

## Phase 0 — Engineering audit
- [ ] Inspect Python/Node/Rust/Tauri toolchain and repository.
- [ ] Run tests and frontend build.
- [ ] Create ADRs and architecture guardrails.
- [ ] Confirm local startup scripts and deterministic DB location.
- [ ] Add CI workflow and static checks.
- [ ] Record real results in STATUS.
Acceptance: clean environment can start API/UI; tests pass.

## Phase 1 — Persistent Router
- [ ] Replace heuristic classifier with provider-backed Router Agent Loop.
- [ ] Define structured RouterAction schema.
- [ ] Implement intent and Quest reference resolution.
- [ ] Persist Router capsule and event cursor.
- [ ] Implement one unified conversation timeline.
- [ ] Ensure restart recovery.
Acceptance: multiple different topics and concurrent Quests remain in one user-visible conversation.

## Phase 2 — Quest Graph and event relay
- [ ] Add TaskNode, dependencies, cycle checks and DAG scheduler.
- [ ] Add worker event inbox/outbox and idempotent consumers.
- [ ] Add progress coalescing and important-event direct delivery.
- [ ] Add Quest pause/resume/cancel/update.
Acceptance: Worker progress/results appear in the same conversation; no separate user chat.

## Phase 3 — Model Registry
- [ ] Provider adapters for OpenAI-compatible and native APIs.
- [ ] Secure OS credential vault.
- [ ] Provider model discovery where supported.
- [ ] Benchmark task definitions and versioning.
- [ ] Benchmark runner and provenance.
- [ ] Online performance statistics and user override.
Acceptance: at least two real providers and auditable capability profiles.

## Phase 4 — Native Runtime and tools
- [ ] Streaming model gateway and retry/error mapping.
- [ ] Agent Loop and structured tool-call parser.
- [ ] Tool schemas and policy enforcement.
- [ ] Sandboxed file and command tools.
- [ ] Worker session persistence, cancellation and recovery.
Acceptance: a Worker performs a bounded multi-step task using native runtime.

## Phase 5 — Dynamic Team Composer
- [ ] Role templates and bounded dynamic role synthesis.
- [ ] Capability/confidence scoring.
- [ ] Cost/latency/context/concurrency constraints.
- [ ] Complementarity and coordination penalty.
- [ ] Explainable team proposals and approval.
- [ ] Dynamic re-composition on failure/blocker.
Acceptance: Router builds and explains a suitable team for distinct task types.

## Phase 6 — Context and Chain of Recall
- [ ] Task Capsule and compaction trigger.
- [ ] Archive and restore conversation context.
- [ ] Quest/project memory and evidence-linked retrieval.
- [ ] Memory freshness, contradiction and invalidation.
Acceptance: long task resumes after compaction/restart without losing constraints.

## Phase 7 — Governance and evidence
- [ ] Risk policy and exact-scope approvals.
- [ ] Budget ledger and circuit breakers.
- [ ] Evidence graph and artifact hashes.
- [ ] Independent verification.
Acceptance: unsafe actions blocked; completion supported by evidence.

## Phase 8 — Desktop release
- [ ] Tauri Rust bootstrap and Core lifecycle manager.
- [ ] Single chat with embedded Quest cards.
- [ ] Model Library, Agent activity, Memory and Approval views.
- [ ] OS credential storage, backup/restore and Windows installer.
Acceptance: end-to-end local desktop demo.

## Definition of Done
Every phase includes code, tests, docs, migration, error handling, logs, reproducible demo and STATUS update. No phase is complete based only on code generation.
