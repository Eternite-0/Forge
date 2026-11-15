# B — Worker / Tool / Evidence 只读审计（FORGE）

- 分支：`codex/phase1-continuous`（审计开始时 `git status` 干净；审计期间出现他人未跟踪产物 `backend/scripts/phase2/`，非本次审计写入，本次唯一写入是本文件）
- 审计方式：纯只读阅读源码；只读命令：`pytest --collect-only`（backend/tests 共 488 项）、`pytest -q` 跑下述 6 个离线测试文件（86 passed in 24.29s，无 provider 依赖）
- 审计日期：2026-11-15
- 证据引用格式：`相对路径:行号`。全部结论均可回溯到源码行或落盘证据文件。

---

## 1. 结论速览（分类矩阵）

| # | 能力 | 分类 | 一句话结论 |
|---|---|---|---|
| 1 | Worker 持久循环（租约/步数/预算/崩溃恢复） | **A** | 真实实现，48 项 `test_worker_runtime.py` 覆盖，断言强度总体强 |
| 2 | 工具执行引擎（意图/回执/不可变） | **A** | 真实实现，23 项 `test_tool_engine.py`，含真实 sqlite/子进程 |
| 3 | 证据门槛（CompletionGate / NodeVerifier） | **A**（结构校验部分） | 真实实现，14 项相关测试（4+3+7）；只校验结构与固定子进程检查 |
| 4 | Worker 模型多步循环 + 工具选择 | **B** | 所有断言都在 scripted mock model 下完成 |
| 5 | 端到端 单节点完成（file_write + json_valid） | **C**（很窄） | 有真实 provider 落盘证据，仅 1 节点、仅 JSON 合法性 |
| 6 | 真实 provider 上的 worker 工具循环 | **C**（很窄） | `live_worker_tool_loop_observed`，quest 未完成（running） |
| 7 | Worker 自我修正 / reflect | **缺失** | 无 reflect/repair 步；任一工具失败即 blocked |
| 8 | Worker 交付协议（findings/decisions/…） | **D** | 默认 schema 只有 `summary` 一个字段 |
| 9 | 独立模型 Reviewer | **缺失** | 只有固定子进程校验；`review` 证据由结构校验器代写 |
| 10 | worker.progress / artifact_ready / review_requested | **D** | 类型已声明、无生产者 |
| 11 | 通用任务端到端自动完成 | **E** | 需人工批准 plan+team(+R1 grant)，且成功判据仅两种前缀 |

---

## 2. Worker 真实循环的结构

### 2.1 状态机（持久化，非内存）

状态枚举：`queued | running | paused | blocked | cancelled | review_pending | failed | succeeded`
证据：`backend/src/forge_core/worker_schema.py:15`

迁移全部在 `store.unit_of_work()` 事务内完成，且同步三张表（worker_runs / workers / task_nodes）：

- start → `queued`：`worker_runtime.py:511-545`（插入 worker_runs）、`:657-669`（`worker.proposed` 事件）
- claim → `running`（租约 fencing）：`worker_runtime.py:842-959`，token 在 `:944-948` 写入，`lease_until=min(now+lease, deadline)`
- 每次模型步前重新 fence 校验：`_fenced` `worker_runtime.py:961-989`（校验 quest.status=='running'、plan.digest、plan_revision、team 输出契约）
- 结束 → `review_pending`：`worker_runtime.py:1756-1767`（同时写 `worker.result` 事件 `:1768-1782`）
- 失败/阻塞 → `blocked|failed`：`_fail` `worker_runtime.py:1029-1066`

补充：capsule 里另有 `next_action ∈ {model, tool, review}` 标签（`:645`、`:1186`、`:1297`、`:1753`）。它只是持久化标记，**没有任何代码读取它来驱动"review 阶段"**（全仓 grep `next_action` 仅 context_service 投影用途）。

### 2.2 步数上限与预算（三重有界）

| 有界项 | 来源 | 代码位置 | 触发行为 |
|---|---|---|---|
| 步数 | `member.max_steps`（默认 4） | `worker_runtime.py:378`；检查 `:1301-1303`；每模型步 +1 `:1450` | `_fail('step_budget')` |
| 工具调用数 | `member.max_tool_calls`（默认 1） | `:377`；检查 `:1643-1645`、`:1688-1689` | `_fail('tool_budget')` |
| token | `member.estimated_tokens` / 节点 budget | `:368`、`:1422-1447` | 置 `blocked` + `token_budget` |
| 金额 | `max_cost_microunits` + operator price bound | `:411-437`、`:1398-1421` | `blocked` + `cost_budget` |
| 时长 | `max_duration_seconds` 与 plan 上限取 min | `:379-391`；claim 过期 `:891-914` | `failed` + `deadline` |

预算三层账本（global / quest / team）：`_reserve` `worker_runtime.py:291-307`，表结构 `worker_schema.py:40-47`。

### 2.3 工具选择方式：由模型选择，且**必须有 prompt + 工具 schema**

- 工具定义来自 Core 契约过滤：`ToolEngine.definitions(allowed)` `tool_engine.py:148-157`
- 传给 provider：`ModelRequest(tools=definitions, model_capabilities={"tools": True} if definitions else None)` `worker_runtime.py:1489`、`:1496`
- 系统提示措辞（含"你的结果是未经核验的声明"）：`worker_runtime.py:629-643`
- 用户消息仅含 objective / success_criteria / ids / upstream_receipts：`:583-595`

也就是说：**Worker 确实需要 prompt 让模型选工具**，并且依赖 provider 的 tool-calling 能力与 `capability_sources` 声明（`:488-495`；registry 来源校验在 `models` 表 profile）。模型回复中的 `tool_calls[].name` 必须落在 `allowed` 白名单内，否则 `_fail('invalid_tool_call')`（`:1646-1655`）。

### 2.4 两个"Core 内部工具"绕开 ToolEngine

- `worker_read_context_source`：`worker_runtime.py:1129-1137` → `_read_worker_source_slice` `:25-63`（带 sha256、offset、trust="untrusted"）
- `worker_read_action_receipt`：`:1139-1152` → `_read_action_receipt` `:66-199`（数十项联合校验通过后才返回）
- 二者**不产生 action_intents/action_receipts**，由 `evidence._verify_worker_context_call`（`evidence.py:55-84`）用"重算 + 与 tool message 逐字节比对"的方式复核。

---

## 3. Worker 自我修正（现状：几乎没有）

### 3.1 错误分类（真实存在且细分）

`_fail` 的 reason_code 覆盖面（`worker_runtime.py`）：
`context_budget`(:1391)、`invalid_tool_scope`(:1394)、`token_budget`/`cost_budget`(:1443)、`provider_unavailable`(:1473)、`provider_mode_changed`(:1476)、`model_error`(:1570)、`usage_unknown`(:1582)、`help_requested`(:1625)、`invalid_model_output`(:1633)、`incomplete_model_output`(:1641)、`tool_budget`(:1644)、`invalid_tool_call`(:1654)、`duplicate_tool_call`(:1669)、`invalid_output_contract`(:1682)、`step_budget`(:1302)、`context_source_rejected`(:1199)、`tool_error`(:1236)、`tool_unresolved`(:1250)、`tool_output_too_large`(:1262)、`approval_required`(:1227)、`lease_expired`(:1118)。

### 3.2 关键缺口：模型拿不到失败反馈

- 工具返回非 `succeeded`（例如 `numeric_ast` 语法被拒 exit_code=1、HTTP 5xx、MCP error）→ 直接 `_fail('tool_unresolved')` 并退出循环（`worker_runtime.py:1241-1256`）。**失败结果不会作为 tool message 回灌给模型**，因此不存在"看到编译错误再改一版"的自我修正。
- 最终 JSON 不符合 `output_schema` → 立即 `_fail('invalid_output_contract')`（`:1677-1683`），没有"再给一次机会修正"的第二次请求。
- 模型异常 → `model_error`；usage 异常 → `usage_unknown`；均为终态阻塞。

### 3.3 重试是否有界：有界，但**是人工触发的、非模型自驱动的**

- `WorkerManager.retry` `worker_runtime.py:785-840`：仅允许 `blocked` 且未过 deadline；未解决的工具调用（running/uncertain/failed）直接拒绝（`:795-799`）；失败类重试上限 **2 次**（`:810-817`，按 `worker.retry` 事件计数）；授权类重试不计入失败次数（`:800-808`）。
- 保留已花费预算与原始审批（`:818-826`）。
- 测试断言：`backend/tests/test_worker_runtime.py:931-953`（第三次抛错、且 token_spent 不被重置）。

### 3.4 reflect 步骤

全仓 grep `reflect|reflection|critique` = 0 命中。**不存在 reflect/self-critique 环节**。

---

## 4. Worker 交付协议（实际字段）

### 4.1 真实存在的结构化字段

- 交付物 = 一次"无 tool_calls 的 assistant 文本"，用 `jsonschema.validate(response.text, capsule["output_schema"])` 校验（`worker_runtime.py:1677-1683`）。
- 默认 `output_schema` 只有 `summary`：
  `team_composition.py:106-111`；worker 侧的默认兜底同构：`worker_runtime.py:614-622`。
- capsule（系统侧契约，不是模型交付物）字段：objective / success_criteria / expected_artifact / constraints(allowed_tools, token_limit, tool_limit, deadline, help_allowed) / responsibility / input_contract / output_schema / acceptance_criteria / ids（`worker_runtime.py:596-628`）。
- 证据引用由 Core 追加，不由模型声明：`worker_capsules.evidence_refs` 追加 `{receipt_id, execution_mode}`（`:1286-1299`）。
- 事件载荷保留原文 claim：`worker.result.payload.claim = response.text`（`:1778`）。

### 4.2 字段对照（需求 → 现状）

| 期望字段 | 是否存在 | 证据 |
|---|---|---|
| `summary` | ✅ 唯一默认必填 | `team_composition.py:106-111` |
| `findings` | ❌ 全仓 0 命中 | grep `findings` = 0 |
| `decisions` | ❌ Worker 层不存在（仅 router/context 的 decisions） | `context_service.py:114`、`router.py:503` |
| `artifacts` | ⚠️ 仅隐含于 `file_write` 回执（resource_scope + sha256），模型不申报 | `evidence.py:489-493` |
| `evidence` | ⚠️ 仅 Core 侧 `evidence_refs`（receipt_id 列表） | `worker_runtime.py:1286-1299` |
| `unresolved` | ❌ 无（有内部 blocking 语义，无交付字段） | grep `unresolved` 仅错误处理 |
| `confidence` | ❌ Worker 交付无（domain 层另有 confidence） | `domain.py:144` |
| `next_actions` | ❌ 全仓 0 命中；`next_action` 是 capsule 侧标签 | grep `next_actions` = 0 |

结论：**交付协议实质只有 `{summary}`**，与 Phase 2 期望的 8 字段差距是"缺 6、半有 2"。

---

## 5. 工具集清单与权限模型

契约固定于 Core，模型无法注册（`tool_domain.py:1`；测试 `test_tool_engine.py:494-498`）。`ToolEngine._parameters` 强制"参数键集合必须精确等于 required"（`tool_engine.py:164-165`）。

| 工具 | 风险 | 真实执行? | 权限/前置 | 实现位置 |
|---|---|---|---|---|
| `file_read` | R0 | ✅ 真实读盘（≤256KiB） | workspace scope；路径穿越/敏感名拒绝 | `tool_engine.py:774-779`、`:188-204` |
| `file_write` | R1 | ✅ 真实写盘，**O_EXCL 只新建、永不覆盖** | 需 `task_grants` 覆盖 scope（`:344-352`） | `:780-794` |
| `directory_list` | R0 | ✅ 真实 scandir（≤1000 项） | workspace scope | `:795-803` |
| `directory_create` | R1 | ✅ 真实 mkdir | 需 grant | `:804-806` |
| `shell_inspect` | R0 | ✅ 真实子进程，但参数仅 `operation=python_version`，argv 硬编码 `[python,-I,--version]` | process:diagnostic | `tool_domain.py:137-145`、`tool_engine.py:809-811` |
| `python_compile` | R0 | ✅ 真实子进程 `compile()` 语法检查（不执行） | process:diagnostic | `:812-820` |
| `numeric_ast` | R0 | ✅ 真实子进程 + 自研 AST 解释器 | 白名单 AST 构造 | `:821-832`、`numeric_harness.py:225-287` |
| `git_status` | R0 | ✅ 真实 git（`--no-optional-locks`、钩子禁用、仅 [core] 配置、拒绝 alternates/commondir） | workspace scope | `:833-856`、`:714-763` |
| `browser_get` | R2 | ✅ 真实 HTTPS（DNS pinning/公网地址校验/10s 截止/256KiB） | 需一次性 approval + host allowlist（env） | `:881-902`、`:489-609`、`tool_engine.py:52-91` |
| `mcp_call` | R2 | ✅ 真实 JSON-RPC over HTTPS（server/tool 双白名单） | 需 approval + 运营方 MCP allowlist | `:885-901` |
| `worker_read_action_receipt` | R0 | ✅ Core 内联（不落 receipt） | 需在 team/node/grant 三处 scope 内 | `worker_runtime.py:66-199` |
| `worker_read_context_source` | R0* | ✅ Core 内联（条件注入：有归档才有） | 仅限本 run 归档 | `:25-63`、`:1313-1337` |
| `worker_request_help` | 非契约工具 | ✅ 内联处理（最大 2 次/run；总事件上限 2） | 需 `help_allowed` 且 depth 未耗尽 | `:1338-1354`、`:1584-1626`、`:718-783` |

权限模型三档 + 两处硬约束：

- R0：scope 内只读（或纯诊断子进程）；R1：需 `task_grants`（`governance.py:103`）；R2：需 `approvals`（policy_version + parameter_digest + resource_scope + 未消费 + 未过期，`tool_engine.py:353-399`）。
- 硬约束 1：Core 安装目录/凭据目录/私钥后缀禁止写（`tool_engine.py:190-204`），测试 `test_tool_engine.py:275-291`。
- 硬约束 2：一旦设置金额上限，`browser_get`/`mcp_call` 直接被拒（无可信价格界）`worker_runtime.py:446-449`。

工具层不可用能力（对 Phase 2 重要的空白）：**没有"修改已有文件"的工具**（`file_write` O_EXCL），没有 patch/apply、没有测试运行器（只能 compile/numeric_ast）、没有任意 shell。

---

## 6. Evidence 体系

### 6.1 receipt（不可变）

- 表：`action_intents` / `action_receipts`，含 CHECK 约束与 **immutable triggers**（`tool_schema.py:16-41`）。
- 写入：`ToolEngine._finish` `tool_engine.py:905-976`（同时写 `evidence(kind='tool')` `:951-961`）。
- 参数绑定：`parameter_digest(parameters)`（`governance.py:28-35`）在 prepare（`:323`）、_begin（`:456-459`）、_perform（`:771-772`）三处复核，改参即拒。
- 幂等：同 call_id 重放返回既有 receipt（`:1043-1056`），测试 `test_tool_engine.py:164`。

### 6.2 hash

- receipt：`result_digest = sha256(result_json)`（`tool_schema.py:28`、`tool_engine.py:934`）。
- 工具输出自带哈希：file_read/file_write `sha256`，子进程 `stdout_sha256/stderr_sha256`，HTTP `sha256`（`tool_engine.py:779/790/704-711/605`）。
- 证据链复核时重算：`evidence.py:451`（receipt digest）、`:363`（artifact 现读现算 vs 记录 hash）。

### 6.3 plan revision / plan digest 绑定

- intent 记录 `plan_id, plan_revision`（`tool_schema.py:19-20`）；`_context` 每次执行都校验 `plan.revision==quest.plan_revision==run.plan_revision` 且 `plan.digest==run.plan_digest`（`tool_engine.py:242-254`）。
- 节点证据校验要求 `run.plan_revision==quest.plan_revision` 且 `run.plan_digest==plan.digest`（`evidence.py:379-384`）。
- artifact evidence payload 内嵌 `plan_revision`（`evidence.py:142`），校验时比对（`:361`）。
- semantic 证据额外校验 `plan.digest == sha256(canonical(graph_json))`（`semantic_evidence.py:129-131`）。

### 6.4 "trusted tool" 的定义（严格）

1. 契约由 Core 硬编码，不可注册/不可扩展（`tool_domain.py:1`、`test_tool_engine.py:494`）。
2. `evidence.trusted=1` **仅当** `execution_mode=='live'` 且 `state!='uncertain'`：`tool_engine.py:957`。
3. 输出内容一律视为不可信，只有"来源可核验"：`worker_runtime.py:198`（`trust: "untrusted tool output; receipt provenance verified"`）；归档消息 `:62`；上游 receipt 引用列表 `:578-582`。
4. mock 收据不能证明 live 完成：`evidence.py:432-436`；测试 `test_tool_engine.py:458-474`。
5. R2 动作还要求 approval 在"消费时刻"仍合法：`evidence.py:36-52`、`test_execution_evidence.py:78-94`。

### 6.5 完成门槛（fail-closed）

- `CompletionGate.check` `evidence.py:182-241`：任一节点未终态即拒（`:210`）；failed 节点必须有成功的 failure 分支（`:197-206`）；skipped 节点必须有未满足条件（`:207-208`、`:567-598`）。
- `_check_live_node` `evidence.py:370-540`：必须存在 live+`review_pending|succeeded` 的 run；最后一条 assistant 消息不得含 tool_calls 且必须过 schema（`:401-408`）；`worker.result` 事件必须存在（`:418-423`）；每个工具调用必须有 `state=succeeded` 且 live 收据（`:438-453`）；kind='tool' 的证据 payload 必须与 receipt/digest/state 一一对应（`:463-474`）；`file_write` 收据登记 produced[path]=(sha256,receipt_id)（`:489-493`）。
- 判定成功判据只接受两种前缀：`json_valid:` 或 `semantic_numeric:scheduler-v1:`（`evidence.py:503-508`、`:736-742`）。其它自然语言判据直接拒（测试 `test_node_verifier_m5.py:186-190`）。

---

## 7. Reviewer / Verifier 现状

**结论：只有结构校验 + 固定子进程检查，没有独立模型审查。**

- `JsonVerifier`：固定 argv `[python,-I,-c,<固定校验脚本>]`，caller 不能传命令或结果（`evidence.py:16-17`、`:93-141`）；写 `execution_runs(kind='json_validation')` + `evidence(artifact,test,review)`（`:121-159`）。同一份 payload 同时写 `test` 与 `review` 两行（`:155-159`）——**"review" 是命名，不是审查**。
- `SemanticVerifier`：固定 suite `scheduler-v1`（10 条硬编码用例，`semantic_suite.py:11-26`），子进程 `-m forge_core.semantic_suite`，前后两次 binding 相等 + 可执行文件哈希不变才落库（`semantic_evidence.py:244-289`），并写 `semantic_provenance`（`:313-336`）。同样向 `test`/`review` 各写一行（`:362-366`）。
- `NodeVerifier.tick`：公平认领 + 最多 3 次尝试（`evidence.py:607-661`），失败一次即 `blocked('criterion_or_provenance_failed')`（`:644-645`），基础设施异常按 `pending` 最多重试 2 次（`:646-647`）。
- 唯一的"模型参与"发生在 Router 的 notice 路径：`worker.result` 属 URGENT_EVENTS（`event_domain.py:37-44`），Router 用 `NoticeDecision` 决定 silent / update_user（`router.py:333-349`、`:450-505`），写回唯一会话消息（`:519-530`）。这条路径**不校验 claim 的正确性**，只做"是否给用户一句话"。
- 因此 `PRESET_ROLES` 里的 `reviewer`（`team_composition.py:93`）只是"团队里的一个角色"，不是自动审查器；没有任何代码把 reviewer 的产出接到 `review` 证据上。

---

## 8. 当前执行链路图（文字版，标真实函数名）

```
[用户消息]
1  POST /api/conversation/message
   └ forge_api/main.py:1020 send_message
2    └ RouterService.handle_user_message            forge_core/router.py:546
       ├ _snapshot / _context / 4 轮 tool 循环       router.py:567,578,607
       ├ RouterService._commit                       router.py:821
       └ 落库：quests 行 + 事件 quest.proposed        router.py:879

[事件投递]
3  Dispatcher.tick（每 tick 注册 router/planner/composer/worker:*）
                                                  forge_core/dispatcher.py:441-473
   └ Dispatcher.deliver → event_inbox 行           dispatcher.py:238-361
     └ 若 consumer 是 worker:*：写 worker_inbox 并直接改 worker_runs 状态
                                                  dispatcher.py:299-354

[规划 + 团队（均需人工批准）]
4  main.py:258 process_pending_plan
   └ QuestGraphService.propose（DAG 环检测 graph_domain.py:62-82）
   └ 事件 quest.plan_proposed                     quest_graph.py:145
   └ POST /api/quests/{id}/plan/approve（人工）    main.py:1113
     └ QuestGraphService.approve → quest.plan_approved（task_nodes=proposed）quest_graph.py:192
5  main.py:346 process_pending_team
   └ TeamCompositionService.propose → team_proposals(proposed)
   └ POST /api/quests/{id}/teams/approve（人工）    main.py:1156
   └ POST /api/quests/{id}/grants（R1 工具必需，人工）main.py:787

[Worker 执行]
6  WorkerScheduler.tick                            forge_core/worker_runtime.py:1802-1920
   ├ 扫描已批准 team_proposals → WorkerManager.start  :1863-1897 / :309-670
   │   └ 写 worker_runs(queued) + capsule + system/user 消息
   └ asyncio.create_task(WorkerManager.run)          :1916
7  WorkerManager.run → _run                          :1110 / :1122
   ├ _claim（租约 token，fencing）                    :842-959
   ├ 循环体 _checkpoint                               :1068-1108
   ├ 待执行工具：ToolEngine.execute                    tool_engine.py:978-1099
   │   ├ prepare（R1 grant / R2 approval / intent）    :307-435
   │   ├ _begin（二次 scope+digest 校验）              :437-487
   │   ├ _perform（真实 IO/子进程/网络）               :765-903
   │   └ _finish → INSERT action_receipts + evidence(kind='tool', trusted=live) :905-976
   ├ 否则：build_worker_context → provider.generate    :1382-1384 / :1552-1558
   ├ tool message + evidence_refs 追加                 :1267-1299
   └ 无 tool_calls：schema 校验 → state=review_pending + worker.result :1677-1782

[独立校验与完成]
8  main.py:213 verify_tick → NodeVerifier.tick        evidence.py:607-661
   └ NodeVerifier.verify                              evidence.py:709-862
      ├ JsonVerifier.verify（固定 JSON 校验子进程）     evidence.py:87-168
      │   └ INSERT execution_runs + evidence(artifact,test,review)
      └ 或 SemanticVerifier.verify（scheduler-v1）     semantic_evidence.py:244-373
   └ CompletionGate._check_live_node（同事务内复核）    evidence.py:370-540
   └ task_nodes=succeeded / worker_runs=succeeded / workers=succeeded :810-824
   └ 全部节点终态 → CompletionGate.check → quests.status='completed' :838-861
      + 事件 quest.status_changed

[回显给用户]
9  Router notice 路径（worker.result ∈ URGENT_EVENTS）
   └ RouterService._handle_notice → NoticeDecision → add_message
                                                  router.py:399-544 / :519-530
```

链路断点（真实存在）：
- 步骤 4/5 之间**必须人工批准**两次（plan + team），R1 工具还需第三次人工 grant。无任何自动推进路径。
- 步骤 7 的失败分支不会回灌模型（见 §3.2），链路在该节点直接 `blocked` 等人工 `retry`。
- 步骤 9 是"模型决定要不要说一句话"，与证据正确性无关。

---

## 9. 与 Phase 2 要求的差距

### 9.A 本任务重点清单（六项）

| 能力 | 状态 | 依据 | 最小必要改动（不重写） |
|---|---|---|---|
| Worker 真实循环（状态机/步数/工具选择） | **已有** | `worker_runtime.py:1110-1784`；48 测试 | 无需改；建议补 `invalid_output_contract`/`tool_unresolved` 的失败路径测试（当前 0 覆盖） |
| Worker 自我修正（错误分类/有界重试/reflect） | **部分**（分类与有界重试已有，reflect 缺失） | 分类见 §3.1；`retry` `:785-840`；reflect 0 命中 | 在 `:1677-1683` 前插入"1 次 schema 修复轮"：把 `jsonschema.ValidationError.message` 作为一条 system/user 消息追加并 `continue`，仅允许 1 次（用现有 `steps` 计数即可，无需新状态）。工具失败同理：把 `result_json`（含 exit_code/stderr 摘要）作为 tool message 回灌，允许 `max_repair=1` 次，不新增表。 |
| Worker 交付协议（8 字段） | **缺失**（仅 summary） | `team_composition.py:106-111` | 只改 `DEFAULT_OUTPUT_SCHEMA`：加 `findings[]/decisions[]/unresolved[]/confidence(number)` 为 required 或 optional，`additionalProperties:False` 保持；`evidence` 继续由 Core 从 `evidence_refs` 注入（模型不可自证）。同步 `worker_runtime.py:614-622` 兜底 schema，避免两处漂移。 |
| 工具集与权限模型 | **已有**（11 契约 + 2 内联 + 1 help） | `tool_domain.py:91-194` | 若 Phase 2 需要"改代码"：新增一个 `file_patch`（R1，带 `expected_sha256` 前置条件，写入前比对现文件 hash）比放开 `file_write` 覆盖更小、更可审计。 |
| Evidence 体系（receipt/hash/revision/trusted） | **已有** | §6 全部行号 | 无需改结构；建议把 `evidence_refs` 的 `execution_mode` 改为从 receipt 反查而非 `:1293` 取 run 字段，减少一处可漂移点。 |
| 独立 Reviewer（真实模型审查） | **缺失** | `evidence.py:155-159`、`semantic_evidence.py:362-366` 的 review 行是结构校验代写 | 最小改动：新增 `ReviewerVerifier`，复用 `WorkerManager` 的 provider + `evidence(kind='review')` 表；输入= claim + receipt 列表 + artifact hash，输出固定 schema `{verdict, reasons[]}`；只在 `_check_live_node` 里"若节点声明 `review_model:` 前缀"时调用，不改变现有 json_valid 路径。 |

### 9.B ROADMAP Phase 2 条目（`docs/ROADMAP.md:33-38`）

| 条目 | 状态 | 依据 | 最小必要改动 |
|---|---|---|---|
| TaskNode / 依赖 / 环检查 / DAG 调度 | **已有** | 环检测 `graph_domain.py:62-82`；依赖就绪 `worker_runtime.py:336-346`；条件分支 `evidence.py:567-598` | 无 |
| Worker inbox/outbox + 幂等消费者 | **已有** | `worker_inbox` `worker_schema.py:5-8`；去重 `worker_runtime.py:695-716`；投递 `dispatcher.py:351-354`；测试 `test_worker_runtime.py:1779-1806` | 无 |
| Progress coalescing + 重要事件直投 | **部分**（消费端有合并逻辑，**生产端缺失**） | 合并逻辑 `router.py:353-360`；但 `worker.progress` 无任何生产者（grep 全仓仅定义/消费） | 在 `worker_runtime.py` 每步结束（`:1300` 的 `continue` 之前）追加一条 `worker.progress` 事件（payload: step/tool_spent/token_spent，无内容）。Router 侧零改动即可生效。 |
| Quest pause/resume/cancel/update | **部分** | pause/resume/cancel 真实（`control` `:991-1027`、命令事件 `quest_graph.py:261-282`、调度器同步 `worker_runtime.py:1812-1835`）；`worker.command.assign/request_status/request_revision` 仅有类型定义无生产者（`event_domain.py:16-25`）；"update" 无实现 | 若 Phase 2 只需 pause/resume/cancel，则已够；若要"运行中改任务"，需新增 `worker.command.request_revision` 生产端 + 一次不消耗 token 的 plan revision 检查点（复用 `_fenced`）。 |

---

## 10. 测试覆盖强度评估（A 类内部的强弱）

| 测试文件 | 项数 | 强点 | 弱点 |
|---|---|---|---|
| `backend/tests/test_worker_runtime.py` | 48 | 覆盖租约竞争、崩溃遗留工具不确定化不重放（`:1744-1778`）、预算不重复计费（`:488-558`）、并发两 manager 只计一次（`:1994-2057`）、inbox 去重（`:1779`）、按角色输出上限（`:150-179`）、归档上下文切片重组（`:1584`） | 全部 `execution_mode='mock'`（文件头 `:1`，`ScriptedMock` `:50-67`，`MockToolEngine` `:70-88`）；只断言 1 个 reason_code（`:2126`）；缺失 `tool_unresolved`/`invalid_output_contract`/`step_budget` 断言 |
| `backend/tests/test_worker_tool_integration.py` | 1 | 唯一"mock 模型 + 真实 ToolEngine + 真实磁盘"的闭环（`:120-134`，断言 3 条 mock 收据与真实文件内容） | 仅 1 例；仅 file_write/file_read/numeric_ast；断言 `state=='review_pending'`，未走到完成 |
| `backend/tests/test_tool_engine.py` | 23 | 真实子进程（`:176-197`）、R1 grant 缺失即拒（`:138-163`）、receipt 触发器不可删（`:165-169`）、R2 审批与 intent 原子回滚（`:200-231`）、双线程只能消费一次（`:234-272`）、放弃执行不重放（`:294-332`）、git 配置劫持拒绝（`:501-516`）、DNS 截止（`:519`） | 网络工具靠 monkeypatch `_network_request`（`:411-415`），真实 HTTPS 路径未在测试中走通 |
| `backend/tests/test_execution_evidence.py` | 4 | 真实执行固定校验器 + artifact 变更后失效（`:35-37`）+ 环境变量剥离（`:67-75`） | 不覆盖 `_check_live_node` 的 receipt 全链路 |
| `backend/tests/test_evidence_gate.py` | 3 | 直接 CAS 竞态被拒（`:15-20`）、模型自证不可信（`:23-33`） | 门槛覆盖面窄 |
| `backend/tests/test_node_verifier_m5.py` | 7 | 全链路 settle + artifact 事后篡改被 Recheck 拒（`:159-183`）、不可验证判据单次 blocked（`:216-225`）、过期 claim 不能完成（`:228-255`） | 模型是 `ScriptedProvenanceStub`，自称 `execution_mode='live'`（`:124-143`，文件头 `:1-5` 明示"不声明真实模型调用"）→ 端到端完成属 **B**，不是 C |

### C 类（真实 provider）证据文件

- `.local/phase1/milestone5/live-probe/worker-20261114T170953Z-14c91e82.json`
  `mode="live"`, `status="live_worker_tool_loop_observed"`, `quest_status="running"`，`receipts[].execution_mode="live"`（含 numeric_ast 真实子进程 argv/cwd）。→ 证明"真实模型 + 真实工具循环"，**未完成 quest**。
- `.local/phase1/milestone5/live-probe/completion-20261114T171357Z-587cd661.json`
  `status="live_json_artifact_completion_verified"`, `execution_mode="live"`, `task_status="succeeded"`, `quest_status="completed"`, `token_spent=1640`, `tool_spent=1`，`evidence` = tool/artifact/test/review 各 1 且 trusted=1，`independent_runs` 含 `kind="json_validation"`。→ 唯一的"真 provider 端到端完成"证据，**仅 1 节点、仅 JSON 合法性**（脚本 `backend/tests/live_completion_probe.py:1-5` 自述）。
- `.local/phase1/milestone5/live-probe/protocol-*.json`：协议探测；其中 `protocol-20261114T214542Z-bf596302.json` 的 `status="partial_or_failed"`（负面结果也已保留）。
- 这些脚本需 `FORGE_PROVIDER_KEY_LIVE_TEST` 且 loopback-only（`live_worker_probe.py:26-35`），本次审计**未运行**（按要求不跑 provider 测试）。

§ 未发现任何针对 `semantic_numeric:scheduler-v1` 的真实 provider 落盘证据（docs 亦 0 提及），该路径只有测试内 scripted 覆盖。

---

## 11. 最小改动优先级建议（都不是重写）

1. **P0 交付协议**：`team_composition.py:106-111` + `worker_runtime.py:614-622` 两处默认 schema 加 `findings/decisions/unresolved/confidence`（约 20 行）。
2. **P0 一次修复轮**：`worker_runtime.py:1677-1683` 插 1 次 schema-feedback 重试；工具失败在 `:1241` 改为回灌 tool message + `max_repair=1`（约 30 行，不动状态机）。
3. **P1 `worker.progress` 生产者**：`:1300` 前追加事件（约 10 行），即可让既有 coalescing 生效。
4. **P1 独立 Reviewer**：新 `ReviewerVerifier`（复用 provider + `kind='review'` 表），按节点判据前缀 `review_model:` 选择性启用。
5. **P2 `file_patch`（R1 + expected_sha256）**：解决"无法修改已有文件"这一最硬的工程限制。
6. **P2 失败路径测试**：补 `tool_unresolved` / `invalid_output_contract` / `step_budget` 三个 reason_code 的断言，把当前只有 1 个 reason_code 的断言面补到可回归水平。
