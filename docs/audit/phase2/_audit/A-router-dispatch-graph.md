# 审计 A：Router / Dispatcher / Quest Graph（只读）

- 仓库：`D:\Competition_this\Agent`，分支 `codex/phase1-continuous`
- 日期：2026-11-15
- 方式：只读源码阅读 + 只读命令。实际执行的命令仅两条：
  - `pytest tests/test_persistent_router.py tests/test_quest_graph.py tests/test_event_dispatcher.py tests/test_m5_lifespan_execution.py -q` → **59 passed, 1 warning, 16.34s**
  - `python --version`（3.13.9，backend/.venv）
- 未运行任何需要 provider/网络的测试；未修改任何源码或既有文档。本文件是唯一写入。

分类口径（严格区分，不夸大）：
**A** 真实实现且被测试覆盖 ｜ **B** 仅在 Mock/脚本化 Provider 下验证 ｜ **C** 已通过真实 Provider 验证 ｜ **D** 只有接口/数据模型/占位 ｜ **E** 目前无法端到端工作

---

## 1. 分类总表

| 能力 | 分类 | 主要证据 | 断言强弱 |
|---|---|---|---|
| Router 结构化决策管道（4 轮、5 个只读工具、守卫、原子提交） | **A** | `router.py:546-983`；`test_persistent_router.py:28-586` | 强（管道/围栏/回滚/预算），弱（语义正确性零覆盖） |
| Router 决策的"模型替身"质量（选对 intent/quest） | **B** | 全部断言来自 `MockProvider` 固定 decision（`test_persistent_router.py:14-25`） | 无 |
| Router 通知播报（notice → 用户可见消息） | **B** | `router.py:328-544`；`test_m4_integration.py:262-345`、`test_background_busy_m7.py:22-117` | 中（含"静默不产消息""失败不推进"） |
| 事件投递 at-least-once / 租约 / dead letter / replay | **A** | `dispatcher.py:1-473`；`test_event_dispatcher.py:18-349` | 强（含子进程崩溃恢复 `:293-321`） |
| DAG 校验、不可变版本、审批与竞态 | **A** | `graph_domain.py:52-83`、`quest_graph.py:64-202`；`test_quest_graph.py:25-136` | 强（环/重复/预算/回滚/CAS 单赢家） |
| DAG 内容由模型生成（Planner） | **B** | `quest_graph.py:426-515`；`test_m4_integration.py:172-259`（`httpx.MockTransport`） | 中（只验 wire schema 与预算归属） |
| Worker 执行 + 工具回执 + 独立验证 → 完成 | **A**（离线）+ **C**（窄） | 离线 `test_m5_lifespan_execution.py:50-216`；真实 `docs/audit/phase1/milestone5/live-probe/completion-20261114T171357Z-587cd661.json` | 真实验证范围极窄（单节点、仅 JSON 语法） |
| Quest 优先级参与调度 | **D** | `quest_graph.py:356-380` 只写不读；`main.py:268`、`worker_runtime.py:1900-1908` 均按 seq/id | 无测试断言优先级生效 |
| Quest 级自动阻塞/失败升级 | **D/E** | 无 `UPDATE quests SET status='blocked'`（grep 全 `src`）；仅 `domain.py:58-72` + 人工 `main.py:1183` | 无 |
| 端到端"用户一句话 → 执行完成" | **E** | 需 4 次人工 API（见 §8 第 8/10 步）；Router 明确拒绝代审批 `router.py:915-917` | 无一条链路测试跨过这段 |

---

## 2. Router 的实际决策协议

### 2.1 Schema 与校验（`router_domain.py:6-22`）
- `RouterDecision`：`extra="forbid", strict=True`；`intent` ∈ {`chat, create_quest, continue_quest, update_quest, status_query, approval_response, ambiguous`}；`reply` 长度 1–10000；`title/goal/quest_id` 可空；`candidates` 上限 100。
- **没有** rationale / confidence / action-plan 字段——决策依据不可审计（对比 `NoticeDecision` `router_domain.py:35-38` 也一样）。
- `TaskCapsule`（`router_domain.py:25-32`）是持久胶囊模型，含 `source_event_cursor`。

### 2.2 调用协议（最多 4 次模型调用）
- `router.py:607-642`：`for _step in range(4)`；`_step<3` 带工具，第 4 轮无工具并追加系统提示"必须现在返回最终决策"。
- 每轮最多 4 个工具调用（`router.py:692-693`）；工具白名单 5 个：`list_quests / list_events / list_evidence / list_models / read_context_source`（`router.py:30-58`）。
- 工具参数强校验：`list_*` 必须 `arguments == {}`；`read_context_source` 只允许 3 个键且有范围/类型检查（`router.py:704-749`）。任何未授权工具/参数 → `DomainError`，"Router requested an unauthorized tool"。
- 工具结果不是"执行"，只是把 `snapshot` 切片或 `context_service.read_source` 的定长片段拼回 messages（`router.py:731-771`）。

### 2.3 输出校验与失败处理
- `finish_reason` 必须 ∈ {None, stop, mock}；响应 ≤ `output_reserve*8` 字节（`router.py:774-780`）；`RouterDecision.model_validate_json` 严格校验（`:781`）。
- 任一违规 → `DomainError`，**整轮无任何写入**（`test_persistent_router.py:179-185` 断言 messages/quests/events 全空）。

### 2.4 拒绝 / 澄清逻辑（真正的判定在这里）
`_commit`（`router.py:821-983`）做确定性后置校验：
- 只接受"本轮上下文里实际呈现过"的 Quest：`presented = {q["id"] for q in context["quests"]}`（`:834`），`quests` 只保留交集（`:835`）。
- 对 `continue_quest / update_quest / status_query / approval_response`，`quest_id` 必须同时满足（`:847-862`）：
  1) 在 `quests` 中；2) `candidates` 去重后**恰好一个且等于 quest_id**；3) 用户文本中的显式 `quest_xxx` 与之一致；4) 无显式 ID 时 memory 未截断、召回唯一、quests 未截断。
  任一不满足 → `intent = "ambiguous"`（`:863`），回复固定为"请说明你指的是哪个 Quest"（`:935`）。
- 唯一例外：全局状态查询（无 quest_id、无 candidates、无显式 ID，`:841-846`）。
- **这是启发式吗**：不是意图分类器；`intent` 完全来自模型。但存在三类确定性覆盖：正则提取显式 Quest ID（`:838`）、candidates 一致性、召回/截断降级（`:854-861`）。因此是"模型决策 + 确定性守卫"，无 Mock→规则回退：无模型配置时直接报错（`:579-580`），不降级。

### 2.5 是否真的由模型驱动
- 是。`intent/title/goal/quest_id` 全部取自模型；无 `if "帮我" in text` 式分类。
- 但**5/7 意图的回复被中文模板覆盖**：create（`:888`）、update（`:914`）、approval（`:917`）、continue（`:920`）、status（`:924-933`）、ambiguous（`:935`）；只有 `chat` 使用模型的 `reply`（`:868` 赋值后未覆盖）。自然语言质量上限受模板约束。
- **契约缺口（重要）**：system prompt（`router.py:19-24`）从未定义 `candidates` 语义，却要求 `candidates == [quest_id]`（`:849-851`）。真实模型按直觉输出 `candidates: []` 会被静默降级为 ambiguous；Mock 测试恰好都填了两处（`test_persistent_router.py:209-224`），所以测不出来。

### 2.6 幂等与并发
- `request_id + input_hash` 幂等（`router.py:557-566`、`:787-793`），同 ID 不同输入报错。
- 上下文漂移：`_stable_context`（`:309-326`）比较 state/models/memory/capsule/messages；不一致则重试，上限 `max_conflicts=2`（`:76`、`:794-796`、`:819`）。源码阅读提示 `read_context_source` 另有逐源校验（`:797-806`）。

---

## 3. Router 是否具备 Agent Loop

| 段 | 有无 | 证据 | 说明 |
|---|---|---|---|
| Observe | 有 | `router.py:96-173`；notice 分支 `:399-419` | 单事务快照：quests(≤100/+显式召回)、messages(≤201)、capsule、events(≤200)、evidence(≤50)、models(≤100) |
| Retrieve | 有但弱 | `router.py:137-148`、`:30-58`、`:265-279`；`memory.py:12-24` | 词项倒排检索（CJK 二元组）+ 5 个只读工具；无向量、无重排、无"检索充分性"判断；工具结果只是并入上下文 |
| Reason | 弱/隐式 | `router_domain.py:6-22` | 单次结构化输出，schema 无 rationale/plan/置信度；模型无法表达"为什么选这个 Quest" |
| Decide | 有 | `router.py:781` | `RouterDecision` |
| Act | 有（限于登记与播报） | `router.py:864-982` | 只写 messages / quests / capsule / event_cursor / router_turns，不执行任何工具或授权（设计如此） |
| **Reflect** | **缺失** | 无自检调用；`router.py:179-185` 校验失败直接抛错 | 没有"检查决策是否满足约束再修订"的一轮，也没有"工具结果不足以决策时继续追问"的策略（只有硬上限 3 轮） |

结论：**Observe/Retrieve/Decide/Act 具备，Reason 弱，Reflect 完全缺失（≈5/6）**。所谓"Agent Loop"目前是"一次模型调用 + 可选 3 轮只读工具"，不具备自我评估与修订。

---

## 4. Quest 创建 / 继续 / 切换 / 歧义澄清

- **创建**：要求 `title` 与 `goal` 同时存在，否则 `DomainError`（`router.py:871-873`）→ `save_quest`（`:874-875`）→ `quest.proposed` 事件（`:877-881`）→ 写 capsule（`quest_id/goal/next_action="await user review"`，`:882-887`）→ 固定回复（`:888`）。**不生成计划**，计划由 planner 消费事件异步产生。
- **继续**：只回模板并返回 `result_quest_id`（`router.py:918-920`）。不写事件、不写 capsule、不设置"当前 Quest"。因此**"切换"没有持久语义**：切到 Quest B 再下一轮，仍需模型重新解析目标（只能靠 recent_messages 的对话历史）。
- **更新**：仅 `proposed/awaiting_approval` 且必须带 `goal`（`:891-892`）；`version+1, plan_revision+1`（`:893-902`）；发 `quest.proposal_updated`（`:905-907`）。**缺陷**：
  - 不把旧 plan 置 `superseded`（对比 `quest_graph.py:100-103` 的 propose 会置），而 `approve` 要求 `quest.plan_revision == plan.revision`（`:176`）→ 更新后旧计划永久不可批准，Quest 停在 `awaiting_approval` 无可用计划；
  - planner 只订阅 `quest.proposed`（`dispatcher.py:147-148`、`main.py:267`），`quest.proposal_updated` 不触发重规划；`process_pending_plan` 还会因"已存在任何 plan"直接跳过（`main.py:274-283`）；
  - 手动 `POST /plan/generate` 会因 `revision = quest.plan_revision + 1`（`quest_graph.py:93`）产生 revision 跳号（1 → 3）。
- **歧义澄清**：一次性模板（`:935`），无 `pending_clarification` 持久状态；下一轮完全依赖模型读历史自行理解。
- **审批响应**：不落审批，回复"请使用审批界面确认；模型不会代替用户审批"（`:915-917`），测试断言 approvals 表 0 行（`test_persistent_router.py:227-244`）。

---

## 5. Planner 是否独立、DAG 来源、成功标准

- **独立性：是**。独立 consumer `planner`（`dispatcher.py:147-148`、`tick` `:441-446`），独立服务 `QuestGraphService`（`quest_graph.py:18`），独立后台循环 `process_pending_plan`（`main.py:219-221, 258-343`），独立预算用途 `purpose="planner"`（`quest_graph.py:493-498`）。
- **DAG 来源：模型生成，非模板**。`generate` 用严格 `json_schema`（`quest_graph.py:455, 485-488`），输入只有 `title/goal/plan_revision`（`:446-455`），输出 `GraphWire` → `GraphProposal`（`graph_domain.py:104-120`）。测试 `test_m4_integration.py:172-259` 只验证 wire schema 形状与预算归属，图内容是 MockTransport 写死的。
- 但 **`propose` 接受任意 dict**（`quest_graph.py:64-73`）：只要通过 `GraphProposal` 校验即可入库，API 层未暴露该入口（`main.py` 只有 `/plan/generate` 调模型），风险目前限于内部调用与测试。
- **成功标准如何定义**：`node.success_criteria` 由模型自由文本给出（`graph_domain.py:10`，仅校验"非空且 ≤1000 字符"，`:17-20`）。
  - 真正可验证的只有两种前缀：`json_valid:<path>` 与 `semantic_numeric:scheduler-v1:<path>`（`evidence.py:736-742`）。
  - 不匹配的节点在验证期直接 `DomainError("Task criterion lacks an independent verifier")`，三次尝试后 `node_verification_attempts.state='blocked'`（`evidence.py:607-623`）。
  - **因此存在"可批准但永远无法完成"的计划**：`test_m5_lifespan_execution.py:230` 的 `"manual"`、`:498` 的 `"manual review"` 正是此情形（断言 quest 不 completed）。schema/propose/approve 三层都没有前置校验。
- **完成判定**：`CompletionGate.check`（`evidence.py:182-210`）要求每个节点 succeeded/failed（失败需有已验证的 recovery 分支）/skipped；`_check_live_node`（`:370-476`）要求 live run + 最近 assistant 消息无 tool_calls 且符合 `output_schema` + `worker.result` 事件 + 每个工具调用有 live receipt 且参数 digest 一致 + `evidence(kind='tool',trusted=1)` + R2 审批一致；`NodeVerifier.verify` 在剩余节点为 0 时把 Quest 置 completed（`:838-863`）。

---

## 6. 多 Quest 并行 / 优先级 / 阻塞

- **并行**：`WorkerScheduler.tick` 跨 Quest 扫描 run 并按 `r.id` 顺序 spawn（`worker_runtime.py:1900-1916`），受 `max_concurrency`（默认 2，`:215`）与 `max_agents`（默认 8）限制；预算三层预留（global/quest/team，`:292-307, 450-476`）。`test_m5_lifespan_execution.py:198` 断言 `worker.peak == 2`（真实并发证据在离线脚本化 Provider 下取得）。
- **优先级：无执行效果（D）**。`quests.priority` 只在 `set_priority` 写入并发 `quest.priority_changed`（`quest_graph.py:356-380`）；planner 取事件 `ORDER BY seq`（`main.py:268`）、调度器 `ORDER BY r.id`（`worker_runtime.py:1903-1908`）、dispatcher `ORDER BY d.seq`（`dispatcher.py:174-191`）都不读 priority。
- **阻塞识别**：节点级有（预算 `worker_runtime.py:1422-1447`、`_fail` `:1029-1067`、崩溃未决工具结果 `:857-876`），并发 `worker.blocked` 事件；`readiness()` 能算出 `ready/blocked/skipped/ inactive`（`quest_graph.py:382-424`）但**源码内无调用者**（仅测试用），实际依赖检查是另一份实现（`worker_runtime.py:336-346`）→ 两份逻辑可能漂移。
- **Quest 级阻塞/失败：缺失**。全仓库无 `UPDATE quests SET status='blocked'`；阻塞只停留在 worker/task_node。Quest 只能人工 `POST /api/quests/{id}/transition`（`main.py:1183-1192` → `quest_service.py:55-88`，表在 `domain.py:45-80`）。所有 Worker 阻塞后 Quest 永久 `running`。
- **潜在死锁**：`NodeVerifier.tick` 只处理 `q.status='running'` 的节点（`evidence.py:619, 629`）；`start` 只把 `approved→queued`（`worker_runtime.py:652-656`），`queued→running` 发生在 `_claim`（`:954-958`）。若任务在 `queued` 状态已达 review_pending（例如并发上限拒绝 claim），验证不会推进。

---

## 7. 重启恢复（cursor / capsule）实际程度

| 项 | 状态 | 证据 |
|---|---|---|
| 事件消费游标 | **已实现** | `event_consumers(scan_seq,cursor_seq,priority_scan_seq)`（`dispatcher_schema.py:5-12`）；`_materialize/_advance`（`dispatcher.py:89-142`）；崩溃后租约过期重投（`test_event_dispatcher.py:293-321` 真子进程） |
| Router 事件游标 / revision / 胶囊 | **已实现** | `router_state.event_cursor/capsule/capsule_version`，CAS 更新（`router.py:952-969`）；turn 幂等表（`:972-982`） |
| Router 通知重试 | **已实现** | `notice_attempts/next_attempt/notice_token/notice_dead_at`（`router.py:337-397`），5 次后 dead |
| 胶囊单一来源 | **部分** | 双源：优先 `context_archive.capsule_json`（`router.py:125`），否则 `router_state.capsule` 文本（`:221-225`）；`compact_router` 又混用 legacy（`context_service.py:287-323`）；`tasks` 每轮重算（`router.py:127`）。字段所有权分散，`next_action/goal` 有陈旧风险 |
| Worker 逐步 checkpoint | **已实现** | `worker_capsules/worker_messages/worker_tool_calls`（`worker_runtime.py:1068-1108`）；lease+generation 围栏（`:842-960, 961-991`） |
| 崩溃后工具副作用 | **安全优先，不重放** | 未决调用置 `uncertain` → run `blocked`（`:857-876`），需人工 `retry`（`:785-840`） |
| 验证尝试 | **已实现** | `node_verification_attempts`，崩溃重试 ≤3（`evidence.py:607-661`） |
| 重启后续跑 | **部分** | 调度器只拾取 `queued` 或 lease 过期的 `running`（`worker_runtime.py:1900-1916`）；`blocked` 一律人工 |
| 压缩/归档重建 | **已实现** | `compact_router` / `rebuild_router`（`context_service.py:263-373`） |

---

## 8. 当前执行链路图（文字版，真实函数名）

1. `POST /api/conversation/message` → `forge_api.main.send_message`（`main.py:1020-1027`）
2. → `RouterService.handle_user_message`（`router.py:546`）：`_sync_memory`（`:88` → `MemoryService.sync_sources`）→ `_snapshot`（`:96`）→ 需要时 `ContextService.compact_router`（`:575`）→ `_context`（`:175`）
3. → 最多 4 轮 `ProviderGateway.generate`；工具分支在 `router.py:704-771`（`list_quests/list_events/list_evidence/list_models/read_context_source`）
4. → `RouterDecision.model_validate_json`（`:781`）→ `unit_of_work` → `_stable_context`（`:794`）→ `_commit`（`:821`）
5. → `_commit`：`work.save_quest`（`:874`）+ `quest.proposed`（`:877-881`）+ capsule/event_cursor（`:952-969`）+ `router_turns`（`:978-981`）
6. → `Dispatcher.tick`（`dispatcher.py:441`）→ `claim`（`:163`）→ `deliver`（`:238`）写 `event_inbox`；`_routable` 只把 `quest.proposed` 投给 `planner`（`:147-148`）
7. → `forge_api.main.process_pending_plan`（`main.py:258`）→ `QuestGraphService.generate`（`quest_graph.py:426`）→ `provider.generate` → `GraphWire`（`:506`）→ `propose`（`:64`）落 `quest_plans/task_nodes/task_edges`，Quest → `awaiting_approval`（`:94-96`）
8. **（人工 1）** `POST /api/quests/{id}/plan/approve`（`main.py:1113`）→ `QuestGraphService.approve`（`quest_graph.py:159`）→ `quest.plan_approved`
9. → dispatcher → `composer` consumer → `process_pending_team`（`main.py:346`）→ `provider.generate`（`TeamWire`）→ `TeamCompositionService.propose`（proposed 提案）
10. **（人工 2、3）** `POST /teams/approve`（`main.py:1156`）+ `POST /quests/{id}/grants`（`main.py:787`，R1 工具所需）
11. → `WorkerScheduler.tick`（`worker_runtime.py:1802`）→ `WorkerManager.start`（`:309`：预算预留、节点绑定、`approved→queued`）→ `asyncio.create_task(manager.run)`（`:1916`）
12. → `_run`（`:1122`）→ `_claim`（`:842`，lease + `queued→running`）→ `_checkpoint`（`:1068`）→ `ContextService.build_worker_context`（`context_service.py:375`）→ `provider.generate`（`:1552`）
13. → 工具调用落 `worker_tool_calls(pending)`（`:1744-1751`）→ `ToolEngine.execute`（`tool_engine.py`）→ `action_intents/action_receipts` + `evidence(kind='tool')`（`tool_engine.py:952`）→ 回填 `worker_messages(tool)` 与 `capsule.evidence_refs`（`:1267-1299`）
14. → 无工具调用时校验 `output_schema`（`:1677-1683`）→ `task_nodes/worker_runs='review_pending'` + `worker.result` 事件（`:1756-1782`）
15. → `NodeVerifier.tick`（`evidence.py:607`）→ `verify`（`:709`）→ `JsonVerifier`/`SemanticVerifier` + receipt 校验 → `task_nodes='succeeded'`（`:810-813`）→ `_settle_conditions`（`:663`）→ 全节点终态后 `CompletionGate.check`（`:182`）→ `quests='completed'`（`:845-863`）
16. → `worker.result / quest.status_changed` 事件 → `RouterService.handle_pending_events`（`router.py:328`）→ `NoticeDecision` → `work.add_message(assistant, source='router_notice')`（`:519-530`）→ `GET /api/conversation`（`main.py:846`）

**结论**：链路技术上闭合，但第 8、10 步是纯人工 API（且需要 CAS `expected_version` 自行维护），Router 明确不代批（`router.py:915-917`）。"用户一句话 → 执行"目前不成立（E）。

---

## 9. 与 Phase 2 要求的差距

> 说明：Phase 2 原始清单见 `docs/ROADMAP.md:33-38`（TaskNode/DAG 调度、worker inbox/outbox 幂等消费者、进度合并与重要事件直投、Quest pause/resume/cancel/update，验收口径"进度与结果出现在同一段对话"）；本节同时纳入本次要求的 Router/Planner/多 Quest/恢复能力。

| # | 能力 | 现状 | 最小必要改动（不重写） |
|---|---|---|---|
| 1 | TaskNode / 依赖 / 环检测 | **已有** | — |
| 2 | DAG 调度器 | **部分**（调度器不用 readiness，另有一份边检查在 `worker_runtime.py:336-346`） | 让 `WorkerScheduler.tick` 与 `start` 复用 `QuestGraphService.readiness`（`quest_graph.py:382`），删除重复实现 | 
| 3 | Worker 事件 inbox/outbox 与幂等消费 | **已有** | — |
| 4 | 进度合并 + 重要事件直投 | **部分**（仅同 quest+worker 的 `worker.progress` 合并 ≤20，`router.py:353-360`；URGENT 抢占 `dispatcher.py:182-191`） | 把 notice 文本改为确定性模板（模型可选润色），避免"没有模型就看不到进度"；合并维度改为按 quest |
| 5 | Quest pause / resume / cancel / update | **部分**（`quest_graph.py:204-283` 覆盖 pause/resume/cancel/retry；update 只改 goal 且打断计划链） | `_commit` 更新分支增加"旧 plan 置 superseded + 发 `quest.proposed`（或新增 `quest.replan_requested`）+ planner 订阅"，使改目标能重新规划（`router.py:889-914`） |
| 6 | 多 Quest 并行 | **部分**（可并行，受 max_agents=8 / max_concurrency=2 限制） | 调度排序加入 `priority DESC, created_at`（`worker_runtime.py:1900-1908`、`main.py:268`） |
| 7 | 优先级真实生效 | **缺失（D）** | 同上，一处排序即可；配套断言"高优先级先起" |
| 8 | 阻塞识别与升级 | **缺失**（仅节点级 blocked） | 在 `_fail`/`verify` 的 blocked 分支后加一次"该 Quest 所有节点是否已终态或阻塞"判定，将 Quest 置 `blocked/needs_input` 并发 `approval.requested` |
| 9 | Router Agent Loop | **部分**（缺 Reflect；Reason 无表达字段） | schema 增可选 `rationale`/`confidence`；在最终决策前加一次"自检/修订"调用（复用现有 4 轮预算中的第 4 轮） |
| 10 | Router 决策契约可读性 | **缺失**（`candidates` 语义未写入 prompt） | `_POLICY`（`router.py:19-24`）明确 `candidates` 定义，或把 `:849-851` 放宽为"candidates 为空或等于 quest_id" |
| 11 | Planner 独立 + 模型生成 DAG | **部分**（有独立消费者；无 repair、无重规划） | 图校验失败时做 1 次有界修复调用，而不是整事件重试到 dead（`main.py:328-343`） |
| 12 | 成功标准可定义可验证 | **缺失**（自由文本 vs 两前缀白名单） | `propose/approve` 时按 `evidence.py:736-742` 白名单前置校验 criteria，返回可读错误；或引入 criterion DSL + 验证器注册表 |
| 13 | 端到端"一句话 → 执行" | **缺失（E）** | 增加一次受治理的用户确认 API：同一事务内 approve plan + approve team + grant scope（保留 Router 不代批的边界），把 3 次人工确认收敛为 1 次 |
| 14 | 重启恢复 cursor/capsule | **部分** | capsule 单一来源（archive 或 router_state 二选一）；补一个"重启后 notice 续跑 + 未完成 run 续跑"的集成测试 |

---

## 10. 关键缺口（按风险排序）

**P0（阻断 Phase 2 验收）**
1. 成功标准不可验证即可批准 → 计划永久卡死（`graph_domain.py:10,17-20` vs `evidence.py:736-742`）。
2. Quest 无自动阻塞/失败升级 → 阻塞后永久 `running`（无 `UPDATE quests SET status='blocked'`；`domain.py:66-72` 允许但无人调用）。
3. `candidates` 契约未文档化 → 真实模型高概率被判 ambiguous（`router.py:849-851` vs `:19-24`）。
4. 端到端需 4 次人工 API（`main.py:1113/1156/787`），Router 拒绝代批（`router.py:915-917`）→ 验收口径"Worker 进度/结果显示在同一对话"仅在后半段成立。

**P1**
5. `priority` 无执行效果（D）。
6. 胶囊双源 + `next_action/goal` 陈旧风险（`router.py:125,221-225`；`context_service.py:287-323`）。
7. `update_quest` 打断计划链（旧 plan 悬空、revision 跳号、无重规划）。
8. 验证要求 `quest='running'` 与"首个 Worker 仅到 queued"存在竞争窗口（`evidence.py:619,629` vs `worker_runtime.py:652-656`）。

**P2**
9. 5/7 意图回复模板化（`router.py:888-935`），对话自然度受限。
10. 无 Reflect 段；无 rationale/confidence → 决策不可解释。
11. Planner 无 repair，模型输出非法即整事件重试（`main.py:328-343`）。

---

## 11. 证据分级与限制（务必与结论一起读）

- **C 类（真实 Provider）只有一处且范围极窄**：`docs/audit/phase1/milestone5/live-probe/completion-20261114T171357Z-587cd661.json`（`status=live_json_artifact_completion_verified`，模型 `deepseek-v4.1-flash`，1 个 `file_write` receipt、`json_validation` exit_code=0、task succeeded、quest completed）。其生成脚本 `backend/tests/live_completion_probe.py` 显示：**手工构造 plan（`:92-108`）与 team（`:133-164`）**，工具参数被 `FixedScope` 限定（`:183-193`），成功标准仅 `json_valid`。因此它**不覆盖** Router 决策、模型生成 DAG、TeamWire 生成的真实往返。
- **"live" 是适配器自报**：`providers/__init__.py:48`（`self.execution_mode = "mock" if transport is not None else "live"`），`provider_pool.py:47` 只做枚举校验；脚本化 Provider 只要声明 `execution_mode="live"` 即可通过证据门的 live 检查（`test_m5_lifespan_execution.py:132-135`）。**证据门的 "live" ≠ 真实网络调用**。
- **本次未审计但相关的产物**：工作区存在未跟踪的 `backend/scripts/phase2/{live_core,live_e2e,smoke_live_core,probe_capabilities}.py`（`live_e2e.py` 自述为 real provider / real tools / real evidence 的 live 驱动）。这是 Phase 2 的在建脚本，本次**未审计、未运行**（需要 provider 凭据）。若其成功运行并留下脱敏证据，可把本文 §1 中多项 B 升级为 C。

- **A 类的强弱**：
  - `test_event_dispatcher.py` 最强（真 SQLite、真子进程崩溃恢复、并发单赢家、dead letter 审计）。
  - `test_persistent_router.py` 强在管道与围栏（4 轮上限、非法工具、幂等、上下文漂移、预算、回滚），但 **intent 语义正确性 0 覆盖**（全部固定 decision）。
  - `test_quest_graph.py` 强在原子性/CAS/回滚（含触发器注入失败），**不覆盖**"模型生成图"与"成功标准可验证性"。
  - `test_m5_lifespan_execution.py` 是唯一跨模块（dispatcher→scheduler→worker→verifier→notice）的端到端，但 plan/team 均为手工构造、Provider 为脚本化，断言集中在"receipt 数=3、执行 exit_code=0、quest completed、peak 并发=2"。

## 12. 一句话结论

Router 是**真模型驱动 + 确定性守卫**的持久决策器（无启发式意图回退），Dispatcher/Graph/Worker 的持久化与围栏工程质量高且被真实 SQLite 测试覆盖；但 **Router 缺 Reflect 段**、**Planner 的"成功标准"与"可验证性"脱节**、**优先级与 Quest 级阻塞是空壳**、**端到端仍需 4 次人工 API**——这四点是与 Phase 2 验收口径的主要差距，且全部是"补一小段"而非重写。
