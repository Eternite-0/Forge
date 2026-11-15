# FORGE Super-agent：底层 Harness、Context 与 Memory 审计

审计日期：2026-11-16。本文记录重构前源码事实和最小修复建议；不把参考项目视为 FORGE 的执行后端，不把单一功能或单元测试视为最终产品验收。

## 1. 依据与来源边界

FORGE 的总体产品目标来自 DeepSeek Super-agent。Codex 与 Claude Code 只提供底层执行、上下文、持久化和权限机制参考。

本轮读取的官方仓库快照：

| 来源 | 固定版本 | 实际读取范围 |
| --- | --- | --- |
| [openai/codex](https://github.com/openai/codex) | `5cb38e79842a0e0a6dca5bb97874163e8ee50129` | `codex-rs/core`、`protocol`、`exec-server`、`rollout`、Windows sandbox 与文档 |
| [anthropics/claude-code](https://github.com/anthropics/claude-code) | `ebbbadc9617ebe177d22bf64876981159a01e6a7` | README、CHANGELOG、公开设置及 hooks 示例、LICENSE |

参考克隆位于系统临时目录 `forge-superagent-harness-20261116`，没有引入项目依赖或修改前端。Claude Code 的公开仓库不是其完整核心 Harness 源码；本文对其执行循环和压缩行为的结论来自官方文档，不能描述为已经审计了私有实现。在线文档读取于审计日，不与以上 Git commit 绑定。

官方文档：

- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)：工具反馈循环、JSONL 会话、自动压缩、独立子上下文。
- [Subagents](https://code.claude.com/docs/en/sub-agents)：独立上下文、工具和权限约束、可恢复子任务、可选 worktree。
- [Sandboxing](https://code.claude.com/docs/en/sandboxing)：操作系统执行边界与 permission flow 是不同机制；shell sandbox 不包围文件工具、MCP 或 hooks；原生 Windows 当前不提供该 shell sandbox，WSL2 可用。
- [Permissions](https://code.claude.com/docs/en/permissions) 与 [Memory](https://code.claude.com/docs/en/memory)：权限配置、持久项目指令与跨会话学习。

## 2. Codex 可直接验证的机制

| 源码（上述固定 commit） | 已验证机制 | 对 FORGE 的意义 |
| --- | --- | --- |
| [`core/src/tools/orchestrator.rs`](https://github.com/openai/codex/blob/5cb38e79842a0e0a6dca5bb97874163e8ee50129/codex-rs/core/src/tools/orchestrator.rs) | 在执行前统一处理 approval、sandbox selection、network approval 和受策略约束的重试；sandbox 扩权重试须重新评价对应 authority | 复用 FORGE 既有 ToolEngine/governance 边界，不能因为 Router 自动委派就允许模型授权自己 |
| [`core/src/tools/approvals.rs`](https://github.com/openai/codex/blob/5cb38e79842a0e0a6dca5bb97874163e8ee50129/codex-rs/core/src/tools/approvals.rs) | action 绑定 environment、cwd、command/patch/MCP 参数；要求人工的 checkpoint 不能由 hook 或自动审核替代 | 保存 exact scope 与权限来源；自动执行策略应由可信 Core 根据已授予能力决定 |
| [`core/src/unified_exec/mod.rs`](https://github.com/openai/codex/blob/5cb38e79842a0e0a6dca5bb97874163e8ee50129/codex-rs/core/src/unified_exec/mod.rs)、`process_manager.rs` | 进程 ID、environment ID、受限输出缓冲、yield/poll、超时和取消；终端生命周期与模型推理分离 | 通用 shell/browser/GPU 任务需要持久进程身份与结果回收；当前 FORGE 固定命令 subprocess 不是这套能力 |
| [`core/src/session/environment.rs`](https://github.com/openai/codex/blob/5cb38e79842a0e0a6dca5bb97874163e8ee50129/codex-rs/core/src/session/environment.rs) | 线程环境默认值、workspace roots、owner-provided 配置校验 | environment 应是有权限来源的实际执行记录，不能只在 Worker prompt 写“隔离” |
| [`core/src/session/compaction_replacement.rs`](https://github.com/openai/codex/blob/5cb38e79842a0e0a6dca5bb97874163e8ee50129/codex-rs/core/src/session/compaction_replacement.rs) | 锁内校验 history generation、prefix 长度、auth owner，原子安装压缩前缀并保留期间新增 suffix | FORGE 现有 Router CAS 和 archive frontier 检查方向正确，应扩展而非替换 |
| [`core/src/compact.rs`](https://github.com/openai/codex/blob/5cb38e79842a0e0a6dca5bb97874163e8ee50129/codex-rs/core/src/compact.rs)、`session/rollout_reconstruction.rs` | 区分模型可见压缩历史与完整 rollout；恢复选择有效 compaction checkpoint 和后续记录；压缩保留真实用户输入及必要上下文 | summary 不是事实数据库；完整来源与恢复 frontier 必须保留 |
| [`rollout/src/recorder.rs`](https://github.com/openai/codex/blob/5cb38e79842a0e0a6dca5bb97874163e8ee50129/codex-rs/rollout/src/recorder.rs) | 有序写入会话 JSONL，显式 persist/flush/shutdown 确认及错误传播 | FORGE SQLite event/outbox 已有更明确事务边界，不需要为模仿参考项目另建 JSONL 权威状态 |

Claude 官方文档支持相同的工具反馈、独立子上下文、持久 transcript 和摘要交接方向，也明确摘要可能丢失早期细节。FORGE 应保留现有的可验证完整来源、权限和证据机制，而不是复制“摘要即记忆”的弱实现。

## 3. FORGE 重构前事实

| 模块 | 已可靠实现的基础 | 差距与影响 |
| --- | --- | --- |
| `context_service.py` / `context_schema.py` | SQLite 保存完整原消息；archive/capsule/source hash；Router snapshot 与 archive frontier；model handoff 的 `previous_archive` 引用链；原子提交；Worker 工具请求和响应整组压缩 | 已接近 Inline & Spill 的存储基础。但完整 message archive 不等于任意外部事件、工具大结果、任务交接都统一使用 spill 引用；应统一对象引用与按需 readback |
| `model_compaction.py` / `worker_model_compaction.py` | 真实模型摘要有独立预算和 Gateway usage 结算；摘要绑定完整输入和 frontier；失败保留原上下文；不重置累计 usage | 保持这些约束，不能为“自动压缩”增加未记账调用或在未结算时重试 |
| `memory.py` / `semantic_memory.py` / `router_recall.py` | 有界 lexical/semantic 检索；来源哈希、撤销、时效、冲突与现行 revision 检查；记忆保持 hypothesis/来源信任标签；来源索引随事务更新 | `source_refs`、`related_quest_ids` 和 archive handoff chain 尚非通用 Chain of Recall。缺少 task → worker/session → event → receipt/artifact → decision/project 的可遍历关系 API。当前 revision 过滤也不适合把过往事实与“当前授权”混为一类 |
| `worker_schema.py` / `worker_runtime.py` | 每个 run 独立 messages、capsule、inbox、provider/model、预算、lease token/generation；有界并行；lease/CAS 保护提交；过期运行可重新 claim；不确定工具结果阻止重放 | 这是会话和所有权隔离。尚无每个 Worker 绑定的执行目录、环境身份、文件变更交接及执行环境恢复记录 |
| `tool_engine.py` | exact intent/receipt、权限验证、工作区路径约束、脱敏环境、`shell=False`、输出与执行超时上界；不确定执行恢复为 `uncertain` | Python 固定 subprocess 的 cwd 仍是共享 `workspace.root`，Git 使用批准路径；没有一般任意 shell/浏览器/GPU 工具执行器。AST 子集测试是受限解释器，不是通用 Python 环境或完整 OS sandbox |
| `project_context.py` / `settings.py` | 单个数据库绑定一个稳定 workspace；workspace metadata 不授予文件权限 | 显式声明 `multiple_workspaces=False`，没有通用项目自动识别和跨项目关系。不能把现有 Quest 分组标签描述成项目发现或全局 Router |
| `forge_api/main.py::_core_lifespan` | dispatcher/router/planner/composer/worker/verifier 的独立后台循环、异常重试与退避、健康字段；恢复 fence 和 WorkspaceLock | 循环内异常会重试，但外层没有监测 task 意外完成/取消、长期卡住操作及重建子循环的 watchdog。健康字段仅表示最近一次 tick，不能代表持续前进 |
| `WorkerScheduler` / `worker_recovery.py` / `ToolEngine.recover_abandoned` | 从数据库查找待执行/过期 run；重整已结算 usage；识别 unresolved calls；保留不确定副作用边界 | 不能用统一“自动重试”覆盖这些差别；watchdog 应调用现有恢复入口并记录原因，无法证实无副作用的动作仍须保留 blocked/uncertain |

当前源码已经支持“摘要外置完整来源”的重要部分；缺口是把这些对象串成持续事件驱动系统。增加另一个 MemoryManager、SessionManager 或图数据库不会自行补齐该缺口。

## 4. 最小修改顺序

1. 在现有数据库与 `MemoryService` 上增加有界 `recall_object`/`recall_chain` 查询。对象引用使用显式类型与稳定 ID；返回来源、关系、trust、当前状态和读取指针。优先读取已有外键和事件字段，不复制权威业务表。限制深度、节点数、UTF-8 字节数和关系分页，循环用 visited set 截断。历史事实可以可见，但旧 approval/receipt 绝不自动授予当前权限。
2. 在现有 ContextService 与事件存储上统一 Inline & Spill。小的核心字段、摘要及对象引用内联，完整事件/工具交接外置；readback 重新校验 hash、workspace/run scope、offset 与 byte bound。spill 存储失败与事件提交必须原子回滚。大 payload 不得静默截断成成功结果。
3. 在现有 Worker run 增加 execution environment 绑定，Core 创建私有 scratch/output/cwd，并为通用 Workspace 记录关联根。共享文件的读写继续经过现有 grants/intents/receipts；仅有私有 scratch 目录不能被称为完整 OS sandbox。后续引入进程能力前，明确 Windows 本机、WSL/container 等边界和实际验证平台。
4. 扩展当前后台生命周期为 watchdog：记录最近开始/完成时间、连续失败、运行 generation 和恢复事件；发现退出 task 后重建原循环，识别超时并取消受控操作。恢复继续使用已有 lease fencing、Outbox lease、Gateway usage settlement 和 uncertain handling，禁止盲目重放外部动作。
5. 将以上功能接入持久 Router 的实际工具及自动执行链：用户只交代目标，Router 选择角色和模型并组织隐藏 Worker。Quest、DAG、计划/团队授权记录保留为内部可审计合同；不要求普通安全任务先经用户逐个管理它们。

## 5. 验证要求

审计阶段只读研究，没有以真实 Provider 行为冒充已完成实施。后续实现至少需要：

- 原有 context/memory/worker/tool/lifecycle 回归，尤其 CAS 冲突回滚、来源篡改、跨 run/工作区拒绝、未配对工具消息保留和不确定副作用不重放。
- Chain of Recall 的关系遍历、循环/分页/字节上限、旧 revision 历史与当前权限隔离。
- 实际运行的大 payload spill/readback、重启恢复和 Worker 私有目录隔离；不能只验 DB 存在字段。
- 注入 Router/Dispatcher/Worker 后台 task 退出和卡住，验证 watchdog 真正重建、保留 fence/usage/receipt 状态。
- 真实配置 Provider 贯通一条用户目标 → 多个隐藏 Worker → 后续订阅事件 → Router 汇总 → 重启后对象引用回忆的链路，保存调用及证据；Mock 测试和真实 Provider 结果分别报告。

相关现有回归文件：`test_context_m6.py`、`test_model_compaction_v29.py`、`test_worker_model_compaction_v29.py`、`test_memory_m6.py`、`test_semantic_memory_phase2.py`、`test_worker_runtime.py`、`test_worker_tool_integration.py`、`test_tool_engine.py`、`test_worker_lifespan_m5.py`、`test_lifecycle_m7.py`。

## 6. 本轮后续实现与局部验证

本节区分已经实施的底层改动与上文记录的重构前状态。整体编排与真实 Provider 验收另见本轮总报告。

- `recall_chain.py` 和 `MemoryService.recall_object` / `recall_chain`：直接读取已有权威对象，支持 conversation、Router、Workspace、Quest、Worker、run、event、message、plan、approval、evidence、intent、receipt、memory、archive、Trigger、thread、source、project；不另建对象副本。返回有界摘要、规范 JSON 的 UTF-8 分片、哈希和下一跳。关系可分页，链遍历限制深度、对象数、总字节并检测循环。
- Recall 的 Worker scope 由可信 run ID 固定：拒绝其他任务对象及未投递的 Trigger，只允许本 run 的订阅/投递 thread、任务关联项目、原始及更新用户请求的来源绑定。用户请求重新核对 sha256；Trigger 与 receipt 核对完整内容 hash；archive 还验证保留的原消息。历史审批和记忆保持“历史来源，不授予当前权限”的标签。Caller-owned transaction 不被提交或关闭。
- `worker_environment.py`：从已持久化 Workspace / Quest / run ID 派生私有 work、tmp、output 目录；拒绝目录 symlink/junction；可使用调用者连接为尚未提交的 Worker 注入描述符。目录身份可在进程重启后重建。实际 Python/Git 固定进程工具改为使用 run 的 cwd 与 TEMP/TMP/TMPDIR，批准输入文件和共享输出依旧经过原 scopes/grants/intents/receipts。
- `gpu_tools.py` / `gpu_status` ToolContract：无参数 R0 只读诊断，使用系统 `nvidia-smi` 与固定 inventory 参数，复用受限进程 runner。缺少驱动工具或 NVIDIA 设备明确返回 unsupported，格式错误或超时返回 unavailable；保存实际诊断输出及哈希。不包含任意命令、GPU 运算或驱动安装。

局部回归命令：

```powershell
python -m pytest backend/tests/test_recall_chain_superagent.py backend/tests/test_worker_environment_superagent.py backend/tests/test_gpu_tools_superagent.py backend/tests/test_tool_engine.py backend/tests/test_worker_tool_integration.py backend/tests/test_memory_m6.py backend/tests/test_context_m6.py -q
```

结果：**78 passed, 1 skipped**（2026-11-16）。跳过项是当前 Windows 用户无目录 symlink 创建权限时的链接攻击测试；真实子进程相对文件隔离、私有临时目录、重启恢复、未提交 run 绑定、来源篡改、跨任务拒绝及分页均已通过。GPU 测试包含实际本机查询，检测到 NVIDIA GeForce RTX 4060 Ti；这只证明只读硬件诊断，不证明 GPU 计算能力。相关新增文件的 Ruff 检查及 `git diff --check` 通过。

这些目录提供工作目录与临时文件分离，**没有建立 OS sandbox**；共享工作区文件能力仍由原权限机制约束。大对象读取有明确分片和 hash，不能把一次摘要或一次 source lookup 解释为完整跨项目产品验收。
