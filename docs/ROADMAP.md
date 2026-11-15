# FORGE 路线图

最新验收（2026-11-15）：V24 全套 1,734 项通过、1 个已有 warning / 465.71 秒；静态与原证据保留性检查通过。strict L1/L2/L3 均在 Planner 阶段失败，15 次真实调用全部结算 87,044 Token，无 Worker 或中途重启。三个补充探针也未进入 Worker。当前 V25 修复让模型明确选择容量档位，原预算、任务标准和审批不变。Phase 2 尚未完成，以下此前记录保留；最新证据见 [V24 历史边界](audit/phase2/acceptance-independent-v24-boundary.md)。

更新：2026-11-15。当前主线是 Phase 2 Agent Intelligence，V23 修复中，最终冻结、全量测试与 strict live 尚未验证。V22 历史全套 1,592 项通过、3 项失败、1 个 warning / 352.37 秒，三层 strict 均失败；19 次调用最终结算 83,594 Token，最终未知 0。L1 三步、三次工具和两次修复后以 context_budget blocked，L2/L3 失败于 Planner；原时序失败标记保留。V21 历史全套 1,560 项通过、1 个 warning / 445.09 秒，三层 strict 均失败：L1 真实 Worker 三次只读工具后 blocked，L2/L3 失败于 Planner；18 次调用最终结算 83,527 Token，最终未知 0，原中途未结算失败标记保留。V20 历史全套 1,490 项通过、1 个 warning / 312.74 秒，三层 strict 均失败，22 次调用结算 108,458 Token，最终未知 0，均无 Worker。V19 已结束：1,448 项通过、1 个 warning / 321.62 秒，三层 strict 均在计划批准前失败；12 次调用结算 58,229 Token，未知 0，三层均无 Worker、审阅或重启。V18 全量 1,413 项通过、1 个 warning / 408.37 秒；37 次调用结算 270,119 Token，三层 strict 均失败且无 Worker。V17 全量 1,405 项通过，但三层 strict 全部在 Planner 模型请求前失败，原记录保存。Phase 2 未完成。下表区分实现和验收，不把接口、提案或局部通过当作完成。

| Sprint | 已实现的范围 | 本轮验收状态 |
| --- | --- | --- |
| A：Provider 与 Agent Loop | 原生 Gateway、能力来源、逐调用预算账本、固定协议探针 | 有历史真实模型观察；最终源码三层任务待验 |
| B：Router 与 Planner | 单一长期对话、来源目标、自动 Quest/DAG、反馈重规划提案、审批后 remaining DAG | 本地回归覆盖；复杂规划成功率待真实任务结果 |
| C：动态团队 | 能力、预算和来源筛选，样本计数的运行观察，动态角色/替换，受限层次展开 | 合同与本地执行证据；单一可用真实模型不能证明模型多样性收益 |
| D：Worker | 私有循环、真实本地工具、严格交付、有限修复、已有产物版本修订、受治理纯函数测试 | 有本地错误→修订→重测证据；复杂实际模型闭环待验 |
| E：Reviewer | 当前节点独立上下文审查、完整来源包、当前 PASS 绑定、有限返工 | 同一模型的新上下文；不等于独立模型或任意正确性证明 |
| F：上下文与记忆 | 原文归档/回读、来源约束、分层词法记忆、有限候选页模型语义选择 | 本地及单样本真实观察；完整复杂连续性待当前 strict live |
| G：协调与恢复 | 十类 ACP v2 负载合同、来源身份、租约/去重、已知用量一次结算、证据保留 | 本地重开/恢复已测；未知外部效果继续保留预留并阻断 |
| H：三层真实任务 | 原始 L1/L2/L3 驱动、持久证据重验、真实文件/独立审阅/重启检查 | V17–V22 历史失败；V23 尚未验证，任何失败均保存，不降低成功标准 |
| I：算法与消融 | 固定多领域团队/压缩/治理基线、离线验证、90 行真实小样本及独立评分 | 探索性结果25/90成功；不宣称策略普遍优越 |

V17 新增批准成功路径祖先的完整交付、原生工具 framing 预检和 serial-capacity-v1。后者在原任务总额内共享一次应急池并保留未来基础工作额度，不扩大总 Token、工具、步骤、时间或费用授权。只读 Core 读取使用严格原来源验证，不冒充文件回执。

本轮完成门槛是同一冻结源码上的全量回归、静态检查、依赖检查、保留性审计及当前 strict L1/L2/L3。复杂场景必须有实际产物、独立检查以及中途 Core 重启后的连续执行；预期的失败、返工、压缩、原文回读也必须在实际记录中观察到。缺失观察不能写成通过。

V18 的原始失败与只读账本重建见 [独立历史边界](audit/phase2/acceptance-independent-v18-boundary.md)及[用量审计](audit/phase2/live-usage-v18-audit.md)。V19 见[独立失败边界](audit/phase2/acceptance-independent-v19-boundary.md)和[用量审计](audit/phase2/live-usage-v19-audit.md)。V20 的 210 文件历史归档与原 manifest 精确匹配，L1 计划批准后失败于 Composer，L2/L3 失败于 Planner，见[独立失败边界](audit/phase2/acceptance-independent-v20-boundary.md)和[用量审计](audit/phase2/live-usage-v20-audit.md)。V21 的 216 文件历史归档同样匹配，L1 没有独立审阅或 PASS，见[独立失败边界](audit/phase2/acceptance-independent-v21-boundary.md)、[用量审计](audit/phase2/live-usage-v21-audit.md)及[原失败与最终结算时序补充](audit/phase2/live-usage-v21-timing.md)。最终结算不能撤销较早失败标记或证明任务完成。

V22 的来源预览和准确反馈没有带来任务验收通过；完整套件暴露原 confidence 诊断 `path` 字段缺失的三项兼容失败，原断言均保留，见[独立失败边界](audit/phase2/acceptance-independent-v22-boundary.json)、[测试失败明细](audit/phase2/acceptance-independent-v22-test-failures.md)、[用量审计](audit/phase2/live-usage-v22-audit.md)和[时序补充](audit/phase2/live-usage-v22-timing.md)。219 文件历史归档、静态与保留性检查通过不替代失败的全套或 strict 任务。

V23 修复聚焦恢复原安全诊断字段，并根据真实来源/工具失败继续修复；继续保留原预算、65,536-byte 完整审阅上限、原成功标准与审批，实现本身不构成通过。V19 前文档原字节保存在[快照索引](audit/phase2/checkpoint-pre-v19-docs/index.json)，另有[V22 前四份文档快照](audit/phase2/checkpoint-pre-v22-owned-docs/index.json)及[本次更新前快照](audit/phase2/checkpoint-pre-v23-owned-docs/index.json)。

后续能力方向：更广泛任务质量评估、多模型选择与独立审阅模型、更多任务/难度的统计研究、受治理的通用代码编辑执行、真正的多模态输入链路。它们不应被现有局部实现或单模型样本提前宣布完成。

桌面 Alpha、Tauri 生命周期和发布继续按 Phase 1 Milestone 7 的独立范围推进；本 Sprint 不承担 UI 视觉重构、安装包和发布。Phase 1 Milestone 1–7 的原报告、原始路线图与所有历史验收结果在审计目录中保留，不追改其结论。
