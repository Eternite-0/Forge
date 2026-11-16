# FORGE 续期能力实验对比（只读汇总）

本次只读取六份原始报告和持久化任务描述，新增 Provider 调用、测试执行、数据库写入均为 0；未修改源码或 Provider 配置。每行的原报告字节 SHA-256、复合源码哈希、任务材料、验收标准来源和证据类别见 [JSON](capability-continuation-comparison.json)。原报告没有 `criteria` 或 `class` 字段，汇总中的对应信息明确标为派生字段。

同源 L2 的复合源码为 `b595f77bba6215278c74bc59cbfdc850b7d7c81680877dac3adf80864cf140cc`，材料哈希相同，调用/Token/时间上限相同，每个条件仅一个样本。复合源码哈希包含后端生产 Python 和三份实验脚本，不是驱动单文件哈希。

| 条件（原始报告） | 原始结果；实际角色 | 调用 / 已知 Token | 事件跨度（秒） | 工具成功/失败 | 自修复事件 / Worker返工计数 | 审查 |
|---|---|---:|---:|---:|---:|---|
| [Single db92c8](20261115T231841Z-capability-single-db92c8.json) | passed；Research ×1 | 13 / 154299 | 80.891 | 7 / 1 | 1 / 2 | NEEDS_REVISION ×1，PASS ×1 |
| [Fixed fe94ad](20261115T232055Z-capability-fixed-fe94ad.json) | failed；Research ×1，Architect 未启动 | 15 / 170378 | 277.845 | 8 / 3 | 3 / 4 | NEEDS_REVISION ×2 |

Fixed 选择了 Research → Architect，但停在 `review_pending` / `running`，失败码为 `bounded_scenario_deadline`；它是未完成的双专家条件。Single 的独立检查通过结构和精确引用校验，明确未评估语义质量；Fixed 没有记录独立产物检查。两者 pass-through 均通过，模型调用全部 settled，未知模型用量为 0；金额和币种仍为 null。预算、审查返工和等待状态使本次差异无法完全归因于组队策略，也不能宣称单 Agent 普遍更优。

不同源码/联合负载的动态团队记录单独观察：

| 原始报告；复合源码前缀 | 原始结果/失败码 | 实际团队（未启动下游） | 调用 / 已知 Token | 事件跨度（秒） |
|---|---|---|---:|---:|
| [300fa0](20261115T221734Z-capability-continuity-300fa0.json)；8c9e002e | failed / router_notice_dead_lettered | L2 Research+Architect（Reviewer 未启动）；L3 Coding | 40 / 345739 | 117.516 |
| [4ccdec](20261115T231351Z-capability-continuity-4ccdec.json)；b595f77b | failed / core_api_status_409 | 两项任务均未准入 | 3 / 11439 | 14.332 |
| [251a4f](20261115T235303Z-capability-continuity-251a4f.json)；217fda1d | failed / router_did_not_create_independent_task | L2 Research+Architect（Reviewer 未启动）；L3 未准入 | 31 / 332091 | 115.789 |
| [e02ee2](20261116T002629Z-capability-continuity-e02ee2.json)；8aebd638 | failed / workers_stopped_without_progress | L2 Research（Architect 未启动）；L3 Coding | 28 / 290808 | 149.170 |

300fa0 的原始记录包含 pass-through、同状态 Core 重启、真实历史压缩和 typed Recall，但 Recall 未完整；L3 宿主 holdout 七项通过，原始最终代码测试回执绑定失败且 Router 最终通知失败，因此整体仍失败。e02ee2 的两个 pass-through 通过，Worker 分别因 `context_budget` 和 `tool_unresolved` 阻塞。后补产物检查不合并进原始验收结果；模型调用结算不代表未知工具副作用已经解决。

表中“事件跨度”保留 `observed_event_span_seconds` 含义，不能当作端到端 wall latency 或 CPU 时间；原始 harness elapsed 在 JSON 另列。自修复事件与 Worker 返工计数不能相加。回执完整性只校验存储哈希和绑定，不能代表语义证据完整率。所有记录恢复事件分母均为 0，恢复有效率未知。动态记录存在源码、聚合调用上限、上下文声明和准入阶段差异，不计算合并成功率或策略排名；完整单 Agent / 固定团队 / 动态团队同源对照仍未完成。
