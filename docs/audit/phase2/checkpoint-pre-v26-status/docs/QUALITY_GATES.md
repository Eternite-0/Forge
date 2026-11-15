# FORGE 质量门槛

最新验收（2026-11-15）：V24 全套 1,734 项通过、1 个已有 warning / 465.71 秒；静态与原证据保留性检查通过。strict L1/L2/L3 均在 Planner 阶段失败，15 次真实调用全部结算 87,044 Token，无 Worker 或中途重启。三个补充探针也未进入 Worker。当前 V25 修复让模型明确选择容量档位，原预算、任务标准和审批不变。Phase 2 尚未完成，以下此前记录保留；最新证据见 [V24 历史边界](audit/phase2/acceptance-independent-v24-boundary.md)。

更新：2026-11-15。V23 修复中，最终冻结与验收尚未验证。V22 已保存 1,592 项通过、3 项失败、1 个 warning / 352.37 秒及三层 strict 失败；19 次调用最终结算 83,594 Token，最终未知 0。三项旧 confidence 反馈测试因缺少原 `path` 字段失败，原断言保留；L1 以 context_budget blocked，L2/L3 失败于 Planner，原时序失败标记保留。V21 已保存 1,560 项后端通过、1 个 warning / 445.09 秒及三层 strict 失败：18 次调用最终结算 83,527 Token，最终未知 0；L1 真实 Worker 三次只读工具后 blocked，L2/L3 失败于 Planner，原中途未结算失败标记保留。V20 已保存 1,490 项后端通过、1 个 warning / 312.74 秒及三层 strict 失败：22 次调用全部结算 108,458 Token，最终未知 0，均无 Worker。V19 已保存 1,448 项后端通过、1 个 warning / 321.62 秒及三层 strict 失败：12 次调用全部结算 58,229 Token，未知 0，三层均无 Worker、审阅或重启。V18 已保存 1,413 项后端通过、1 个 warning / 408.37 秒及三层 strict 失败：37 次调用全部结算 270,119 Token，未知 0，三层均无 Worker、审阅或重启。V17 已保存 1,405 项后端通过、三层 strict 失败的历史结果。Phase 2 未完成。本文件描述应满足的标准，不能用待运行项目替代结果。

1. 所有后端源代码、实验驱动和测试在同一 manifest 下冻结，跑完整后端套件以及 Ruff、格式、依赖和 diff 检查。局部集合有重叠，不能累加。源码变化后旧结果只证明旧快照。
2. Phase 1 报告、旧测试身份、原始失败数据库、日志与实验结果完整保留。兼容 fixture 修改必须披露；不得删除断言、伪造运行数据、隐藏失败或覆盖原 run。
3. 实际模型实验走原 Gateway、有限持久预算、原能力/输出/context/重试/并发边界。逐 HTTP attempt 计入 settled 用量或完整未知预留；费用未知保持 null，不能当作零。
4. 区分实际模型、本地脚本 HTTP、Mock、合成实验。Gateway 连接本地脚本服务时自然产生的 `execution_mode=live` 只证明原生传输和账本路径，不能成为真实模型质量或 strict 任务验收证据。
5. 当前 strict L1/L2/L3 必须由只读验收器根据原数据库和实际文件重新验证。成功的 HTTP、合法提案、通过的测试、模型自报或过往版本成功均不能替代当前任务成功。
6. L1 保持单 Worker、真实只读文件读取/目录回执、完整八字段交付与当前独立 PASS；L2/L3 保持专业角色、完整最终产物、真实哈希/回执/固定检查和当前内容审查。复杂任务还须实际中途重启、身份/计数/来源保留以及成功恢复完成。
7. 原始目标和更新目标、来源约束、批准图/团队及当前结果必须绑定。来源撤销、修改、过期、跨任务错配、过时 PASS、权限扩展或未知效果均不能继续授权。
8. `worker_read_context_source`、`worker_read_action_receipt`、`worker_read_completed_delivery` 按 Core 原来源及绑定的 Worker exchange 重验。其结果不伪造成 action receipt，不满足独立的源文件读取、文件创建或最终产物要求。前驱交付必须完整、保留原字节上限并重验完整成功证据；其内容仍为模型声明。
9. 新容量合同测量实际来源、原生 Gateway 序列化、输出以及有界审阅/纠错。`serial-capacity-v1` 必须由用户明确批准，限定成功单链/并发一/Token-only；基础额度及一次共享池之和在原任务 cap 内。未来节点基础额度受到保护。所有未结算、拒绝、超额及未知 attempt 按原账本保守处理，不因启动、重启或重新组队退款。
10. Worker 修复、审阅返工、Agent 派生、步骤、工具和时间均有界。租约更替、暂停/撤销或过时响应不能恢复旧执行权；准确结算历史已知 usage 只修账，不授予响应应用或重放权。
11. Benchmark 固定任务、预算、基线、评分规则和全部原始结果。必须同时报告任务成功、Token/调用、耗时、返工和证据完整性。模型阶段完成率与任务成功率分开；单模型、小样本、不同调用次数和反馈差异必须披露。

能力边界：语义记忆是有限候选页的模型选择，保留原身份、哈希和信任，无法证明大规模/跨领域检索质量。反馈重规划只生成新提案，不能自动批准。受限层次展开不等于无限 Agent。图像/音频/视频执行、任意 shell 与通用仓库编辑/测试执行未实现。同模型新上下文审阅不具备模型多样性，证据门槛也不证明任意领域正确性。

V23 最终全量、strict run ID、manifest 哈希及独立保留性审计尚未完成，只能根据实际产物更新结果。恢复原安全诊断字段及后续来源/工具修复不得修改旧断言、扩大 verifier 范围、遗漏来源或改变原预算、65,536-byte 完整审阅上限及审批要求。

V22 的 219 文件历史归档、静态检查、保留性和用量绑定通过，但全套三项失败、三层 strict 均失败；独立边界的 `tests.passed` 与 `evidence_consistency_passed` 均为 false，见[原始失败边界](audit/phase2/acceptance-independent-v22-boundary.json)、[三项测试失败明细](audit/phase2/acceptance-independent-v22-test-failures.md)和[用量审计](audit/phase2/live-usage-v22-audit.md)。L1 `20261115T104536Z-L1-79aa8a` 的三步/三工具/两修复没有产生独立审阅或 PASS，新交付拒绝事件为零；L2 `20261115T104536Z-L2-776888`、L3 `20261115T104536Z-L3-774dfc` 失败于 Planner。L1 原中途未结算/running 标记不因最终十次 settled 而撤销，见[时序补充](audit/phase2/live-usage-v22-timing.md)。

V21 的 216 文件历史归档精确匹配原 manifest，三层原失败见[独立失败边界](audit/phase2/acceptance-independent-v21-boundary.md)和[用量审计](audit/phase2/live-usage-v21-audit.md)。L1 `20261115T102752Z-L1-6a4082` 已有计划/团队和真实工具调用，但 Worker blocked、没有独立审阅或 PASS；L2 `20261115T102753Z-L2-ba61b0`、L3 `20261115T102753Z-L3-913aa9` 失败于 Planner。原始 `remaining_workers_blocked_with_unsettled_model_attempts` 与 `quest_status_running` 不因最终零未知而撤销。较早中途快照和精确结算时间未保存，最终账本不能回推该时刻，见[时序补充](audit/phase2/live-usage-v21-timing.md)。V20 的 210 文件历史归档也精确匹配，L1 失败于 Composer、L2/L3 失败于 Planner，见[独立失败边界](audit/phase2/acceptance-independent-v20-boundary.md)和[用量审计](audit/phase2/live-usage-v20-audit.md)。

V19 原始 `20261115T095809Z-L1-92e62d`、`20261115T095809Z-L2-896400`、`20261115T095809Z-L3-545d98` 全部在计划批准前失败，207 文件归档与原 manifest 精确一致，见[独立失败边界](audit/phase2/acceptance-independent-v19-boundary.md)和[用量审计](audit/phase2/live-usage-v19-audit.md)。V18 原始 `20261115T093857Z-L1-598df5`、`20261115T093857Z-L2-bd6e94`、`20261115T093857Z-L3-0b7788` 全部在计划批准前失败，见[独立边界](audit/phase2/acceptance-independent-v18-boundary.md)及[用量审计](audit/phase2/live-usage-v18-audit.md)。V17 的 201 文件 manifest、完整套件及 `20261115T092655Z-L1-c3a404`、`20261115T092655Z-L2-350db9`、`20261115T092655Z-L3-bf61b1` 原失败见 `docs/audit/phase2/acceptance-v17.md`。历史结果继续保留；未观察或失败检查不能在最终文档中改成通过。更新前原字节分别保存在[V22 前四份文档快照](audit/phase2/checkpoint-pre-v22-owned-docs/index.json)和[本次更新前快照](audit/phase2/checkpoint-pre-v23-owned-docs/index.json)。
