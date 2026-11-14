# Detailed Implementation Roadmap

## Continuous engineering milestones — 2026-11-15

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
