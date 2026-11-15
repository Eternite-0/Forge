# Phase 2 — Agent Intelligence 状态

最新验收（2026-11-15）：V24 全套 1,734 项通过、1 个已有 warning / 465.71 秒；静态与原证据保留性检查通过。strict L1/L2/L3 均在 Planner 阶段失败，15 次真实调用全部结算 87,044 Token，无 Worker 或中途重启。三个补充探针也未进入 Worker。当前 V25 修复让模型明确选择容量档位，原预算、任务标准和审批不变。Phase 2 尚未完成，以下此前记录保留；最新证据见 [V24 历史边界](audit/phase2/acceptance-independent-v24-boundary.md)。

更新：2026-11-15。V23 修复中，最终冻结快照、全套后端结果和 strict L1/L2/L3 尚未验证，Phase 2 未完成。V22 历史全套 1,592 项通过、3 项失败、1 个 warning / 352.37 秒，三层 strict 均失败。19 次调用最终结算 83,594 Token，最终未知 0；L1 三步/三工具/两修复后以 context_budget blocked，L2/L3 失败于 Planner，原时序失败标记保留。最新兼容回归是三项旧 confidence 反馈测试缺少原 `path` 字段，原断言均保留。V21 历史全套 1,560 项后端通过、1 个 warning / 445.09 秒，但三层 strict 均失败：L1 真实 Worker 三次只读工具后 blocked，L2/L3 失败于 Planner。18 次调用最终结算 83,527 Token，最终未知 0，原中途未结算失败标记保留。V20 历史全套 1,490 项通过、1 个 warning / 312.74 秒，三层 strict 均失败，22 次调用结算 108,458 Token，最终未知 0，均无 Worker。V19 已结束：1,448 项后端通过、1 个 warning / 321.62 秒；三层 strict 均在计划批准前失败。12 次模型调用全部结算 58,229 Token，未知 0，三层均无 Worker、审阅或重启。V18 已保存 1,413 项后端通过、1 个 warning / 408.37 秒与三层 strict 失败，37 次调用结算 270,119 Token。V17 已作为全套通过、三层 strict 失败的历史快照保存。

实现链路已连接：单一长期 `main` 对话 → Router → 独立 Planner → 批准的 DAG/团队 → 后台 Native Worker → 受治理工具/完整结构化交付 → 当前节点独立上下文 Reviewer → 有限返工 → NodeVerifier/CompletionGate → Router 通知。来源约束、原文归档回读、长期记忆和持久预算贯穿链路。

V17 变更：自动反馈重规划通知连接 remaining DAG 提案，保留已完成节点的原证据且要求再次批准；完整前驱交付读取不要求不存在的中间文件；三个 Core 只读工具由验收器使用原来源验证；预检包含运行时可能开放的 Core 工具 framing；明确批准的串行共享应急池不再永久累加每节点最坏额度，但保持原总 cap、未来工作基础额度和未知调用占用。

| 证据类别 | 已记录结果 | 能说明什么 |
| --- | --- | --- |
| V16 完整后端 | 1,304 passed / 5 failed / 1 warning / 328.69 s | 历史失败快照，不能证明 V17 |
| V17 Core framing/验收/驱动联合回归 | 88 passed / 47.46 s | 本地原生序列化、归档回读、Core 来源验证及旧驱动兼容 |
| V17 串行/前驱联合回归 | 48 passed / 28.15 s | 合同、未知预算拒绝、前驱完整证据及本地 Runtime |
| V17 Runtime 最终文件 | 3 passed / 6.09 s | 原生本地脚本 HTTP、两节点 PASS/重开 Store/pool-once、篡改与 global-cost 拒绝 |
| V16 真实算法小样本 | 25/90 任务成功，65 失败，135 次模型调用，111,243 已知 Token | 单模型六固定题十五变体，探索性；全部原结果保存 |
| V17 真实语义记忆单样本 | 1 调用，1,713/113 Token，未知0；原探针失败保留 | 正确目标来源检索有独立补充审计；原 oracle 漏候选，不能改原失败或外推复杂任务完成 |
| V17 完整后端 | 1,405 passed / 0 failed / 0 error / 0 skipped / 1 warning / 427.75 s | 201 文件历史冻结快照的本地回归通过 |
| V17 strict live | L1/L2/L3 均失败；各 2 次已结算调用；共 11,535/1,908 输入/输出 Token，未知0 | Planner 请求前 `Quest_is_too_large_to_plan`，无计划、Worker、产物、PASS、归档或重启；不能证明复杂任务完成 |
| V18 完整后端 | 1,413 passed / 0 failed / 0 error / 0 skipped / 1 warning / 408.37 s | 203 文件历史冻结快照的本地回归通过 |
| V18 strict live | L1/L2/L3 均失败；13/12/12 次调用结算，80,013/87,717/102,389 Token，未知0 | 每层五次 Planner inbox 处理、十条拒绝事件；`Invalid_graph_proposal`，无 Worker、产物、PASS、归档或重启 |
| V19 完整后端 | 1,448 passed / 0 failed / 0 error / 0 skipped / 1 warning / 321.62 s | 207 文件历史冻结快照的本地回归通过 |
| V19 strict live | L1/L2/L3 均失败；各4次调用结算，16,860/18,893/22,476 Token，未知0 | 每层一次 Planner inbox 处理、两条拒绝事件；无 Worker、产物、PASS、归档或重启 |
| V20 完整后端 | 1,490 passed / 0 failed / 0 error / 0 skipped / 1 warning / 312.74 s | 210 文件历史冻结快照的本地回归通过 |
| V20 strict live | L1/L2/L3 均失败；14/4/4 次调用结算，67,880/20,314/20,264 Token，共 108,458，最终未知0 | L1 批准计划后十次 Composer 调用仍未组队；L2/L3 失败于 Planner；均无 Worker、审阅或重启 |
| V21 完整后端 | 1,560 passed / 0 failed / 0 error / 0 skipped / 1 warning / 445.09 s | 216 文件历史冻结快照的本地回归通过 |
| V21 strict live | L1/L2/L3 均失败；9/5/4 次调用最终结算，40,081/21,354/22,092 Token，共 83,527，最终未知0 | L1 计划/团队获批，真实 Worker 三步/三次只读工具/两次修复后 blocked，无独立审阅或 PASS；L2/L3 失败于 Planner；原时序失败标记保留 |
| V22 完整后端 | 1,592 passed / 3 failed / 0 error / 0 skipped / 1 warning / 352.37 s | 219 文件历史冻结；三项旧 confidence 反馈测试因缺少原 path 字段失败，原断言保留 |
| V22 strict live | L1/L2/L3 均失败；10/5/4 次调用最终结算，37,977/21,759/23,858 Token，共 83,594，最终未知0 | L1 Worker 三步/三工具/两修复后 context_budget blocked，无独立审阅/PASS，新交付拒绝事件0；L2/L3 失败于 Planner；原时序失败标记保留 |
| V23 完整后端/strict live | 尚未验证 | 恢复原安全诊断字段并根据真实来源/工具失败继续修复，仍需冻结、全量测试和真实验收 |

专项集合重叠，不能相加。上述 Runtime 的本地脚本 HTTP 使用真实 Gateway/账本机制，未调用真实模型。实际模型仅通过用户授权的 workbuddy-api/wb-in `deepseek-v4.1-flash` 进行；代理成功、上游模型身份和任务结果正确性分别陈述。

严格复杂任务验收必须观察：实际 Provider、真实工具及产物、独立检查与需要的返工、压缩后的原文来源恢复、中途 Core 重启后保留原身份/审批/回执/计数并继续成功完成。V16–V19 三层任务均失败在 Planner 阶段，V20 L1 的 Composer 与 V21/V22 L1 的 Worker 局部进展也没有完成任务，不能借用更早版本局部重启成功补足 V23。安全诊断兼容及后续修复继续受原预算、65,536-byte 完整审阅上限及审批限制。

V21 L1 原始失败包含 `remaining_workers_blocked_with_unsettled_model_attempts` 和 `quest_status_running`。最终 settlement wait 记录 0.002 秒、零未结算，最终账本九次调用全部 settled；较早中途快照与精确结算时间未保存，不能由最终账本反推或撤销原标记。见[原失败与最终结算时序补充](audit/phase2/live-usage-v21-timing.md)。最终用量结算只证明账务观察，不能证明执行成功。

V22 L1 保留同类原始失败标记，最终 settlement wait 为 0.761 秒、零未结算，最终十次调用全部 settled；同样不能由最终观察回推较早时点，见[时序补充](audit/phase2/live-usage-v22-timing.md)。完整套件三项失败的原 JUnit/log、测试身份及断言绑定见[失败明细](audit/phase2/acceptance-independent-v22-test-failures.md)。归档、静态、保留性及用量校验通过不改变全套或 strict 失败。

当前限制：仅单一真实模型，真实多模型选择/多样性收益未证；一般任务质量与自动重规划效果需更广评估；真实跨 Quest 并行完成尚未独立建立。多模态输入执行不可用。代码工具是固定的有界纯函数、数值、语法检查与自有产物修订，任意 shell、通用仓库编辑和不受限测试执行未实现。未知模型/工具效果保留预留并可能阻断；费用在缺少可信定价时未知。

交付入口：[完整报告](PHASE2_AGENT_INTELLIGENCE_REPORT.md)、[实验](PHASE2_EXPERIMENTS.md)、[质量门槛](QUALITY_GATES.md)、[路线图](ROADMAP.md)及 `audit/phase2/verification.json`。V17 历史结论见[验收记录](audit/phase2/acceptance-v17.md)；V18 见[独立历史边界](audit/phase2/acceptance-independent-v18-boundary.md)和[用量审计](audit/phase2/live-usage-v18-audit.md)；V19 见[独立失败边界](audit/phase2/acceptance-independent-v19-boundary.md)和[用量审计](audit/phase2/live-usage-v19-audit.md)；V20 见[独立失败边界](audit/phase2/acceptance-independent-v20-boundary.md)和[用量审计](audit/phase2/live-usage-v20-audit.md)；V21 见[独立失败边界](audit/phase2/acceptance-independent-v21-boundary.md)和[用量审计](audit/phase2/live-usage-v21-audit.md)；V22 见[原始失败边界](audit/phase2/acceptance-independent-v22-boundary.json)、[测试失败明细](audit/phase2/acceptance-independent-v22-test-failures.md)和[用量审计](audit/phase2/live-usage-v22-audit.md)。V23 实际 manifest、完整测试、strict run ID、未通过项和最终独立审计结果尚未确认；此前原字节保存在[V19 前快照索引](audit/phase2/checkpoint-pre-v19-docs/index.json)、[V22 前四份文档快照](audit/phase2/checkpoint-pre-v22-owned-docs/index.json)及[本次更新前快照](audit/phase2/checkpoint-pre-v23-owned-docs/index.json)，全部原失败保留。
