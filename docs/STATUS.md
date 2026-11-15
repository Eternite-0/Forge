# FORGE 项目状态

当前落地（2026-11-16）：后端 V30 已接入任务适配交付/输出容量、两次有界修复及预取后可执行工具呈现，累计 Token 上限默认关闭。V29 的八个失败用例已修复并定向通过。最新真实 L1 进入独立审阅和返工，15 次调用全部结算，但最终内容判断仍未通过；本次未跑 L2/L3 或全量验收。前端由另一个会话维护。见 [最新交接](PHASE2_HANDOFF.md)。以下为保留的历史记录。

最新验收（2026-11-15）：V24 全套 1,734 项通过、1 个已有 warning / 465.71 秒；静态与原证据保留性检查通过。strict L1/L2/L3 均在 Planner 阶段失败，15 次真实调用全部结算 87,044 Token，无 Worker 或中途重启。三个补充探针也未进入 Worker。当前 V25 修复让模型明确选择容量档位，原预算、任务标准和审批不变。Phase 2 尚未完成，以下此前记录保留；最新证据见 [V24 历史边界](audit/phase2/acceptance-independent-v24-boundary.md)。

更新：2026-11-15。分支：`codex/phase1-continuous`。

**V23 修复中，最终冻结源码、完整后端套件和 strict L1/L2/L3 尚未验证，Phase 2 未完成。** V22 历史完整套件为 1,592 项通过、3 项失败、1 个已有 warning / 352.37 秒，三层 strict 也均失败。三项旧测试保留原断言，暴露 confidence 反馈缺少原 `path` 字段的兼容回归。19 次实际模型调用最终全部结算，共 83,594 Token，最终未知用量 0；L1 Worker 在三步、三次工具和两次修复后以 `context_budget` blocked，L2/L3 失败于 Planner。原始中途未结算失败标记继续保留。当前任务集中于 Agent Intelligence；桌面前端由另一个窗口维护，安装包与发布不属于本轮交付。

FORGE 保留一个长期 `main` 对话，Router 是唯一面向用户的 Agent。后端已经连接模型驱动 Router、独立 Planner、审批后的 DAG/团队、后台 Worker、受治理工具、独立上下文审阅、返工、上下文归档和原文回读。记忆包含来源约束、词法检索以及有预算的模型语义选择。计划、团队、危险工具及证据门槛继续由 Core 执行。

V17 补齐了自动反馈重规划入口：Router 可针对停止的部分完成 DAG 生成 `quest.replan_requested`，后台 Planner 再提出保留完成证据的 remaining DAG。新计划和团队仍须明确批准，事件本身不授权执行。受约束的层次展开要求新的计划/团队审批，不能增加父节点原预算、工具或深度。

V17 的完整前驱交付工具重验明确批准的成功路径祖先、原始结果事件、八字段完整交付及原证据。交付仍是未验证模型声明，后继必须执行自己的任务并通过自己的审查。归档原文、前驱回执和完整交付读取由验收器按各自来源证据验证，不伪造文件动作回执，也不满足最终文件要求。

新增 `serial-capacity-v1` 是明确批准的串行共享应急额度，仅支持成功单链、并发一和无费用上限的 Token 预算。每个后续节点的初始工作基础额度受到保留；共享池只登记一次。实际请求仍由原 Gateway 账本约束，已花费、未结算和未知调用不会退款。应急池耗尽会阻断任务，不能保证所有节点最坏情况下的审阅、修复都成功。

本地专项证据包括：[Core 请求与验收兼容 88 项](audit/phase2/core-framing-driver-v17-regression.xml)、[串行/前驱 48 项联合回归](audit/phase2/serial-native-regression-v17.xml)、[串行 Runtime 最终 3 项](audit/phase2/serial-runtime-v17-final.xml)。这些集合有重叠，不能相加为完整套件。原生 Gateway 的本地脚本 HTTP fixture 使用实际账本、工具、审阅和重开 Store；其技术 `execution_mode=live` 不表示实际模型或真实任务成功。

历史 V16 全套保留 1,304 passed、5 failed、1 个已有 warning / 328.69 秒，三层 strict live 均失败于 Planner 提案阶段。V16 固定算法研究的 90 行全部完成模型阶段，但任务成功仅 25/90；65 个失败保留，135 次真实模型调用，输入/输出 91,487/19,756 Token。不能把完成调用或正确账本当成任务成功。

V17 单次跨任务语义记忆探针使用真实模型和原 Gateway，1 次调用、1,713/113 输入/输出 Token、未知 0。模型选择了正确来源的决定和约束；原探针因候选枚举漏掉自动生成的同来源约束而记为失败，原结果保留。独立补充审计可说明实际来源/信任保持，不能追认原探针全项通过、重抽样本或证明复杂 Quest 完成。

当前仍需分别验证：复杂目标的实际产物正确性、失败/返工/压缩/重启后的完整连续性、多个模型选择的效果，以及真实多 Quest 并行完成。图像/音频/视频输入执行不可用；明确转入 `needs_input` 只说明诚实处理能力限制。任意 shell、无限制 Python、一般仓库补丁/测试执行不属于现有受治理工具范围。未配置可信价格时费用保持未知。

V17 全套 1,405 项通过、1 个已有 warning / 427.75 秒；三层 strict 均在 Planner 模型请求前以 `Quest_is_too_large_to_plan` 失败，原失败保留。V18 L1/L2/L3 分别使用 80,013 / 87,717 / 102,389 Token，共 13 / 12 / 12 次已结算调用。每层保留五次 Planner inbox 处理和十条计划拒绝事件。其 203 文件源码归档与原 manifest 精确一致；只读数据库审计均为零修改。见 [V18 独立历史边界](audit/phase2/acceptance-independent-v18-boundary.md)和[用量审计](audit/phase2/live-usage-v18-audit.md)。

V19 全套 1,448 项通过、1 个已有 warning / 321.62 秒。每层保留一次 Planner inbox 处理与两条拒绝事件，L1/L2/L3 分别使用 16,860 / 18,893 / 22,476 Token、各四次已结算调用，共 58,229 Token，最终未知 0，三层均无 Worker、独立审阅或重启。五次拒绝为可执行 verifier ID 校验失败，L3 修复后的末次拒绝为完整来源回执需 35,444、声明 35,439 canonical bytes。207 文件历史归档、冻结 manifest、全套测试与只读用量审计一致，见 [V19 独立失败边界](audit/phase2/acceptance-independent-v19-boundary.md)。

V20 全套 1,490 项通过、1 个已有 warning / 312.74 秒，三层 strict 均失败。L1 批准计划后十次 Composer 调用仍未组队，L2/L3 失败于 Planner，均无 Worker。22 次调用全部结算 108,458 Token，最终未知 0；210 文件历史归档精确匹配原 manifest。见 [V20 独立失败边界](audit/phase2/acceptance-independent-v20-boundary.md)和[用量审计](audit/phase2/live-usage-v20-audit.md)。

V21 全套 1,560 项通过、1 个已有 warning / 445.09 秒，三层 strict 均失败；18 次调用最终结算 83,527 Token，最终未知 0。216 文件历史归档、原 manifest、全套测试和只读用量审计一致，见 [V21 独立失败边界](audit/phase2/acceptance-independent-v21-boundary.md)和[用量审计](audit/phase2/live-usage-v21-audit.md)。L1 `20261115T102752Z-L1-6a4082` 的 Worker 在三步、三次只读工具和两次修复后 blocked，没有独立审阅或 PASS；L2 `20261115T102753Z-L2-ba61b0` 和 L3 `20261115T102753Z-L3-913aa9` 均在计划批准前失败。原始 `remaining_workers_blocked_with_unsettled_model_attempts`、`quest_status_running` 标记不因最终零未知而撤销；较早中途快照与精确结算时间未持久化，不能由最终账本反推，见[时序补充记录](audit/phase2/live-usage-v21-timing.md)。

V22 的 219 文件历史归档与原 manifest 精确匹配，四项静态检查、原始证据保留性和只读用量校验通过，但全套测试及 strict 任务未通过，见[独立失败边界](audit/phase2/acceptance-independent-v22-boundary.json)、[三项测试失败明细](audit/phase2/acceptance-independent-v22-test-failures.md)和[用量审计](audit/phase2/live-usage-v22-audit.md)。L1 `20261115T104536Z-L1-79aa8a`、L2 `20261115T104536Z-L2-776888`、L3 `20261115T104536Z-L3-774dfc` 分别使用 37,977 / 21,759 / 23,858 Token、10 / 5 / 4 次已结算调用。L1 没有独立审阅、PASS 或新交付拒绝事件；最终结算不能撤销原失败标记，见[时序补充](audit/phase2/live-usage-v22-timing.md)。V23 聚焦恢复原安全诊断字段并继续根据真实来源/工具失败修复；原预算、65,536-byte 完整审阅上限及批准要求继续生效，尚无 V23 验收结论。

最终证据入口为 [Phase 2 状态](PHASE2_STATUS.md)、[报告](PHASE2_AGENT_INTELLIGENCE_REPORT.md)、[实验](PHASE2_EXPERIMENTS.md)、[质量门槛](QUALITY_GATES.md)及 `docs/audit/phase2/verification.json`。历史 V17–V22 结果不能替代 V23。V23 必须重新绑定实际冻结哈希、全套计数、strict run ID 和未通过项后才能判断验收。先前主文档原字节保存在 [V19 前文档快照](audit/phase2/checkpoint-pre-v19-docs/index.json)、[V22 前四份文档快照](audit/phase2/checkpoint-pre-v22-owned-docs/index.json)及[本次更新前快照](audit/phase2/checkpoint-pre-v23-owned-docs/index.json)。Phase 1 Milestone 1–7 报告及全部失败实验继续保留。
