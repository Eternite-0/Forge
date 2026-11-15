# D — Context / Memory / Governance 只读审计

- 仓库：D:/Competition_this/Agent，分支 `codex/phase1-continuous`，HEAD `bcc3592`
- 审计日期：2026-11-15；性质：只读代码审计，**未修改任何源码/文档**（本文件为唯一写入）
- 范围：`context_service.py`、`context_schema.py`、`memory.py`、`memory_schema.py`、`governance.py`、`evidence.py`（治理相关）、`store.py`、`migrations.py`，以及为还原链路必须阅读的 `router.py`、`worker_runtime.py`、`tool_engine.py`、`dispatcher.py`、`event_domain.py`、`forge_api/main.py`

## 0. 方法与被验证的运行事实

- 精读源码（行号引自当前工作树）。未运行任何需要 provider 的测试，未运行 `tests/live_*_probe.py`。
- 实际执行（本地、无网络）：

```
cd backend
./.venv/Scripts/python.exe -m pytest tests/test_context_m6.py tests/test_memory_m6.py tests/test_governance.py tests/test_migrations.py tests/test_schema_gateway_m6.py -q
→ 72 passed in 13.47s
```

- 环境观察：系统 Python 3.13 缺 `python-dotenv`，3 个模块收集失败（`backend/src/forge_core/settings.py:9` 的 `from dotenv import dotenv_values`）。必须使用 `backend/.venv`，否则会把"环境缺失"误判为代码缺陷。
- 未读取、未打印任何凭据（下文仅出现环境变量名）。

## 1. 分类总览（严格区分，不夸大）

| # | 能力 | 分类 | 关键证据 | 强度说明 |
|---|---|---|---|---|
| 1 | Router 会话归档 + 完整性校验 + 重建 | **A** | `context_service.py:263-338`（compact）、`:340-373`（rebuild）、`:199-220`（逐条 source hash 校验）；`tests/test_context_m6.py:12-29`、`:44-59` | 强：篡改消息即报 integrity（`test_context_m6.py:24-29`）；CAS 失败整表回滚（`:44-59` 用触发器注入） |
| 2 | Worker 上下文预算 + 归档 + 工具对恢复 | **A** | `context_service.py:375-528`；`test_context_m6.py:174-243`、`:615-698`、`:701-737` | 强：断言 tool_call/tool_result 不被拆散（`:213-214`）、receipt 进 capsule（`:686-687`）、破坏归档报 integrity（`:697-698`） |
| 3 | compaction 摘要生成 | **A（但非模型生成）** | `context_service.py:302-323` 全字段由 SQL 行拼装；模块无 provider/runtime import | 摘要=结构化模板；测试只断言字段与连续性，不衡量摘要质量 |
| 4 | 自动 compaction 触发 | **触发逻辑 A；端到端 B（mock provider）** | 用户链 `router.py:569-577`；通知链 `router.py:421-433`；`test_context_m6.py:246-297`、`:569-612` | 阈值真实（条数>50 或字节>`context_limit//2`）；两条链均在 `execution_mode="mock"` 下验证 |
| 5 | Core 派生源索引 + 召回 | **A** | `memory.py:147-312`（sync）、`:314-600`（recall）；`test_memory_m6.py:110-136`、`:199-241`、`:268-313`、`:316-332` | 中强：含事务回滚、快照篡改、plan_revision 失效、游标分页；但多例自建最小表（见第 6 节） |
| 6 | 手工记忆写入 + key 冲突 + 过期 + 撤销 | **D（无生产调用方）/ A（仅测试）** | `memory.py:51-127`；生产侧仅 `router.py:85` 构造、`main.py:875` 读、`main.py:885` invalidate；`test_memory_m6.py:19-107` | `remember()` 在 `backend/src` 无调用点 → 运行期 `memories` 空表，冲突/过期分支在真实链路不可达 |
| 7 | 分层 L0/L1/L2/L3 | **部分（L0 缺失）** | `memory.py:70` 白名单含 L0；实际只产出 `memory.py:474`（L2）、`:568`（L3/L1） | L0 从未被写入或读取 |
| 8 | constraint 抽取/索引/冲突/过期 | **缺失（E）** | `context_service.py:316` 仅继承 legacy capsule；`router_domain.py:30` 默认空；`router.py:882-887,908-913` 从不填 | 唯一"约束保留"=逐字归档 + `quests.metadata` 透传 |
| 9 | Agent 间通信：类型集合 + 持久化 + 状态机 | **A（集合与投递）** | `event_domain.py:16-44`、`dispatcher_schema.py:13-31`、`dispatcher.py:144-216,238-361`、`worker_schema.py:5-8,35-43` | 状态机真实可测；但无"类型→payload schema"强类型表 |
| 10 | 风险分级 R0–R3 | **A** | `governance.py:16-25`、`:38-42`、`:191-192,353-354`；`tool_domain.py:97-191` | 强：默认拒绝，未知名抛错；`test_governance.py:50-57` 覆盖降级与 R3 |
| 11 | 审批参数绑定 / 单次消费 / 过期 / 策略版本 | **A** | `governance.py:195`、`:387-395`、`:398-406`、`:254-268`；`test_governance.py:17-27,93-114,117-130,148-158,161-176` | 强：含两线程并发消费仅一次成功（`:30-47`） |
| 12 | 完成门：审批→真实 receipt→独立校验→落库 | **A + C** | `evidence.py:171-241`、`:370-540`、`:709-862`；C 证据 `.local/phase1/milestone5/live-probe/completion-20261114T171357Z-587cd661.json` | A 段强；C 仅证明"JSON 产物 + 独立语法校验"，非领域正确性 |
| 13 | 预算准入 / 熔断 | **A** | `model_call_budget.py:145-455`（admit）、`:457-483`（settle+breach 围栏）、`:116-133`（cooldown）；`tests/test_model_call_budget.py:124,63,280` | 强：超用置 `breached=1` 使后续全部 BudgetExceeded；worker 侧另有一套（`worker_runtime.py:1396-1469`） |

## 2. 重点问题逐条回答

### 2.1 Context 分层（L0-L3）与 compaction

**实现了哪几层。** 没有"层"字段驱动分层，分层是召回项上的来源标签：

- L1 = Quest 记录 / 决策事件 / 验证证据派生项（`memory.py:568`）
- L2 = Workspace 项目身份（`memory.py:470-485`，`:474`）
- L3 = 失败经验（`worker.failed/blocked/tool.execution_uncertain`，`memory.py:536-545,568`）
- **L0 只在白名单里**：`memory.py:70` 允许 `{"L0","L1","L2","L3"}`，全仓 grep 仅命中该行。未归档的"工作上下文"没有 L0 记录，它是 `router.py:118-124` 直查 `messages` 的 live 窗口。

结论：**L1/L2/L3 已有（Core 派生），L0 缺失**；分层是可检索标签，不是可插拔存储层。

**compaction 触发。** 两条真实触发点，条件相同：

- 用户消息链 `router.py:569-577`：`len(json(messages)) > context_limit//2` 或 `len(messages) > 50`，且 `len(messages) > 10`，每轮 `compact_router(keep_recent=10)` 后 `continue` 重取快照；上限 100 次否则 `DomainError`（`:573-574`）。
- 后台通知链 `router.py:421-433`：同阈值。
- 硬边界：消息数 ≤10 时永不压缩，单条超大只能失败关闭（`router.py:256-257`、`:291-292`）。

**摘要生成方式：模板/数据库拼装，不是模型生成。** `context_service.py:302-323`：

- `goal/success_criteria` ← `quests` + `quest_requirements`（`:290-307`，并在 `rebuild_router` `:352-371`、`router.py:126-136` 重取，避免过期）
- `facts` ← 上一版 capsule（`:308`，**恒为空**，见第 2.3 节）
- `hypotheses` ← `_merge_messages`（`:17-45`）：user 消息逐字保留、去重、绑 `source_refs`；非 user 消息截断 256 字符并标 `omitted`
- `tasks` ← `_task_context`（`:48-161`）：逐 Quest 拉 requirements/approved plan/task_nodes/evidence/receipts
- `constraints` ← `legacy.get("constraints", ...)`（`:316`），只继承旧 `router_state.capsule`

整模块无 provider/runtime import，无模型调用 —— 刻意的确定性模板。

**来源引用与可恢复性（本模块最强项）。**

- 每条 source 存 `{"id","hash": sha256(row)}`（`:324`）；归档行自带 `source_hash/capsule_hash`（`:176-197`）
- 每次重建都重算 hash 并逐条 `_verify_source`（`:199-220`、`:241-261`）
- 原文不删：`messages` 全量保留（`test_context_m6.py:19` 断言 8 条仍在）；可经 `read_source` 读回（`context_service.py:530-537`），Router 侧 `read_context_source` 支持 `offset_chars`/`max_chars≤2048` 分片并回传 `source_sha256`（`router.py:714-747`；`test_context_m6.py:495-566` 用 4 片拼回原文并逐片校验）
- Worker 侧同理：只允许读回已归档 ordinal（`worker_runtime.py:1129-1204`），并对读回内容二次比对（`:1155-1167`）

### 2.2 constraint 抽取与索引：**不存在**

- 抽取：无。相关代码仅 `context_service.py:107`（把 `quests.metadata` 当 constraints 透传）、`:316`（继承旧 capsule）、`router_domain.py:30`（字段声明）、`memory.py:86`（允许 `kind="constraint"`）。`router.py:882-887`、`:908-913` 构造 `TaskCapsule` 只填 `quest_id/goal/next_action/source_event_cursor`，`constraints` 恒为 `[]`。
- 索引：无。`memory_terms` 只索引 Quest/事件/证据/Workspace 快照（`memory.py:22-24,122-124,169`）；`sync_sources` 不扫 `messages`（`:188-199`）。
- 冲突检测：**只有** `memories.memory_key` 同键不同 statement 的 `conflicted` 标记（`memory.py:410-430`），而 `remember()` 无生产调用方 → 运行期不生效。
- 过期检测：同样只在 `memories.valid_until`（`memory.py:401`、`:418-419`）。另有"计划版本失效"：plan_revision 变化把派生源置 `active=0`（`:220-223,268-272,288-292`）—— 版本失效，不是时间过期。
- 现存"约束不丢"靠两件事：user 消息逐字进 `hypotheses`（`context_service.py:17-45`）+ `quests.metadata` 透传。`docs/PHASE1_MILESTONE6_REPORT.md:41-42` 也承认 "Future work needs per-Quest constraint association"。
- 真实供应商的 `constraints_preserved=true`（`.local/phase1/milestone6/context-20261114T183632Z-53aba023/report.json`）来自脚本把 60 条约束写进 `quests.metadata`（`tests/live_context_probe.py:166,180-183`），再用子串包含判断（`:208-210`）——**透传校验，不是抽取校验**。

### 2.3 Recall 检索实现

- **纯词项索引，无语义向量**：`_terms`（`memory.py:12-17`）做 ASCII 词 + 中文 bigram，上限 128；排序=候选集合字典序（`:372` `sorted(candidate_set)[:513]`）；无 TF-IDF/相似度/embedding（`backend/src` 全量无 embedding/vector/cosine/faiss/numpy 命中）。
- 候选边界：每词 513（`:359`）、按 quest 索引 513（`:367`）、`refs` 直并（`:371`），总候选 513，消费前 512（`:372-374`）；`limit`/`max_bytes` 双限，超限计 `omitted` 并给 `cursor`（`:584-600`）。
- 来源可靠性：每项带 `trust`（`core_workspace|core_record|core_event|verified_evidence`）、`source_hash`、`plan_binding`（`bound|unbound`）、`trust_scope`（`memory.py:564-581`，明确"仅证明来源记录，不证明领域真值"）；召回时逐项重算快照哈希并与当前 Core 行比对（`:446-563`），不一致即丢弃（`test_memory_m6.py:231-232`）。
- 反幻觉硬约束：`status="confirmed"` 一律拒绝（`memory.py:76-77`），召回项恒 `hypothesis`（`:475,569`；`test_memory_m6.py:136,191,304`）。
- 冲突：仅 `memory_key` 维度（第 2.2 节），派生源无冲突合并。
- 过期过滤：`valid_until`（`memory.py:401`）；撤销源 `revoked_memory_sources` 逐项过滤（`:403-409,450-457`；`test_memory_m6.py:192-196,360-382`）。
- 事实层缺口：capsule 顶层 `facts` 永为 `[]`（`context_service.py:308`；`test_context_m6.py:84` 明确断言）。可信证据只在 `tasks[].facts`/`evidence_refs`（`context_service.py:136-145`），未上卷到会话级事实。

### 2.4 Agent 间通信协议

- 消息类型是**常量集合**，不是 enum 类型系统：`WORKER_COMMANDS`（6）与 `WORKER_FACTS`（7）为 `frozenset`（`event_domain.py:16-36`），`URGENT_EVENTS`（`:37-44`）；payload 自由 JSON（`events.payload TEXT`，`migrations.py:69-72`），无 per-type schema。
- **持久化消息 + 状态机是真实的**：`events`+`outbox` 同事务（`store.py:141-158`）；`event_deliveries.state CHECK ('pending','leased','delivered','dead','skipped')`（`dispatcher_schema.py:13-20`）；`event_inbox` 带 `notice_attempts/notice_next_attempt/notice_dead_at/notice_token`（`:23-31`）；`worker_inbox`（`worker_schema.py:5-8`）在 `worker_runtime.py:1071-1096` 转成 user 消息并注明"untrusted, not authority"。
- 命令→状态迁移真实：`dispatcher.py:305-342` 把 `worker.command.pause/resume/cancel` 映射到 `paused/queued/cancelled`，校验前置状态（`:314-320`）与 Quest 授权（`:321-326`）。
- 缺口：①`worker.command.assign` 只在 `event_domain.py:18` 定义，全仓无生产点；worker 实际由 `WorkerScheduler.tick` → `manager.start` 启动（`worker_runtime.py:1886-1893`）。②无"agent→agent 直接消息"类型；`worker.help_requested` 只进 composer（`main.py:354`），限 2 次（`worker_runtime.py:763-768`）。③路由白名单硬编码在 `_routable`（`dispatcher.py:144-161`），新增消费者需改代码。

### 2.5 治理：风险分级、审批绑定、预算熔断

- **风险分级：真实且失败关闭。** `ACTION_RISKS` 固定映射 6 个动作（`governance.py:16-25`）；未知名 `DomainError("Unknown action is denied")`（`:38-42`）；R3 在申请与授权两侧恒拒（`:191-192`、`:353-354`）；R0/R1 要求 `parameters["path"]` 落在 `resource_scope` 内（`:361-367`），R1 还要匹配当前 plan_revision 的 `task_grants`（`:370-379`）。
- **审批绑定：真实且强。** 7 元组绑定（quest_id、plan_revision、action_type、parameter_digest、resource_scope、risk、policy_version，`:387-395`）；不匹配即置 `REVOKED`/`EXPIRED` 并抛错（`:396-417`）；`consumed_at IS NULL` 单次消费（`:403-406`）；过期在读取与决策两处检查（`:254-268`、`:307-311`）；完成门要求 `decided <= consumed < expires`（`evidence.py:36-52`）。工具侧有等价检查并原子消费（`tool_engine.py:369-399`）。
- **预算熔断：真实，双层次。** ①模型层：`admit` 逐次 HTTP 预留（`model_call_budget.py:145-455`），`settle` 发现超用置 `breached=1`（`:468-482`），此后 `admit` 直接 `BudgetExceeded("ledger is fenced")`（`:170-173`）；429 冷却共享（`:116-133`，`tests/test_model_call_budget.py:280`）。②Worker 层：token/cost 预留失败即 `worker.blocked`（`worker_runtime.py:1396-1447`，`reason_code=token_budget|cost_budget`），叠加 `steps/tool_spent` 上限（`:1301-1303,1643-1645`）。
- 运维缺口：`breached=1` 之后只有"重新对账"路径（`model_call_budget.py:182-193` 要求 `reconciled_cost`），**没有运营解封接口**；触发后需手改库。
- 漂移风险：风险表有两份 —— `governance.ACTION_RISKS`（`governance.py:16-25`）与各工具 `risk` 字段（`tool_domain.py:97-191`），靠"工具名 == action_type"对齐（R2 仅 `browser_get`/`mcp_call` 两表都有，`tool_domain.py:178,191`）。新 R2 工具若忘加名，审批会因 "Unknown action is denied" 失败（失败关闭，但会卡死功能）。

### 2.6 迁移与 schema 版本

- `SCHEMA_VERSION = 9`（`migrations.py:6`），`MIGRATIONS: {1..9}`（`:252-262`）；统一在 `BEGIN IMMEDIATE` 内、失败整体回滚并复验版本（`:265-292`）。
- 版本元数据强校验（单行 singleton、禁降级、禁未来版本、禁有表无版本）：`migrations.py:18-46`；测试 `tests/test_migrations.py:37-63,66-90`。
- v8 `_context_memory_budget`（`migrations.py:175-182`）组合 `context_schema.migrate_context`（3 表，`context_schema.py:4-17`）、`memory_schema.migrate_memory`（5 表 + 游标种子，`memory_schema.py:4-26`）、`model_budget_schema`。v9 `_worker_output_contract`（`:185-249`）新增 `restore_reviews/semantic_test_runs` 并**主动把在途 worker 置 blocked**（`:221-249`）——迁移改业务状态的先例，新表迁移需评估同类影响。
- **新增一张表的必要步骤（现状口径）**：①在对应 `*_schema.py` 写 `migrate_xxx(connection)`（纯 DDL，禁 `executescript`，见 `migrations.py:284` 注释）；②在 `migrations.py` 新增步骤函数并 import 调用；③`SCHEMA_VERSION` +1、`MIGRATIONS` 增键；④涉回填/状态改写时参照 `:221-249`；⑤同步 `tests/test_migrations.py:89`（断言 `SCHEMA_VERSION == 9`）、`:136` 的 `parametrize(1..9)`、`:164` 的最终版本断言；⑥若涉及备份，同步 `workspace_backup.py`（已有跨库拷贝 `:598-661`）。

## 3. 当前执行链路图（文字版，含真实函数名）

```
[1] 用户消息
    POST /api/conversation/message        forge_api/main.py:1020-1027 send_message
      → RouterService.handle_user_message  router.py:546

[2] 召回前置
    RouterService._sync_memory            router.py:88-94 (limit 100 批)
      → MemoryService.sync_sources        memory.py:147-312 (quests/events/evidence/workspace → memory_source_index)
    RouterService._snapshot               router.py:96-173
      ├─ ContextService._archives         context_service.py:241-261 (逐条 hash 校验)
      ├─ _task_context                    context_service.py:48-161
      └─ MemoryService.retrieve_context   memory.py:314-600 (词项候选 → 快照比对 → items/omitted/cursor)

[3] 压缩（如需）
    RouterService.handle_user_message     router.py:569-577 (字节>limit/2 或 条数>50 且 >10)
      → ContextService.compact_router     context_service.py:263-338
          ├─ 并发校验 current != snapshot        :281-282
          ├─ capsule 模板拼装                    :302-323
          ├─ _record_archive (三表同事务)         :176-197
          └─ router_state revision/event_cursor CAS :329-334

[4] 上下文组装 + 模型调用
    RouterService._context                router.py:175-293 (UTF-8 字节估算 + 逐类裁剪 + budget)
      → provider.generate                 router.py:686 (≤4 轮；读工具 ≤3 轮、每轮 ≤4 调用)
      → RouterService._commit             router.py:821-...
          ├─ add_message                  store.py:40-52
          ├─ save_quest (create_quest)    store.py:62-81
          └─ append_event (events+outbox) store.py:141-158

[5] 事件投递（后台 loop）
    run_loop("dispatcher")                forge_api/main.py:215, 206-207
      → Dispatcher.tick                   dispatcher.py:441-473
          → claim (materialize + lease)   dispatcher.py:163-216
          → deliver                       dispatcher.py:238-361
              ├─ event_inbox INSERT       dispatcher.py:282-296
              ├─ worker.command.* 状态机   dispatcher.py:305-342
              └─ worker_inbox INSERT      dispatcher.py:351-354

[6] 计划与团队
    run_loop("planner") → process_pending_plan      forge_api/main.py:258-...
      → QuestGraphService.propose / approve         quest_graph.py:64,159
    run_loop("composer") → process_pending_team     forge_api/main.py:350-...
      → TeamCompositionService (approve)            team_composition.py:553-588

[7] Worker 执行
    run_loop("worker") → WorkerScheduler.tick       worker_runtime.py:1802-1920
      → WorkerManager.start (预算/依赖/审批事务校验)  worker_runtime.py:309
      → WorkerManager.run → _run                    worker_runtime.py:1110-1122
          ├─ _claim (租约/代次)                      worker_runtime.py:842-960
          ├─ _checkpoint (inbox→user msg, 待办 tool)  worker_runtime.py:1068-1108
          ├─ ContextService.build_worker_context     context_service.py:375-528
          ├─ provider.generate (token/cost 预留)      worker_runtime.py:1396-1558
          └─ finish: worker.result + review_pending   worker_runtime.py:1756-1784

[8] 工具执行与证据
    ToolEngine.prepare (R0/R1/R2 授权、审批绑定)     tool_engine.py:307-435
      → _begin / _perform / _finish                  tool_engine.py:437,765,905-976
          ├─ action_receipts INSERT                  tool_engine.py:926-937
          ├─ evidence(kind='tool', trusted=...) INSERT tool_engine.py:942-961
          └─ tool.receipt_recorded 事件               tool_engine.py:962-969

[9] 独立校验与落库
    run_loop("verifier") → NodeVerifier.tick        evidence.py:607-661
      → NodeVerifier.verify                         evidence.py:709-862
          ├─ JsonVerifier.verify (固定非 shell 检查 + 哈希) evidence.py:87-168 → evidence(artifact/test/review)
          ├─ SemanticVerifier.verify                semantic_evidence.py:327-364
          ├─ CompletionGate._check_live_node (receipt/approval/产物哈希) evidence.py:370-540
          └─ gate.check → quests.status='completed' 同事务     evidence.py:842-861
```

链路断点：`/api/quests/{id}/transition`（`main.py:1183-1192`）走 `QuestService.transition`（`quest_service.py:55-88`），而 `Repository.transition_quest` 明确拒绝执行类状态（`store.py:94-105`）；唯一调用 `CompletionGate` 的 API 型入口 `QuestService.complete`（`quest_service.py:15-53`）**无任何调用方**。即"完成"只能由 [9] 的 `NodeVerifier.verify` 内部触发，"用户/前端点完成"路径不存在。当前不存在 `tests/live_*_probe.py` 之外的端到端脚本；`backend/scripts/phase2/` 下三个未跟踪脚本属 Phase 2 探针（`probe_capabilities.py` / `live_core.py` / `smoke_live_core.py`），只做能力探测与单轮 smoke，未覆盖 Quest 执行。

## 4. 与 Phase 2 要求的差距（逐条对照）

| Phase 2 能力 | 状态 | 现状与证据 | 最小必要改动（不重写） |
|---|---|---|---|
| Context 分层 L0–L3 | **部分** | L1/L2/L3 由 Core 派生（`memory.py:474,568`）；L0 仅白名单（`memory.py:70`）；工作上下文无层标签（`router.py:118-124`） | 增加 L0 产出点：为 `router_state` 未归档消息窗口在 `memory_source_index` 登记一层 `layer='L0'` 摘要（复用 `memory_terms` 与现有 `source_refs`），召回侧 `layers=` 过滤已支持，无需改 compaction |
| compaction 触发策略 | **已有** | `router.py:569-577`、`:421-433`；阈值写死 | 把 `keep_recent`/阈值/上限提到 `Settings`（`settings.py`），可按 model `context_window` 自适应；保留失败关闭 |
| 摘要生成方式 | **已有（模板）** | `context_service.py:302-323`；无 provider import | 若需模型摘要：新增独立 summarizer，输入=已归档 sources，输出仍须通过现有字段与 hash 校验；模板路径留作回退。**不要**在 `compact_router` 事务内调模型 |
| 来源引用与可恢复性 | **已有** | `context_service.py:199-220,241-261,324`；`read_source` `:530-537`；分段读回 `router.py:714-747` | 把"归档→恢复"的回归上移到 API 层（现有覆盖在 service 层） |
| constraint 抽取 | **缺失** | 无抽取代码；`TaskCapsule.constraints` 恒空 | 在 `_commit` 增加一步确定性抽取（规则 + 用户原句切片，`source_refs=message id`、`status=hypothesis`），复用现有 `remember()` 校验，不新建表 |
| constraint 索引 | **缺失** | `sync_sources` 不扫 `messages`（`memory.py:188-199`） | 若按上条写入 `memories`，`remember()` 已自动索引（`memory.py:122-124`）；写入放进 `_commit` 的同一 `unit_of_work` |
| constraint 冲突检测 | **部分（机制在，链路不通）** | `memory.py:410-430`；`remember()` 无调用方 | 与抽取一起接通即复用；补"新约束覆盖旧约束"的 `valid_until` 写入（字段已有 `memory.py:98-102`） |
| constraint 过期检测 | **部分** | 仅 `memories.valid_until`（`memory.py:401`） | 抽取时允许带 `valid_until`（`remember` 已校验时区），召回侧无需改动 |
| Recall 语义向量 | **缺失（词项）** | `memory.py:12-17,346-372`；无 embedding 依赖 | 不替换词项索引：新增可选 `memory_vectors` 表 + 在 `retrieve_context` 内做候选重排（候选仍受 512 上限），失败回退词项 |
| Recall 候选/来源可靠性/冲突/过期 | **已有（可靠性强，冲突弱）** | 候选 512（`memory.py:372`）、快照重校验（`:446-563`）、`trust_scope`（`:580`）、`valid_until`（`:401`）；冲突仅 key 维 | 冲突：对派生项按 `(quest_id, 归一化 statement)` 计算，复用现有 `conflicted` 字段，不建新表 |
| Agent 通信消息类型枚举 | **部分** | 常量 frozenset（`event_domain.py:16-36`），payload 自由 JSON | 新增 `event_payload_schemas.py`（event_type → jsonschema），在 `Dispatcher.deliver` 解析 payload 后（`dispatcher.py:244-247`）校验，失败走现有 `fail()` 死信 |
| Agent 通信持久化 + 状态机 | **已有** | `events`/`outbox`/`event_deliveries`/`event_inbox`/`worker_inbox`；命令状态机 `dispatcher.py:305-342` | 补 `worker.command.assign` 真实发出点，或在协议文档显式标注其未实现，避免清单与实现不符 |
| 风险分级 | **已有** | `governance.py:16-25,38-42`；R3 恒拒 | 消除双表漂移：`ACTION_RISKS` 由 `tool_domain.CONTRACTS` 派生，保留"未知名拒绝" |
| 审批绑定 | **已有** | `governance.py:387-395`、`tool_engine.py:369-399`；并发单次消费 `test_governance.py:30-47` | 无需改；建议补"审批→完成门"跨层用例（现仅 graph 用例覆盖 `evidence.py:225-241`） |
| 预算熔断 | **已有** | `model_call_budget.py:145-483`；worker 双预算 `worker_runtime.py:1396-1469` | 补 `breached=1` 的运营解封入口（现仅 `reconciled_cost` 路径 `model_call_budget.py:182-193`） |
| 迁移与版本 | **已有** | `migrations.py:6,252-262,265-292`；SCHEMA_VERSION=9 | 新表按第 2.6 节六步；建议把 `test_migrations.py:89,136,164` 的硬编码 9 改为引用 `SCHEMA_VERSION` |

## 5. 分类明细（为什么不是更高/更低）

- **A（真实实现 + 硬断言）**：归档完整性与回滚、Worker 上下文、风险分级、审批绑定、预算熔断、迁移回滚。
- **B（仅 Mock 验证）**：自动 compaction 的端到端形态用 `execution_mode="mock"`（`test_context_m6.py:101-116,260-285`）；结构化输出/预算预留的 wire 断言用 `httpx.MockTransport`（`test_schema_gateway_m6.py:51,17-102`）。→ 触发逻辑记 A、端到端记 B。
- **C（真实 provider 证据）**：
  - 上下文连续性（真实 HTTP，1 次调用）：`.local/phase1/milestone6/context-20261114T183632Z-53aba023/report.json`（`status=passed`、`router_identity_restored=true`、`constraints_preserved=true`、`source_readback_verified=true`、`intent=continue_quest`、`matched_original_quest=true`、`calls=1`）；前 5 次失败诊断保留在同目录与 `docs/PHASE1_MILESTONE6_REPORT.md:66-97`。
  - Worker 工具环（真实）：`.local/phase1/milestone5/live-probe/worker-20261114T170953Z-14c91e82.json`。
  - JSON 产物完成门（真实）：`.local/phase1/milestone5/live-probe/completion-20261114T171357Z-587cd661.json`（`task_status=succeeded`、`quest_status=completed`、单条 receipt）。
  - Phase 2 能力探针（真实、脱敏）：`.local/phase2/provider/capabilities-20261114T214733Z.json` 与 `...T214830Z.json`（`json_schema_mode_reliable=true`；`prompt_json_mode_reliable` 两次分别为 `false`/`true` → 不稳定，必须靠本地严格校验兜底）。
  - 以上均不构成"通用模型质量"证据：端点都是 `http://127.0.0.1:8788/v1` 的本地 OpenAI 兼容代理，模型 `deepseek-v4.1-flash`，报告内 `cost=null`（费用未知）。
- **D（接口/模型/占位）**：`MemoryService.remember`（`memory.py:51`，无生产调用方）、由它承载的冲突/过期路径、`EvidenceService.record_model_claim`（`governance.py:421-447`，无调用方）、`TaskCapsule.constraints`（`router_domain.py:30`）、`worker.command.assign`（`event_domain.py:18`）、层 `L0`（`memory.py:70`）。
- **E（目前无法端到端工作）**：①"约束抽取→索引→冲突/过期"整链（无抽取点，冲突机制空转）；②手工记忆层生产路径不可用（`memories` 空表）；③语义召回；④前端"用户点完成"路径（`store.py:94-105` 拒绝，`QuestService.complete` 无调用方）。

## 6. 测试强度评估

**强**

- 完整性：篡改消息后 `rebuild_router` 报 integrity（`test_context_m6.py:24-29`）；篡改 Worker 消息后 `build_worker_context` 抛错（`:697-698`）。
- 原子性：用 `TRIGGER ... RAISE(ABORT)` 注入失败，验证三张 context 表与 `worker_capsules.revision` 回滚（`test_context_m6.py:44-59`、`:701-737`）；approvals/outbox/events 同回滚（`test_governance.py:133-145`）。
- 并发：同审批两线程仅一次成功（`test_governance.py:30-47`）；4 线程初始化只留一个版本（`test_migrations.py:120-134`）。
- 边界：分片读回 4 段拼回原文并逐段校验 sha256（`test_context_m6.py:495-566`）；越界/超长 source 被拒（`:452-492`）；UTF-8 字节上限（`test_memory_m6.py:89-103`）。
- 迁移：每版本失败都能回滚到干净库（`test_migrations.py:136-164`），v8 组件级失败（`:167-210`）。

**弱**

- `test_memory_m6.py` 多数用例自建最小表（`:111-127,155-167,200-217,246-263,361-378`），只用真实 `Store` 的仅 `:268-313,316-332,335-357` 三例 —— 不能证明与真实 `quests/events/evidence/approvals` 列集/约束兼容。
- 语义断言以"包含关系"为主：`constraints_preserved` 用子串（`tests/live_context_probe.py:208-210`）、capsule 用 `in json.dumps(...)`（`test_context_m6.py:85,267-269`）—— 抓不住语义丢失。
- 冲突只在同 key 不同 statement 上验证（`test_memory_m6.py:89-107`），**没有**"较新可信证据覆盖较旧假设"的用例（`docs/CONTEXT_MEMORY_GOVERNANCE.md:15` 承诺了该行为）。
- 没有衡量 compaction 后模型输出质量的用例（不应由单测承担，但 Phase 2 验收需单独口径）。
- 已知空实现被断言为期望值（`facts == []`，`test_context_m6.py:84`），会阻碍后续补实现（需同步改测试语义）。

## 7. 结论

- M6 范围内，**归档式上下文与基于 Core 记录的召回是本仓最扎实的部分**：来源可恢复、哈希可验证、失败关闭、并发与回滚都有硬测试。
- **compaction 的"摘要"是确定性模板而非模型生成**，对外不要表述为"模型自动摘要"；可恢复性依赖 `read_context_source`/`read_source` 而非摘要本身。
- **constraint 是最大缺口**：无抽取、无索引、无约束级冲突/过期；现有"约束保留"=逐字归档 + `quests.metadata` 透传，真实供应商证据也只证明透传。
- **Agent 间通信是真正的持久化事件总线 + 投递状态机**，但缺"类型→payload schema"强约束，且 `worker.command.assign` 为空声明。
- **治理（风险分级/审批绑定/预算熔断）质量高**，剩两处运维缺口：`breached` 无解封入口、风险表双份易漂移。
- **schema 版本 9，迁移工程化程度高**；新增表按第 2.6 节六步即可，注意 `test_migrations.py` 中硬编码的 9。
