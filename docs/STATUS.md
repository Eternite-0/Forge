# FORGE 项目状态

2026-11-16 迁移交付：用户要求尽快换环境继续，本环境停止新增真实模型验收。最新已关闭条件 `20261116T055126Z-capability-continuity-f87e7e` 的 **L2、L3 实际任务均通过**：L2 多专家研究及严格引用检查通过，L3 经真实失败测试、代码修订、成功测试和 7 项独立留出检查后由 Reviewer PASS；原 Router 收到审阅后的完成。整体仍失败，未通过的检查为 `complete_thread_consumption`、`post_restart_complete_thread_consumption`、`typed_multihop_recall`。预览送达不能证明完整 Thread 原文已读，宿主列举关系不能替代 Worker 实际 Recall。该条件 **33/33 调用 settled、376561 tokens**；当前完成阶段累计 **99 attempts 全部 settled、1089785 charged-or-reserved tokens**，此前其他阶段账目和未知效果独立保留。

交付源码新增完整读取／实际 handback 门槛：最终交付须有当前 Worker 的完整分页读取及来源证明，或真实成功并具有持久理由的显式 handback；缺页时保留已计费草稿，以原预算和期限进行有界反馈，来源损坏时在受租约保护的事务中阻塞。此修复的离线针对性回归为 **101 passed / 1 existing skip，33.90s**，见 [原始测试 XML](audit/superagent/capability-thread-read-targeted.xml)。**当前修复尚未进行新的真实模型验收或全量回归**；旧全套 **2886 passed / 13 historical failures / 3 skipped** 仅属于此前 round3 冻结源码，不为当前生产改动背书。

迁移继续请先读 [新环境交接](SUPERAGENT_NEXT_ENV_HANDOFF.md)及 [验证索引](audit/superagent/final-verification.json)。原 Provider 配置、100M 显式上下文声明、单任务 32 calls／500000 tokens、输出和重试边界均保留；前端未由本轮修改，Git 未 commit／push。拟议 60 attempts／1000000 tokens 新条件的 helper 仅为未执行草稿，本环境不运行 prepare 或 live。

以下全部为保留的历史阶段结论，其“本轮”“当前”等表述仅指各自记录时间，不覆盖上述迁移交付状态。

2026-11-16，已在新会话接续“重构 FORGE 为 DeepSeek Super-agent”。本轮修复两个已证实缺陷：明确 Worker 文件工具以 FORGE storage 为相对路径基准，项目身份和私有 cwd 不增加权限；已结算的失败工具回执在修复次数或步数耗尽时仍先验证来源并持久化，未知操作继续禁止重放。保留当前分支、原 Provider 配置、100M 用户声明、预算与权限边界，未修改前端或提交/推送。

**真实自主 L3 首次完整通过本轮条件。** `20261116T041952Z-capability-development-d5b385` 实际编写代码，执行精确授权的 CPython 测试，经历真实失败后修订并通过；7 项独立隐藏测试通过，最终代码哈希与成功回执一致；独立 Reviewer 先 NEEDS_REVISION 后 PASS，最终审阅结果回传原 Router，Thread 直达也通过。24/24 调用 settled、232,275 Token，没有新增未知调用或工具结果。相关联合回归 **222 passed / 2 skipped**，静态检查通过；未重跑本轮源码的完整后端套件，原全量历史失败不改写。

**完整 Super-agent 验收仍未完成。** 多专家 L2，以及同一条件中的双项目并行、重启、压缩、完整 Recall 与最终交回仍待通过。续跑累计 155/160 attempts（153 settled、2 次未转发的历史故障注入 rejected），已知 1,510,691 Token；历史拒绝未知用量和 1 个未知工具结果保留。本轮一次性条件已结束，不再追加真实探针。当前入口：[机器验收索引](audit/superagent/final-verification.json)、[本轮验证](audit/superagent/capability-resumed-verification.json)。以下记录为此前阶段，原索引字节保存在 [接续前验收记录](audit/superagent/capability-pre-resumption-final-verification.json)。

最终修复源码（2026-11-16，345 文件冻结）已按用户声明使用 DeepSeek **100M** 模型窗口，完整请求可负担额度与累计任务预算分别限制。已去除无显式 envelope 任务的旧 65,536 字节审阅总包阈值，保留实际模型窗口、完整 native 请求预留、累计额度、费用、单产物及显式 envelope 限制。补齐新项目在 Router 推理前的有界元数据登记、压缩交接诊断和摘要输出约束、合法 Watchdog 告警、返工耗尽收敛、现存真实目录处理及严格字节验收。原 Provider 配置、前端、数据库体系、事务/CAS/Outbox/权限与未知操作防重放均保留。

**完整能力验收仍失败，目标未完成。** 新的最终联合运行 `20261116T002629Z-capability-continuity-e02ee2`：28/28 调用 settled；两个独立 Quest 正确关联 beta/alpha，真实并行与双方 Thread pass-through 已验证。L2 Research 通过大于旧阈值的真实 Reviewer 准入并两次返工，随后因原可负担请求额度停止，Architect 未执行；L3 错用项目相对路径、耗尽有限修复后停止，未产生代码或测试。未到达本次联合重启、完整 Recall 和最终完成交回。早一阶段 L1 和单 Worker L2 完整通过，不能替代最终多专家/L3验收。最终完整回归 **2795 passed / 14 failed / 3 skipped**；13 项为相同历史失败，新增一项是遗漏迁移的旧 65K 测试合同，正单独纠正和重验，原 XML 不变。准确结果统一索引到 [机器验收](audit/superagent/final-verification.json)。本轮未提交或推送 GitHub。

旧 65K 测试合同已单独纠正并通过 **61 项跟进回归**，仍验证完整产物/哈希/回执不裁剪、实际 64K 模型窗口和原累计预算拒绝不足请求；仅该测试文件变化，最终生产源码和 14 失败的原全量 XML 保持不变。剩余未解决测试为原 13 项历史失败，不将跟进结果改写为全量通过。

本次续跑三个闭合条件阶段共 **131 attempts**，其中 129 次真实上游调用 settled、2 次未转发上游的故障注入 rejected，已知 1,278,416 Token；两次拒绝用量未知并完整保留预留，另保留一个旧工具未知结果。未增加原 160 次续跑调用上限，未提高单任务预算、输出或重试。最初 160 次任务/诊断阶段与 12 次微评测另行保留，不能混算或抹去其历史未知调用。以下记录是历史阶段。

最新续跑（2026-11-16）：已按用户明确声明使用 DeepSeek **100,000,000 Token 模型窗口**，单次请求的可负担额度、累计执行预算分别约束；余额不会改写模型容量。API 的 1M 声明仅作为冲突来源保留，未修改原 Provider 配置。新的冻结阶段已结束：L1 本地项目分析、单 Worker L2 研究完整通过；双项目联合场景在 Router JSON 校验前失败；固定团队暴露返工耗尽后的 `review_pending` 悬挂；注入一次 HTTP503 验证了自动切模，但备用 Worker 的目录操作结果未知，未重放。完整能力验收仍未通过，继续修复具体缺口。

该阶段 50 次 attempts：49 次真实上游调用全部 settled、1 次未转发上游的故障注入 rejected；已知 515,841 Token，拒绝调用用量未知并保留全部预留，另有 1 个工具结果未知。原 160 次阶段独立闭账，不能混为本轮成功。冻结源码 338 文件全部匹配；全套回归 2652 passed / 14 failed / 3 skipped，其中新增失败已定位为旧测试将模型窗口误当累计余额的断言冲突，尚待纠正及重新验证。见 [阶段闭账](audit/superagent/capability-continuation-closure.json)。以下是保留的历史阶段记录。

2026-11-16 Capability Sprint 继续 `57a162d`，后端改进已实施，**完整能力验收尚未通过**。本轮未重做前端、未提交或推送 GitHub。L1 有真实 Worker、Reviewer 与严格引用检查通过；L3 已产生真实代码并通过 7 项独立测试，但最终报告回执绑定失败；L2 多专家研究未完成。联合场景验证了并行、Thread pass-through、原 Router/Worker 重启、真实历史压缩与有界多跳 Recall，但双项目关联和最终审阅完成通知失败。

已修复模型窗口/累计配额混用、Router 有界纠错、Worker 批次逐项审批、Python 结果回执呈现、专家审阅正文遗漏和自动项目重验调度；保留事务、CAS、Outbox、权限与未知副作用防重放。Provider `/models` 实际声明输入 1,000,000 Token、输出 128,000，与用户口头的 100M 不一致；默认按 Registry 声明，显式较小 operator cap 仍有效。未更改 Provider 配置。

任务与针对性诊断共 **160 次真实调用（159 settled、1 次历史 reserved/结果未知）**；另有 12 次模型微评测，全部 settled。任务/诊断已知用量 1,355,207 Token，未知调用用量及货币费用保持未知，未增加实验上限。最后两次只验证修复后的真实回执可见性，不能追认为 L3 重验。最终生产源码全量回归 **2587 passed、13 项历史 failed、3 skipped**，无新增失败；59 份改动 Python 文件 Ruff 通过。完整边界见 [Capability Sprint 报告](SUPERAGENT_CAPABILITY_SPRINT_REPORT.md)、[实验记录](SUPERAGENT_EXPERIMENTS.md)及 [机器可读验收](audit/superagent/final-verification.json)。以下 Phase 2 记录原样保留为历史，不是本轮最新结论。

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
