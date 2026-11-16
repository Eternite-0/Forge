# HTTP API 与前端入口对照

核查日期：2026-11-16。以当前 `backend/src/forge_api/main.py` 的 HTTP 路由声明和 `desktop/src/` 调用点为准，按 HTTP 方法分别计数：**50 条 `/api/*` 路由，加 1 条 `/health`**。Prompt 中的 48 条和未接入清单来自较早版本。

本表记录接口的实际 UI 入口与实现位置。具备入口不等于所有状态都已通过真实 Provider 验收；浏览器合成数据验收、单元测试和真实 Core/Provider 联调须分别记录。原生 `invoke` 命令、凭据库和文件工作区能力不属于此 HTTP 路由计数。

所有 `/api/*` 调用继续经过 [api.ts](src/api.ts) 的原有 `request<T>(connection, path, init)`，会话 Bearer 保留在内存中。UI 不改鉴权、后端请求模型、审批来源、版本与摘要绑定。

## 会话、活动、运行状态与记忆（13 条）

| # | 方法与路径 | UI 触达路径 | 实现文件 | 实际限制 |
|---|---|---|---|---|
| 1 | `GET /api/conversation` | 长期对话；加载更早消息；连接校验 | [useConversationFeed.ts](src/hooks/useConversationFeed.ts)、[api.ts](src/api.ts) | 读取当前工作空间的一条长期对话，按 `before` 分页。 |
| 2 | `POST /api/conversation/message` | Composer 发送；失败消息重试 | [useConversationSend.ts](src/hooks/useConversationSend.ts) | 使用真实 `request_id`；相同内容重试保留原 ID，网络失败不宣称请求未执行。 |
| 3 | `GET /api/timeline` | 会话中的工具调用、子智能体事件、历史加载 | [useConversationFeed.ts](src/hooks/useConversationFeed.ts)、[ChatTimeline.tsx](src/ChatTimeline.tsx) | 保留分页与历史事件来源；不生成缺失的工具输出或思考时长。 |
| 4 | `GET /api/activity` | 右侧 Agent 卡片、会话子智能体条目；运行活动抽屉 | [AgentPanel.tsx](src/components/AgentPanel.tsx)、[ConversationRuntime.tsx](src/ConversationRuntime.tsx)、[useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts) | 独立轮询约 6 秒；详情按 `run_id` 绑定，读取失败清除陈旧当前状态。已记录步数和运行成功不代表任务完成。 |
| 5 | `GET /api/events` | 运行活动 → 事件流 | [EventFeed.tsx](src/components/EventFeed.tsx) | 按 `after` 序号轮询；每批 100 条，本次查看最多保留最近 500 条。不是 SSE/WebSocket 订阅。 |
| 6 | `GET /api/settings` | 常规、运行配置；Composer 当前模型；工作空间识别 | [useConversationFeed.ts](src/hooks/useConversationFeed.ts)、[useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts)、[OperatorSettings.tsx](src/OperatorSettings.tsx) | 区分当前活动配置与已保存配置，不把已保存模型当作正在运行的模型。 |
| 7 | `POST /api/settings` | 设置 → 运行配置 → 保存 | [OperatorSettings.tsx](src/OperatorSettings.tsx)、[useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts) | 必须携带 `expected_revision`；保存后重启 Core 才生效。不会重置既有预算账本。 |
| 8 | `GET /api/restore-status` | 写入锁定横幅；设置 → 备份与恢复 | [useConversationFeed.ts](src/hooks/useConversationFeed.ts)、[WorkspaceInspector.tsx](src/components/WorkspaceInspector.tsx) | 恢复锁定不会由 UI 自动解除；记录损坏或不可用时保留人工复核要求。 |
| 9 | `GET /api/budget` | 设置 → 用量与预算；运行配置 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts)、[BudgetOverview.tsx](src/BudgetOverview.tsx) | 区分已确认用量、预留和未结算状态；未知费用不视为零。 |
| 10 | `GET /api/memory` | 设置 → 记忆；侧栏记忆入口 → 搜索/加载更多 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts)、[WorkspaceInspector.tsx](src/components/WorkspaceInspector.tsx) | 使用后端 `cursor`；展示来源、冲突、有效期与可信度。 |
| 11 | `POST /api/memory/{memory_id}/revoke` | 记忆记录 → 撤销记忆 | [WorkspaceInspector.tsx](src/components/WorkspaceInspector.tsx) | 只对可撤销的有效记忆标识展示入口；需用户确认；后台仍负责有效性检查。 |
| 12 | `GET /api/recall` | 会话 → 引用与来源；记忆页；事件详情的关联引用 | [RecallCard.tsx](src/components/RecallCard.tsx) | 使用真实 `reference`、内容偏移与关联偏移；每页内容有字节上限，历史陈述不作为授权或正确性证明。 |
| 13 | `GET /api/runtime/health` | 顶栏 → 运行健康 | [RuntimeHealth.tsx](src/components/RuntimeHealth.tsx)、[runtimeHealth.ts](src/runtimeHealth.ts) | 根据 `last_seen`、`timeout_seconds` 与组件状态判断心跳；连接成功不代表模型和权限已就绪。 |

## 任务、计划、团队与 Worker（15 条）

任务是长期对话的后台上下文。侧栏后台任务或会话内子智能体条目的“查看任务”打开右侧任务抽屉；不创建新的用户会话。

| # | 方法与路径 | UI 触达路径 | 实现文件 | 实际限制 |
|---|---|---|---|---|
| 14 | `GET /api/quests` | 后台任务列表、会话任务关联；加载更多 | [useConversationFeed.ts](src/hooks/useConversationFeed.ts)、[QuestWorkspace.tsx](src/QuestWorkspace.tsx) | 仅对已加载记录分组/计数，保留 `before` 分页。 |
| 15 | `GET /api/quests/{quest_id}` | 未加载的关联任务；任务操作与历史 → 子智能体提案 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts)、[QuestControls.tsx](src/components/QuestControls.tsx) | 返回 `{quest, workers}`；这里的 `workers` 为会话提案/身份记录，实时执行信息另读 `/api/activity`。 |
| 16 | `GET /api/quests/{quest_id}/plan` | 后台任务 → 执行计划；团队页；操作后刷新 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts)、[QuestWorkspace.tsx](src/QuestWorkspace.tsx) | 没有计划时 404 显示暂无计划；不会把成功操作后的无计划状态误报成操作失败。 |
| 17 | `GET /api/quests/{quest_id}/plan/history` | 任务操作与历史 → 计划历史 | [QuestControls.tsx](src/components/QuestControls.tsx) | 真实版本摘要与审批记录，不伪造旧版计划正文。 |
| 18 | `POST /api/quests/{quest_id}/plan/generate` | 任务操作与历史 → 生成计划/重新规划剩余工作 | [QuestControls.tsx](src/components/QuestControls.tsx) | 发送 `expected_version`；新计划限允许状态；剩余计划/节点展开仍须后端检查已完成证据、原预算和未结算运行。 |
| 19 | `POST /api/quests/{quest_id}/plan/approve` | 执行计划 → 批准此计划 | [WorkspaceInspector.tsx](src/components/WorkspaceInspector.tsx) | 绑定 `digest`、`plan_revision`、`expected_version`，确认后提交；批准不等于执行完成。 |
| 20 | `POST /api/quests/{quest_id}/plan/control` | 任务操作与历史 → 暂停/恢复/取消/为失败任务创建重试提案 | [QuestControls.tsx](src/components/QuestControls.tsx) | 按实际计划/任务状态展示。失败重试创建新 Quest，需重新批准，并非重放旧请求。 |
| 21 | `POST /api/quests/{quest_id}/priority` | 任务操作与历史 → 任务优先级 | [QuestControls.tsx](src/components/QuestControls.tsx) | 显式选择 1–5，绑定任务版本；结束任务不可调整。未实现拖拽排序。 |
| 22 | `GET /api/quests/{quest_id}/teams` | 任务团队；任务操作与历史 → 任务团队 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts)、[QuestControls.tsx](src/components/QuestControls.tsx) | 读取既有团队提案、成员、模型、工具权限和预算。 |
| 23 | `POST /api/quests/{quest_id}/teams/approve` | 任务团队 → 批准此团队 | [WorkspaceInspector.tsx](src/components/WorkspaceInspector.tsx) | 绑定 `expected_version`、`plan_revision`、`proposal_digest`；不自动发放目录或工具权限。 |
| 24 | `POST /api/quests/{quest_id}/teams/reassign` | 任务操作与历史 → 选择团队合约、执行记录、原因与策略 → 提出重新分配 | [QuestControls.tsx](src/components/QuestControls.tsx)、[questControls.ts](src/questControls.ts) | 沿用原角色、权限、预算与输出合约，转换为现有 `TeamWire`。若累计 `execution_token_budget` 无法在该 Wire 中完整表示则拒绝转换，要求回对话重新规划；后端仍检查执行所有权与未结算动作。 |
| 25 | `POST /api/quests/{quest_id}/transition` | 任务操作与历史 → 调整任务状态 | [QuestControls.tsx](src/components/QuestControls.tsx)、[questControls.ts](src/questControls.ts) | 只展示该状态允许的用户转换；`approved/queued/running/reviewing/completed` 等执行转换由可信 Core 负责，不作为手动选项。 |
| 26 | `POST /api/quests/{quest_id}/workers` | 任务操作与历史 → 子智能体提案 → 创建提案 | [QuestControls.tsx](src/components/QuestControls.tsx) | `role`、可选 `model_id` 是查询参数；仅创建 proposed Worker，不启动运行。**不存在同路径的 GET**。 |
| 27 | `GET /api/team/preview` | 任务操作与历史 → 模型候选预览 | [QuestControls.tsx](src/components/QuestControls.tsx) | 基于模型库的默认角色候选，不是任务已批准团队，也不证明模型连接可用。 |
| 28 | `GET /api/projects/discovered` | 侧栏 → 项目 → 已发现项目 | [WorkspaceSidebar.tsx](src/WorkspaceSidebar.tsx) | 只读展示发现的名称/路径；无跨工作空间切换、项目 CRUD 或访问授权语义。 |

## 审批与目录授权（9 条）

| # | 方法与路径 | UI 触达路径 | 实现文件 | 实际限制 |
|---|---|---|---|---|
| 29 | `POST /api/approvals` | 任务抽屉 → 审批中心 → 创建审批请求 | [ApprovalsPanel.tsx](src/components/ApprovalsPanel.tsx) | 用户填写精确 JSON 参数、范围、原因与可选过期时间；风险按已知操作映射。创建后仍需独立作出审批决定。 |
| 30 | `GET /api/approvals` | 审批中心 → 选择任务；加载更早审批 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts) | 任务维度查询与 `before` 分页。 |
| 31 | `GET /api/approvals/pending` | 会话内审批卡；侧栏待审批数量 | [useConversationFeed.ts](src/hooks/useConversationFeed.ts) | 当前读取最多 20 条，侧栏数量表示本次已加载待审批数，不宣称全量统计。 |
| 32 | `GET /api/approvals/{approval_id}` | 审批记录 → 审批详情 | [ApprovalsPanel.tsx](src/components/ApprovalsPanel.tsx) | 展开时认证读取，关闭/卸载时中止；连接代次变化不沿用旧详情。 |
| 33 | `POST /api/approvals/{approval_id}/decision` | 会话内审批卡；审批中心 → 批准/拒绝 | [useWorkspaceController.ts](src/hooks/useWorkspaceController.ts)、[ApprovalsPanel.tsx](src/components/ApprovalsPanel.tsx) | 绑定真实 `parameter_digest` 和 `plan_revision`；已过期记录不可在详情中决定；最终权限以服务端为准。 |
| 34 | `POST /api/approvals/{approval_id}/revoke` | 审批记录 → 撤销审批 | [ApprovalsPanel.tsx](src/components/ApprovalsPanel.tsx) | 只展示待处理或已批准且未消费的可撤销记录；撤销已执行的动作不属于此接口能力。 |
| 35 | `GET /api/quests/{quest_id}/grants` | 任务详情/审批中心 → 工作区权限 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts)、[WorkspacePanels.tsx](src/components/WorkspacePanels.tsx) | 展示当前计划版本的真实目录范围与撤销状态。 |
| 36 | `POST /api/quests/{quest_id}/grants` | 工作区权限 → 输入明确范围 → 授权此范围 | [WorkspacePanels.tsx](src/components/WorkspacePanels.tsx) | 不默认选择整个工作空间；带 `expected_plan_revision`，后端规范化并验证范围。 |
| 37 | `POST /api/quests/{quest_id}/grants/revoke` | 工作区权限 → 已授权范围 → 撤销范围 | [WorkspacePanels.tsx](src/components/WorkspacePanels.tsx) | 绑定任务、范围和计划版本；不改变其他任务的权限。 |

## 模型（7 条）

| # | 方法与路径 | UI 触达路径 | 实现文件 | 实际限制 |
|---|---|---|---|---|
| 38 | `GET /api/models` | 设置 → 模型库；Composer → 模型下拉 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts)、[ConversationComposer.tsx](src/ConversationComposer.tsx) | Composer 仅提供当前供应商已启用、未隔离模型；选择后进入运行配置预填，保存并重启后生效。 |
| 39 | `GET /api/models/{profile_id}` | 模型库 → 编辑/启停 | [useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts) | 带回 `updated_at` 供版本确认；隔离档案的拒绝保持为错误。 |
| 40 | `PATCH /api/models/{profile_id}` | 编辑模型档案 → 保存 | [ModelEditor.tsx](src/components/ModelEditor.tsx)、[useWorkspaceInspector.ts](src/hooks/useWorkspaceInspector.ts) | 仅提交发生变化的字段与 `expected_updated_at`；协议支持改动标记为用户覆盖，不冒充基准测量。 |
| 41 | `POST /api/models` | 模型库 → 添加模型 → 保存 | [ModelEditor.tsx](src/components/ModelEditor.tsx) | 新档案 ID 自动生成；未知能力、上下文和费用不自动视为支持或免费。 |
| 42 | `POST /api/model-intelligence/discover` | 模型库 → 模型发现 | [ModelIntelligence.tsx](src/components/settings/ModelIntelligence.tsx) | 显式按钮操作，依赖已配置真实 Provider；不以静态模型列表伪造发现结果。 |
| 43 | `GET /api/model-intelligence/{profile_id}` | 模型库 → 模型智能/能力详情 | [ModelIntelligence.tsx](src/components/settings/ModelIntelligence.tsx) | 展示后端记录的性能、健康和能力来源；没有样本时保持未知。 |
| 44 | `POST /api/model-intelligence/benchmark` | 模型库 → 选择模型与维度 → 能力测量 | [ModelIntelligence.tsx](src/components/settings/ModelIntelligence.tsx) | 显式触发；要求启用模型及对应 Provider，可能产生真实模型调用；失败不宣称探测成功。 |

## 触发器与历史文件（6 条）

| # | 方法与路径 | UI 触达路径 | 实现文件 | 实际限制 |
|---|---|---|---|---|
| 45 | `POST /api/triggers/sources` | 设置 → 触发器 → 事件源 → 注册 | [TriggersSettings.tsx](src/components/settings/TriggersSettings.tsx) | 事件源类型、接收规则与允许用户需保持持久标识一致；只展示本次打开页面后注册的源，后端无全量源列表端点。 |
| 46 | `POST /api/triggers` | 设置 → 触发器 → 接收事件 | [TriggersSettings.tsx](src/components/settings/TriggersSettings.tsx) | 精确提交 source、external ID、thread、payload；失败保留去重标识；接收不等于任务完成。 |
| 47 | `GET /api/triggers` | 设置 → 触发器 → 待处理事件 | [TriggersSettings.tsx](src/components/settings/TriggersSettings.tsx) | 仅最早 32 条 pending 事件，处理后移出；不是历史触发器、源或全部定时任务清单。 |
| 48 | `POST /api/triggers/schedules` | 设置 → 触发器 → 定时任务 → 创建 | [TriggersSettings.tsx](src/components/settings/TriggersSettings.tsx) | 使用已注册 timer 源，时间提交 Unix 秒，重复间隔按秒；只有创建和本次回执，无全量列表、编辑或删除 API。 |
| 49 | `POST /api/triggers/file-watches` | 设置 → 触发器 → 文件监听 → 创建 | [TriggersSettings.tsx](src/components/settings/TriggersSettings.tsx) | 仅工作空间内受后端检查的普通文件，依赖 file 源；无全量列表、编辑或删除 API。 |
| 50 | `GET /api/artifacts/receipts/{receipt_id}/diff` | 会话文件变更块 → 展开；文件记录 → 历史更改 | [artifactHistory.ts](src/artifactHistory.ts)、[InlineArtifactDiff.tsx](src/InlineArtifactDiff.tsx)、[ArtifactHistoryPreview.tsx](src/ArtifactHistoryPreview.tsx) | 精确不可变回执；无历史基线、损坏或超限时明确失败，不以当前文件/Git 伪造历史结果。 |

## 公共启动探针（1 条）

| 方法与路径 | UI 触达路径 | 实现文件 | 实际限制 |
|---|---|---|---|
| `GET /health` | 浏览器开发连接 → Core 启动密钥 → 连接 | [api.ts](src/api.ts) | 获取启动挑战并在本机内存计算 HMAC-SHA256，真实密钥不作为请求参数或 Bearer 发送，不写浏览器存储。公共健康探针只证明存活；完整组件健康另读认证端点。 |

## 验收边界与接口缺口

- 当前路径覆盖以源码和可操作入口为证据；静态映射不能代替真实 Core/Provider 的端到端验收。新接口浏览器合成场景的实际输出记录在对应测试与交付报告中。
- Worker 执行状态取自 `/api/activity`，Worker 身份/提案取自 `/api/quests/{id}` 的 `workers`。不得添加或调用不存在的 `GET /api/quests/{id}/workers`。
- 后端没有独立的会话临时模型、推理强度、Git 分支切换 API。模型选择进入配置保存流程，不能显示“即时切换已生效”；不为分支或强度添加无效控制项。
- 项目发现是只读信息，不是多空间切换、项目创建或目录授权；仍保持每个工作空间一条长期用户对话。
- 事件源、定时任务与文件监听目前缺少全量查询、修改、删除端点。UI 只显示实际存在的待处理事件和本次创建回执，不虚构持久管理能力。
- 团队重分配须能完整表达原合约。现有 `TeamWire` 无独立 `execution_token_budget` 字段，累计分配不同于单次输入/输出估算时，前端保守拒绝重建，不降低预算要求。
- 原生凭据、Core 重启、备份/恢复、当前文件与 Git 预览仍沿用现有 Tauri 命令；此重构未修改 `src-tauri/` 或 HTTP 契约。
