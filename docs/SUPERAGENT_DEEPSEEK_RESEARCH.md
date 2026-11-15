# DeepSeek Super-agent：官方依据与 FORGE 架构差异审计

审计日期：2026-11-16。本文是后端重构之前的基线审计，不表示下列缺口已经修复。

## 研究依据与边界

- 官方架构文章：[Exploring Super-agent Design: Towards a More Autonomous Agent System](https://x.com/DeepSeekHarness/status/2116857257245987236)。读取了 DeepSeek Harness 官方账号发布的完整英文机译正文（页面注明中文为原文），包括第 1–9 节；这里的依据是技术文章，并非另一个已经验证的学术论文。
- 官方原型：[deepseek-ai/dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype)，固定源码版本 [`8c32a9dc5d72c033816c6a8110b37f40cf94fba4`](https://github.com/deepseek-ai/dsh-superagent-prototype/tree/8c32a9dc5d72c033816c6a8110b37f40cf94fba4)。源码仅克隆到系统临时目录，没有添加为 FORGE 运行依赖。
- 重点读取原型 README、`docs/000-philosophy.md`、`001-domain.md`、`002-authorization.md`、`003-execution.md`、`007-architecture.md`，并核对 `src/core/intake.ts`、`src/app/assessment.ts`、`src/app/cards.ts`、`src/app/context.ts`、`src/app/runtime.ts`、`src/app/sessions.ts`、`src/executor/dsh.ts`、`src/app/retry-health.ts`、`src/infra/health.ts`。
- FORGE 基线：当前 README；`router.py`、`router_domain.py`、`runtime.py`、`dispatcher.py`、`event_domain.py`、`worker_runtime.py`、`worker_recovery.py`、`worker_schema.py`、`migrations.py`、`project_context.py`、`tool_engine.py`、API 启停与后台循环。Context/Memory 的独立审计应与本文合并使用。

官方 README 明确说这是研究原型，非 DeepSeek 官方产品，且公开代码与内部版本、文章描述不完全相同；有些设计仍属未来方向。不能把文章每一项能力都说成原型已经实现，也不能把原型的软件工程细节当成 FORGE 必须照搬的接口。

## 产品方向必须采用的结论

文章第 1 节定义四个特征：Continuous、Productive、Proactive、Persistent。中心不是一张任务图或多个 Agent，而是用户始终面对同一个能够持续工作的实体。第 2.1 节进一步区别 Super-agent Router 与单任务 Agent Team Lead：Router 保持全局顺序点、记忆和交互，只调度独立任务，不深入分解和执行每项任务。复杂任务内部可以由 Worker 自己组织 Team/DAG（第 2.2 节），而这些执行细节不应成为用户管理 Session 的负担。

因此 FORGE 可以继续保留 Quest、计划、团队合同、审查和审批记录；它们是执行与权限基础设施，不能再决定产品交互的主语。现有可靠数据库和证据机制不需要推翻，缺的是把这些基础设施自动连起来的事件驱动控制流。

## 文章、原型与 FORGE 的逐项差异

| 模块 | 官方文章要求 | 官方原型源码证据 | FORGE 重构前状态与差距 |
| --- | --- | --- | --- |
| Persistent Router | 第 2.1 节：长期主 Session，所有外部交互与跨任务协调；轻量、不执行任务；积压事件可批处理 | `src/app/runtime.ts` 的启动流程调用 `sessions.ensure(masterId, stateDir, ..., 'orchestrator')`；候选与结果通过持久 Effects 投递到同一个主 Session | `store.ensure_router()`、`router_state`、`router_turns`、CAS 和上下文归档已经存在。`RouterService._commit()` 创建 Quest 提案后默认等待 review；`continue_quest` 仅关联状态，不向正在运行的 Worker 交付后续指令。需要从“分类并登记提案”变成“授权范围内创建、订阅、推进任务并总结结果” |
| Isolated Workers | 第 2.2 节：独立任务独立上下文、执行工具；Worker 不直接对用户说话；复杂 Worker 可以包含团队 | `src/executor/dsh.ts` 的 `workerSessionId`、`ticketExecution`、`prepareDirectory`、`launch`：每 Ticket 一个持久 DSH Session 与目录；`docs/003-execution.md` 解释并发槽与后台工作 | `WorkerManager` 有持久 run/messages/capsule、所有权 lease/generation、有限修复、证据；Worker 绑定 `plan_id/node_key/team_digest`，`WorkerScheduler.tick()` 从 approved plan/team 寻找任务。`ToolEngine` 多个 subprocess 的 cwd 仍是全局 `store.workspace.root`，上下文隔离不等于执行目录隔离。需要补任务环境描述与目录边界，保留现有权限验证 |
| Fast Path / Slow Path | 第 3.1 节：确定性来源/actor/规则过滤；只有需语义判断的候选进入模型；第 2.1 节支持积压合批 | `Intake.receive()` 先 `scope.scope(trigger)`；`Policy.gate` 决定确定性准入；`MasterAssessor` 提供模型 assessment；`Evals` 按 Thread 串行并合批 | `EventEnvelope` 与 Dispatcher 有类型/引用校验、优先事件、持久投递，但事件主要来自内部 Quest/Worker 生命周期，缺少通用来源、外部事件身份、线程及准入策略。`handle_pending_events()` 只处理固定通知类型，并仅合并同 Worker 的 progress。需要统一 Trigger 入口和来源级确定性过滤，复用现有事件与 Outbox |
| Thread Subscription / Pass-through | 第 3.2 节：Thread 与 Worker 多对多；同一流后续事件直接扇出；不相关事件可 handback；输出仍经 Router | `src/core/intake.ts:Intake.receive` 读取 `thread.subscriptions`、持久 Input 扇出、人类 follow-up 有 owner 时不进入 Eval；`docs/002-authorization.md` 明确订阅仅传递上下文，不赋新权限；`Tickets.handback` 重新排队 | `Dispatcher._routable()` Worker 分支只接受 `WORKER_COMMANDS` 且 `row.worker_id == consumer.worker_id`。没有 Thread/Subscription 持久关系。`WorkerManager.deliver()` 有去重 inbox，但不是自动订阅路由，且 16 KiB payload/64 inbox 上限须配合 Spill。需加小型订阅表和原子扇出到现有 Worker inbox；订阅绝不等于权限 |
| Inline & Spill | 第 3.3 节：对象摘要、必要邻接信息内联；长文留外部可寻址存储，提供继续读取引用 | `cards.ts` 的 `triggerCard/ticketCard/ticketDetails/threadCard/decisionCard` 带 preview、read、关联 ID；`textPage` 返回精确分页和 next_offset；`assessment.ts` 将正文、邻近消息和 stub 分别限额 | FORGE 已有 ContextService、source IDs、精确 source framing、worker source reads 和 compaction，应复用；新 Trigger/Thread/订阅结果也必须给出可解析引用，不能只截断 payload 后丢原文。模型输入可以压缩，原始事件/结果不能被破坏 |
| Chain of Recall | 第 3.3 节：从当前对象沿 Trigger→Thread→Worker→其他 Thread 等关联逐跳恢复上下文；每次 recall 继续返回邻接引用 | 原型未命名独立“Recall Manager”，但 `cards.ts:ticketDetails` 已返回 origin trigger、decision、threads、assignments、inputs、result、transcript 等资源链；相应资源按需读取 | FORGE 有任务、消息、证据与记忆来源的关联，并支持检索；重构前缺少 Trigger/Thread/Worker 订阅对象链与统一的可读对象资源协议。应为现有对象补引用投影，不能用一个全新向量库或总结代替可靠源记录 |
| Persistent State | 第 5.1 节：compute 可替换，Session 状态独立持久；不能把 in-memory task 当真相 | `Engine` 在事务中写领域对象与 durable Effects；`Runtime` 先目录锁、再 Store；DSH session persistence 分开承担 session log；`DshExecutor.resume()` 从存储恢复 Assignment | FORGE 已有 SQLite 顺序迁移、事务 Outbox、Inbox、lease/fencing、CAS、worker checkpoint、预算 ledger、未知外部效果处理。这些是现成优势。需持久化新增 Trigger/Thread/订阅、过滤结果、调度与 supervisor 状态；恢复时不能重放未知效果 |
| Watchdog | 第 6 节：独立于主系统的外部 Control Loop/Watchdog Session，基础设施存活与任务实质进展都要检查，可处理瞬时故障并继续复查 | 固定版本未发现独立 Watchdog Session 或目标偏移判断；`HealthBoard` 和 `RetryHealth` 是状态投影，`docs/003-execution.md` 定义 transient retry 与重启恢复，不等于完整文章 Watchdog | `forge_api.main._core_lifespan.run_loop()` 捕获异常重试并写内存 `background_health`；`WorkerScheduler.tick()` 回收过期 lease。没有独立 supervisor、持久 heartbeat、卡住后台 task 的替换、环境探针或跨组件任务进展审计。应先补可恢复外部监督边界；只加一个 health endpoint 不能宣称达到第 6 节 |

## 必须明确区分的未实现与部署约束

1. **原型运行在 DeepSeek Harness 上。** `Sessions`、inbox、后台 shell/subagent 等底层能力依赖 DSH 包，FORGE 必须自行实现对应 Harness，不能把调用 DSH CLI 当成自己的 Worker Runtime。
2. **原型默认单 GitHub repository。** GitHub/Feishu 是 Source 实现，概念本身可复用，不能把 Source/Thread 设计退化为 GitHub-only；FORGE 的非 Git Workspace、文件、定时、终端、浏览器、MCP、GPU 是自己的扩展。
3. **完整独立 Watchdog 不在固定原型中。** retry-health 不检查 Worker 是否偏离任务目标；观察到 retry/health 代码不能据此说已实现文章第 6 节。
4. **云 Agent Loop、BYOM endpoint daemon 与跨设备恢复属于文章架构方向。** 固定原型的本地目录和 DSH Session 管理不证明其已经实现文章第 5 节全部部署形态。FORGE 用户要求本地优先，可先把执行环境接口与持久状态分开，无须新增云服务。
5. **目录隔离并非安全沙箱。** 原型文档明确 Executor 以操作者 OS 身份执行命令、无需逐命令审批。FORGE 不应照搬这一权限默认，应保留自己的 scopes、intent/receipt、审批和 restore fence。
6. **原型保留条件性人类介入。** `Mandate` 表达授权范围，外部输入不能改变权限；超出范围的 follow-up 要 handback，不是默认自动执行所有评论。消除频繁内部 plan/team 确认不等于删除外部动作权限。
7. **原型允许操作员直接打开 Executor Session。** 这是调试/接管能力，不能当作 FORGE 日常产品入口；用户仍应只与 Router 对话。
8. **文章实验不能证明通用任务正确率。** 第 7.1 节 coding work 为耗时模拟，5 次运行主要测调度；pass-through 的总 cache-read tokens 从 31.8M 降到 23.2M，但任务耗时基本一致。第 7.2 节 Router 用真实模型、Worker 用 mock。FORGE 自身的真实 Provider 测试必须单独记录。

## 最小改动顺序

1. **把原有可靠机制作为不可回归的底座。** 保留 Router CAS、事件与 Outbox 同事务、lease/fencing、未知外部效果的保守恢复、权限 scopes、工具证据和预算账本。新增对象只进入现有迁移链，不启用另一个数据库或 Manager 层。
2. **统一 Trigger 与 Thread。** 在现有事件系统旁补少量关系表，持久记录来源、外部 identity、Thread、准入结果、关联对象以及必要原文引用。来源适配只产生事件，不直接执行工具。过滤结果同样可审计、可去重。
3. **打通订阅与 Worker inbox。** 新任务启动时原子建立订阅；后续同 Thread 事件原子扇出，不必每次 Router 推理。Worker 对超范围事件提供 handback；Router 能观察概要而不重新决策全部 follow-up。明确终态/暂停/取消/手动停止语义，防止恢复已撤销任务。
4. **把 plan/team 审批从日常入口收回内部。** 先提供受 trusted host 策略约束的自动计划/组队/启动链路，仍调用原来的合同校验。Router 不获得伪造用户审批能力；需要用户决定的高影响动作仍停在现有真实授权边界。多个独立任务应创建多个隔离 Worker，任务内 DAG 继续存在。
5. **补执行环境与对象 Recall。** 先支持本地普通目录、Git 可选，给 Worker 固定执行上下文与 artifact 位置；保持来源只读和显式写授权。Trigger/Thread/Quest/Worker/decision/event 的摘要附关联 ID、内容长度与可精确分页读取地址，沿引用恢复历史。
6. **监督实际运行。** 后台组件定期写持久 heartbeat；独立监督循环识别 task 已退出或长期不推进，恢复安全任务，保留未知效果停顿。检查执行环境可达性。再在具备证据的边界上增加模型进展判断，不能用未经证明的总结自动终止工作。

## 本轮实施所需验收证据

- 零模型调用地过滤不匹配来源，去重同一外部事件；候选才进入 Router Slow Path。
- 两个独立任务得到不同 Worker 上下文和执行目录；同 Thread 多订阅可扇出；重复投递不产生重复 inbox 消息。
- 初始触发后连续 follow-up 进入原 Worker，Router 的 Provider 调用数不随同 Thread follow-up 增长；手回 Router 路径可用。
- 真实 Provider 从用户入口接收任务，能够自动启动隐藏 Worker、接收结果并由同 Router 汇总；记录模型、配置、调用/使用量与失败，区分真实和 mock。
- 关闭并重开同一数据库后 Router identity、订阅、未读消息、Worker checkpoint、原始对象与事件去重状态保持。
- 对后台 task 异常退出、租约超时、环境不可达、已知可重试失败和未知副作用分别测试；未知效果不得自动重放。
- 大正文经 Inline & Spill 后可按引用完整重建；历史对象至少完成 Trigger→Thread→Worker/Quest→执行记录的多跳恢复。
- 现有事务、CAS、审批、证据、预算和工具权限测试必须回归；前端文件保持不变。

此文只报告研究和重构前证据，未运行原型，也不把静态阅读当作真实模型或端到端验证。
