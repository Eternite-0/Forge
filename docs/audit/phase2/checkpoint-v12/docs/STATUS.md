# FORGE 项目状态

更新：2026-11-15（Asia/Shanghai）

## 当前状态

**Phase 2 Agent Intelligence 进行中；strict L1已有通过记录，L2/L3 及复杂连续性验收尚未完成。** 本轮重点为现有后端的 Router/Planner、动态团队、Native Worker、独立审查返工、有来源的上下文与记忆、Agent 协调和真实端到端实验。进度见 [Phase 2 状态](PHASE2_STATUS.md)、[完整报告](PHASE2_AGENT_INTELLIGENCE_REPORT.md)和[实验记录](PHASE2_EXPERIMENTS.md)；最小架构调整见 [ADR 009](adr/ADR-009-agent-intelligence-contracts-and-review.md)。

当前已记录 B/F 局部合并 **53 passed / 18.36 秒**、驱动 **5 passed / 0.57 秒**、C/I **42 passed / 14.61 秒**、D/E/G **128 passed / 46.34 秒**、Provider/目标来源 **42 passed / 4.56 秒**；这些测试有重叠，不能相加作为完整套件数量。离线 v2 为小规模固定合成实验，零次 Provider 调用；[v4 重跑](audit/phase2/benchmark-final-v4.json)为 6.3642 秒、0 Provider 调用，固定机制指标未变。本地 workbuddy-api/wb-in 的 `deepseek-v4.1-flash` 经 Gateway 的历史 proxy-default 探针为 **11/11 小协议案例 / 26.712 秒**；显式 `reasoning_effort=none` 探针为 **10/11 / 20.210 秒**，精确文本对话检查失败保留。参数不证明上游内部 reasoning 行为，上游身份仍是代理自报。协议通过不能代替复杂任务验收。

保留的 L1 `20261115T012115Z-L1-2a34ad` 在 **103.621 秒**通过 strict 验收，发生一次审查返工与一次 Worker 自修，但没有压缩或重启。同快照 L2 `20261115T012115Z-L2-8e64f5` 在 **96.416 秒**失败：中间只读研究被要求交付下游报告，后续审查受 context limit 阻断，最终调用账本无未知项。L3 `20261115T012340Z-L3-17c603` 在 **447.913 秒**失败，有效七节点图后未组队或执行 Worker；缺少原始模型回复，不能断言缺失字段。

后续 L2 `20261115T013328Z-L2-fed968` 在 80.711 秒失败：16 次模型、6 次工具、54,597/9,558 输入/输出 token、未知 0，raw/native 为 68,294/68,550，超过 65,536；声明并发 3 也超过场景 cap 2。L3 `20261115T013328Z-L3-4e61d3` 在 151.834 秒失败，最终为合法 7 角色/并发 2；研究产物 15,776 字节，raw/native 67,009/68,224，Worker 已用 38,989/70,000、剩余 31,011 不足审查。两次均无 `review.started` 或重启。

v6 的三次新 live 也失败，最终未知调用均为 0：L1 `20261115T014735Z-L1-b48614` 122.096 秒，两轮 `NEEDS_REVISION` 加前序自修耗尽原 Worker `max_repairs`；L2 `20261115T014735Z-L2-cd59cb` 84.678 秒，14,060 字节只读 delivery 未受 v6 JSON-artifact 指导，raw/native 60,371/60,425，已用 18,143/78,000、剩 59,857，单审查输入已差 568；L3 `20261115T014735Z-L3-4ce51e` 165.77 秒，产物 11,651 字节、raw/native 65,637/66,278，已用 57,093/90,000、剩 32,907，27 次调用、123,506/22,569 输入/输出。L2/L3 无审查或重启；复杂连续性仍未完成。

[全量 v4](audit/phase2/frozen-tests-v4.xml)为 **781 passed、6 failed、1 warning / 290.98 秒**；六个旧 M4 Mock Planner fixture 使用不可执行 criteria。仅改为 `delivery_review` 后，[原断言回归](audit/phase2/planner-legacy-fixture-regressions.xml)为 **21 passed / 12.89 秒**，不代替最终全量。[v5 快照](audit/phase2/source-manifest-v5.json)含 153 文件，Ruff、150 个 Python 文件格式和 diff 检查通过；[v5 全量](audit/phase2/frozen-tests-v5.xml)及[日志](audit/phase2/frozen-tests-v5.log)为 **820 passed、1 warning / 219.96 秒**，manifest 哈希 `a1f4646cb9acc810f3d35e8f9609a99ed553efd1ce4c2b3a0906d98c0333ed93`。v5 早于后续 v6 改动，不覆盖新修复；[v6 全量](audit/phase2/frozen-tests-v6.xml)及[日志](audit/phase2/frozen-tests-v6.log)保留 **848 passed、1 failed、1 warning / 283.77 秒**。`test_m5_lifespan_execution` 的实际并行 DAG 测试失败：Core 将 Gateway 模型并发 1 与 Worker 并发 2 混同，误把 team operator cap 降为 1；v7 恢复 Worker 并发 2，独立于 Gateway 资源，不修改原测试。[v7 全量](audit/phase2/frozen-tests-v7.xml)及[日志](audit/phase2/frozen-tests-v7.log)为 **849 passed、1 warning / 216.87 秒**；[155 文件 manifest](audit/phase2/source-manifest-v7.json)哈希 `58339132008a97956237ec38c2eb19eb7062f46b4ef2f5f946567ad52be26627`，Ruff 与 152 个 Python 文件格式通过。该快照早于 v8，不能覆盖新严格交付修复。[v8 全量](audit/phase2/frozen-tests-v8.xml)及[日志](audit/phase2/frozen-tests-v8.log)为 **886 passed、1 warning / 283.46 秒**；[156 文件 manifest](audit/phase2/source-manifest-v8.json)为 `92bb6067ff7bc1dadfa6aa9b5994089969603b53b3980a9a5601d0a140e6b9e3`，Ruff/153 文件格式/pip 检查通过。v9全量已通过，v10结果见下，v11结果见下，v12冻结结果见下，机器汇总记录本轮实际部分状态。

v6 对新中间 JSON 分析/交接产物提供约 6 KiB 软指导，所有原要求、完整代码及最终报告仍须保留，不截断旧产物；Planner 指导在原总额内预留完整 context 审查/响应/修订累计预算。新团队/替换提案及审批校验 operator cap，v7 将 Worker cap 2 与 Gateway 模型调用并发分开；Composer/Worker 使用同一 Worker cap；harness 总 token/并发取 CLI 与场景较小值，并记录有效配置。[Composer 组](audit/phase2/composer-concurrency-20261115T094900.xml) **69 passed / 5.70 秒**、[Worker 指导组](audit/phase2/worker-report-guidance-tests.xml) **99 passed / 28.05 秒**均属本地/Mock，不代表 v6 全量或 live 通过。v7 把软指导扩展到有后继的 `delivery_review`，引用集中避免反复完整哈希，v7 曾建议中间角色 `output_tokens=2048`，v8 已取消该偏好以容纳完整 JSON，source-heavy 首节点在原总额允许时约两 context 累计预算并留至少 10% 协调余量；完整要求和全部硬限制保留。

v7 三次 live 全失败、最终未知均为 0：L1 `20261115T015415Z-L1-d9dedd` 98.792 秒、16 模型/4 工具、57,113/7,877 输入/输出，2048 输出截断耗尽 repair；L2 `20261115T015415Z-L2-b0e9f0` 93.078 秒、19/6、78,714/11,191，只规划两节点不满足 3–4 专业 Worker，14,141 字节产物又受 context limit 阻断；L3 `20261115T015415Z-L3-2732f0` 99.483 秒、24/9、127,932/11,268，真实审查 NEEDS_REVISION 后耗尽 max_steps=10，返工未完成。复杂连续性仍未证实。

v8 明确 Worker 当前节点范围，Planner 仅对整个任务均为简单只读时用单节点并保留明确专长/数量；Composer role ID 唯一且专长匹配，用户允许时建议 max_steps=16；native Core 将自修/审查返工共享 max_repairs 配为 4，原计数与步骤/token/工具/费用/时间/审查轮次仍约束。仅新 TeamWire 显式 null 选择 STRICT 八字段 schema，confidence 0–1，模型必须完整提供，Core 不填默认；历史/direct/显式旧 schema 保留。非有限 JSON 按原有界 repair 拒绝。[185 项合同组](audit/phase2/native-delivery-contract-20261115T151500.xml) 47.49 秒、[最新 36 项](audit/phase2/native-delivery-nonfinite-20261115T152200.xml) 5.97 秒均为有重叠的本地/Mock，v8 全量与 live 结果另录。

v8 三次 live 全失败：L1 `20261115T020604Z-L1-dceb58` 136.466 秒、23 模型/14 工具、已结算 128,890/14,443 输入/输出；L2 `20261115T020604Z-L2-7f6370` 99.597 秒、25/15、109,862/9,567。两次实际 NEEDS_REVISION 后反复 file_read 至原 tool budget 阻断，停机时各有 1 项未结算 Router notice，真实总用量/费用未知、保留 reservation。L1 有 2 个真实 Worker archive（17/6 来源），L2 有 4 个（9/6/6/4），但无 Core 中途重启/停机 Router 压缩。L3 `20261115T020604Z-L3-30acf0` 103.132 秒、18/6、84,067/12,506、未知 0；批准研究节点要求产物却仅允许读工具，真实文件不存在，未进入审查。无 L2/L3 后继执行，见[独立连续性诊断](audit/phase2/continuity-and-live-diagnosis-v8.md)。不能据此断言归档导致循环或前驱交接失败。

[v8 无损引用表示](audit/phase2/reviewer-lossless-v8-diagnostic.json)将确切历史 fixture 的 native input reserve 64,870 降至 64,346，省 524 字节，配 1,190 输出仍在原 65,536 内；原 packet/PASS/hash/raw cap 不变。[124 项](audit/phase2/reviewer-lossless-v8-tests.xml) / 60.43 秒为离线构造，不是 live 成功。[Benchmark v8](audit/phase2/benchmark-final-v8.json)为固定离线 17.598702 秒/0 Provider 调用。

v9 对生成 DAG 的具体产物 verifier 强制同节点已有 file_write，否则走原有界 schema 修复，不自动赋权；Worker 在有效单次 allowance ≥8192 时显示原 steps/tools/tokens 余量、归档 source_refs 与当前回执，超余量 batch 在执行任何工具前按原 repair 路径修复。未扩大工具/步骤/token/费用/时间上限；Publisher 仅新增 retry 数字拆分及 review reason/context 白名单。v9全量/两次live结果见下，v10结果见下，v11结果见下，v12结果见下，最终复杂连续性仍未完成。

[v9 全量](audit/phase2/frozen-tests-v9.xml)及[日志](audit/phase2/frozen-tests-v9.log)为 **895 passed、1 warning / 237.83秒**；[157文件manifest](audit/phase2/source-manifest-v9.json)哈希 `9a33b199404b777428ff0f0e1a1c1da356ad93b23b4395565fc0d211b3d9203a`，Ruff/154文件格式/pip通过。它早于v10，不能覆盖后续容量反馈或计数修改。[benchmark v9](audit/phase2/benchmark-final-v9.json)为5.9372256秒、0 Provider调用，仍是固定合成机制实验。

v9 L2 `20261115T021801Z-L2-b11011`在80.141秒失败：17模型/6工具，已结算65,607/9,348输入/输出、未知0；L3 `20261115T021801Z-L3-290b7f`在97.584秒失败：18/6、71,908/14,522、未知1个Router notice（9,985+4,096预留，真实总用量/费用未知）。实际产物15,235/10,788字节匹配回执；只读重建raw packet为68,207/68,967，均超过原65,536。两者均未发起审查、无后继/中途重启，首Worker仍review_pending。39份已结束driver记录发布于[snapshot-v2](audit/phase2/live-runs/snapshot-v2/index.json)，原run.json与旧snapshot保留。

v10未来计数使用实际self_repair事件与review revision分开记录，标记`disjoint_event_counts_v2`；旧`repairs`包含审查返工，历史retry总数可能重叠且保持原样。[27项局部回归](audit/phase2/retry-accounting-v10-fixed.xml)通过；v10已接入容量反馈与只读settlement等待，结果见下，v11结果见下，v12冻结结果见下，机器汇总记录本轮实际部分状态，容量诊断不算模型审查或PASS。
[v10全量](audit/phase2/frozen-tests-v10.xml)及[日志](audit/phase2/frozen-tests-v10.log)为
**915 passed、1 warning / 279.54秒**；[158文件manifest](audit/phase2/source-manifest-v10.json)
`814d0dcc6496f65d01ededad38a1e45a8d0f9848739467f7c36b985f3c318415`，
Ruff/149文件格式/pip/diff通过。它早于v11。[benchmark v10](audit/phase2/benchmark-final-v10.json)
7.5434305秒/0 Provider调用，仍是固定合成机制实验。[只读保留审计](audit/phase2/preservation-v10.json)
确认355份Phase1证据、9份旧报告字节不变，364个原测试身份保留；349个AST不变、15个修改，
具体断言替换已列明，不能称全部测试逐字不变或已证明语义等价。

v10三次同运行快照`f822b94b68dfa6e682f71fc1930d30054aa47bf41dfc27ef75a984e66f432c40`，
strict均失败、最终未知调用均0、费用仍未知：
L1 `20261115T061513Z-L1-c94230`59.801秒，11模型/5工具、32,806/3,291输入/输出；
错把plan ID当upstream receipt ID，context_source_rejected，未审查。
L2 `20261115T061513Z-L2-62b044`80.284秒，17/9、75,382/6,207；
实际research独立同模型审查PASS并succeeded，任务仍有未完成节点时在已结算边界暂停Core，
停机生成Router archive（2份来源、原文readback通过），同workspace/Router ID重启；
已完成节点、回执哈希、已结算调用和Worker累计预算计数保留，resume返回200。
architect重启前已执行1步，重启后继续到3步，随后读11,211字节前驱回执超过原8192上限而blocked。
这证明该有界连续性片段，整个复杂任务仍失败。
L3 `20261115T061513Z-L3-8fdef4`120.099秒，25/8、134,215/15,831；
真实NEEDS_REVISION后同Worker修订，新raw packet70,657/native reserve70,947，
Worker已用98,513/160,000、剩61,487不足受保护的容量修订加独立审查，
Core以`review_capacity_unrepairable`给出BLOCKED，未重启。
早期unsettled stop标签保留，停机前只读等待后最终账本unknown0，不能据早期标签写成最终未知。

Core容量诊断标为`deterministic_review_capacity/core_review_capacity`，不算模型审查/PASS，
保留完整来源、原packet/hash及65,536限制；只有当前显式file_revise/grant、原repair/步骤/
工具/token/费用/时间/轮次余量且无未结算调用，才允许同Worker返工并重新独立审查。
[容量回归](audit/phase2/reviewer-capacity-v10-fixed-tests.xml)143项通过（JUnit45.843秒），
[首次143项](audit/phase2/reviewer-capacity-v10-tests.xml)中的1失败保留。driver停机前最多30秒
只读等待结算，不重发/取消/擅自settle未知调用；[27项局部回归](audit/phase2/driver-settlement-v10.xml)
通过（JUnit4.263秒）。v10 disjoint retry为0/0/2，L3分别1次审查返工和1次notice retry，
self_repair/HTTP retry均0；历史重叠计数不追改。

42份driver记录已发布到[snapshot-v3](audit/phase2/live-runs/snapshot-v3/index.json)，
保留v2/v1与原run.json，发布不调用Provider或追认失败。v11正在修复local receipt读取拒绝后的
有界self_repair与upstream字节/可读性提示，[79项局部回归](audit/phase2/receipt-read-repair-v11-tests.xml)
通过；Planner operator预算上限仍在集成。v10 L3图节点预算总和920,000超过场景630,000，
但Gateway每次实际调用始终执行原硬上限。v11结果见下，v12冻结结果见下，机器汇总记录本轮实际部分状态，不能宣布Phase2完成。

[v11全量](audit/phase2/frozen-tests-v11.xml)及[日志](audit/phase2/frozen-tests-v11.log)为
**941 passed、1 warning / 199.39秒**；[160文件manifest](audit/phase2/source-manifest-v11.json)
`eec443f0aad4687e336a8b9ca3629218ebce4417b8096dabb8eaa44845dc7822`，
Ruff/157文件格式/pip/diff通过，早于v12。[benchmark v11](audit/phase2/benchmark-final-v11.json)
4.3837569秒/0 Provider调用，仍是固定合成机制结果。[保留审计v11](audit/phase2/preservation-v11.json)
仍为355份旧证据、364个原测试身份，349个AST不变/15个修改，历史原记录不追改。

v11已接入local receipt拒绝后的原计数内self_repair：仅本地拒绝且有Core无副作用证明，
失败读仍计数，新call仍须通过原前驱/scope/回执限制；upstream引用提供字节数/可读性提示，
没有自动扩权。[79项回归](audit/phase2/receipt-read-repair-v11-tests.xml)通过（JUnit19.062秒），
[35项兼容](audit/phase2/receipt-repair-legacy-v11.xml)通过（JUnit9.704秒）。
生成Planner图在持久化前执行可信operator总token上限，超额走原一次repair，再超额不保存图；
默认/手工/历史图不改。[16项预算](audit/phase2/planner-operator-budget-v11.xml)和
[51项兼容](audit/phase2/planner-operator-compat-v11.xml)通过（JUnit1.203/17.397秒），
均为有重叠的本地/Mock证据。

同runtime快照`dc73aec124a2fc9637e568940a4adfa29b407092d6bcad31ffb11c37839ac520`：
L1 `20261115T062445Z-L1-e6498c`在111.975秒通过strict，19模型/5工具，
102,134/12,157输入/输出、未知0；真实独立同模型NEEDS_REVISION两次，各同Worker修订后
最终PASS/Quest completed。disjoint计数为review2/self0/HTTP0/notice0；无archive/Core重启。
早期L1通过记录继续保留，不能把两次通过当作通用复杂任务能力。

L2 `20261115T062445Z-L2-b7e197`177.094秒strict失败，37模型/16工具，
199,677/25,402输入/输出、最终未知0。research与analysis均真实返工后PASS；
l2-research.json、l2-report.json存在并匹配最新回执。任务未结束时暂停至已结算边界后
Core停机压缩Router的1份来源、readback通过，同Router/workspace重启；完成节点、回执、
已结算调用和预算计数保留，resume200。analysis重启前已1步，随后继续并通过，最终只读review
Worker真实启动，2步/6次工具读取后context_budget；首次整尾archive因本次请求未预置
readback工具而不能建立。disjoint为review2/notice1/self0/HTTP0；早期unsettled标签保留，
最终只读等待后账本unknown0。中间PASS及报告存在不等于最终复杂任务验收。

L3 `20261115T062445Z-L3-ba6bb7`95.121秒失败，17模型/6工具，
73,147/13,283输入/输出、未知0；Core容量BLOCKED，未发独立模型审查或重启。
raw/native为66,309/66,394，Worker已用39,752/110,000、余70,248；
旧guard将串行reservation错误相加为76,891，而单次floor最大值49,827，过度保守地拒绝返工。
这不是实际Gateway累计预算不足；raw包仍须经真正受治理修订、完整新交付及独立审查才可通过。
disjoint为notice1/review0/self0/HTTP0。

45份driver记录发布于[snapshot-v4](audit/phase2/live-runs/snapshot-v4/index.json)，
保留v3/v2/v1及原run.json。v12在纠正串行预检；[145项局部回归](audit/phase2/reviewer-serial-capacity-v12-tests.xml)
通过（JUnit74.570秒），实际Gateway配offline MockTransport，不是live调用。
首次整尾archive需本次请求已含readback工具及其精确reservation；
[87项旧回归](audit/phase2/first-archive-legacy-v12.xml)通过（JUnit65.280秒），新路径测试和
v12完整结果如下。原权限/预算/context上限不扩展，Phase2仍进行中。

本轮后端[v12冻结全量](audit/phase2/frozen-tests-v12.xml)及[日志](audit/phase2/frozen-tests-v12.log)
**955 passed、1 warning / 251.43秒**；[162文件manifest](audit/phase2/source-manifest-v12.json)
`ccc5efe69b4493dd77093b4a62f6f6851f09c63ca1f2806298f2e59e80d7f8d7`，
Ruff/159文件格式/pip/diff通过。[benchmark v12](audit/phase2/benchmark-final-v12.json)
4.6835418秒/0 Provider调用，仍为固定合成机制结果。[保留审计v12](audit/phase2/preservation-v12.json)
确认355份旧证据、364个原测试身份，349个AST不变/15个修改，历史未重写。

v12串行容量预检改为比较单次reservation floor最大值，后续每次完整请求仍经Gateway
准入并结算真实用量；没有提高预算。首次整尾archive在steps>0且有效单次allowance≥8192时
预置本次source readback工具，完整工具字节计入原reservation，不能丢失原文恢复能力。
[12项新路径修复回归](audit/phase2/first-archive-v12-fixed-tests.xml)通过（JUnit1.848秒）；
[首次12项](audit/phase2/first-archive-v12-tests.xml)中1失败保留。与旧87项/serial145项有重叠，
均为本地/Mock；本次三run并未触发首次整尾fallback/readback，不能称该新路径live成功。

v12同runtime快照`de228407cdb822110db71de4c2ffda7a0a2c351ea02284b4a7e60fcd17d369d4`，
三run最终未知均0、费用仍未知。
L1 `20261115T063556Z-L1-1e03cf`102.934秒strict通过，16模型/4工具，
66,279/8,829输入/输出；真实NEEDS_REVISION→同Worker修订→PASS/完成。
disjoint total1/review1/self0/HTTP0/notice0，无archive/Core重启。

L2 `20261115T063556Z-L2-a3ad74`109.832秒失败，23模型/9工具、
104,306/10,744输入/输出。source-analysis实际delivery-review PASS并succeeded，
中途在已结算边界暂停Core并停机压缩Router的1份来源/readback通过；同workspace/Router身份、
完成节点、回执、已结算调用和预算计数保留，resume200。draft-report暂停前已step1、
token18,234/tool0，重启前后保持，继续至step5/tool5并实际写出l2-draft-report.json。
Worker已用52,939/90,000、余37,061；只读重建完整raw packet54,421字节，
含receipts23,997/artifacts12,699/delivery4,038/constraints7,998及原合同/来源。
原完整审查预算不足，review.blocked保留`review_contract_or_budget_failed`，
未对draft发新模型审查，strict仍失败；disjoint retry0。

L3 `20261115T063556Z-L3-14d6f5`115.371秒失败，21模型/8工具、
105,632/15,238输入/输出。实际NEEDS_REVISION→file_revise，模型扩大修订产物/delivery；
新raw68,149/native68,412。已用79,762/120,000、余40,238，
native fixed floor48,956+revision receipt1,125+verdict1,024=51,105仍大于余量，
修正后的serial guard正确给出Core容量BLOCKED。工具为directory_list2/file_read4/
file_write1/file_revise1，没有own-file reread。普通Worker archive存在，未触发first-tail/readback，
无L3 Core重启；disjoint total1/review1/self0/HTTP0/notice0。

[只读Reviewer断言诊断](audit/phase2/reviewer-claim-diagnostic-v12.json)还确认该L3返工目标有错：
ast.parse(mode=exec)接受顶层Return节点，仓库实际有界解释器执行`return 0`返回0、2步。
Python字节码编译规则不能替代numeric AST合同。0 Provider调用、原失败不变。
新鲜上下文同模型审查仍可能给出错误返工要求；适用产物还须经独立固定numeric语义gate，
模型verdict不保证领域正确性。

[snapshot-v5](audit/phase2/live-runs/snapshot-v5/index.json)已发布48份driver记录，保留v4/v3/v2/v1与
原失败。本轮后端源码保持v12冻结；同源码两次追加有界L2/L3采样已完成，见下，全部失败继续保留。
`docs/audit/phase2/verification.json`汇总本轮实际状态及缺口，
不是Phase2完成证书；L2/L3复杂验收和既有能力缺口继续保留。

同一v12源码的最后两次有界采样独立保留，未修改源码或替换失败：
L2 `20261115T064204Z-L2-3f89c4`115.488秒strict失败，29模型/9工具，
154,973/15,583输入/输出、未知0。research两次真实NEEDS_REVISION并返工，
已用122,642/150,000、余27,358；第三包raw67,630/native67,645，
native fixed58,051+receipt1,124+verdict1,024=60,199超过余量，Core容量BLOCKED，
无Core重启。disjoint total2/review2/self0/HTTP0/notice0。

L3 `20261115T064204Z-L3-1288e8`111.105秒strict失败，21模型/9工具，
104,171/14,090输入/输出，未知0、retry0。research实际独立PASS/succeeded，
5步/5工具、49,821/120,000；首次L3在任务未结束时完成已结算中途Core重启：
Router压缩1份来源/readback、原identity/workspace/完成节点/回执/已结算调用/预算计数保留，
resume200。数学节点暂停前step1/token18,462/tool0，重启前后保持，继续至step5/tool4，
49,962/90,000并真实写出l3-model.json。余40,038；只读重建默认_packet成功，raw40,350字节
（receipts10,983/artifacts10,821/delivery5,148/constraints7,552），native输入39,871+
最低verdict1,024=40,895超过余量，_review_reservation明确拒绝Independent review budget exhausted。
历史reason仍为`review_contract_or_budget_failed`，未发数学模型审查；不是JSON解析失败，
真实重启/后继继续片段不等于整个复杂Quest完成。

[snapshot-v6](audit/phase2/live-runs/snapshot-v6/index.json)最终发布50份driver记录，
保留v5/v4/v3/v2/v1和全部原失败。历史集合至少记录9项未结算调用，另6份早期run缺失用量统计，
因此aggregate unknown总数及真实总usage/cost仍未知；input/output总和仅覆盖已报告部分。
最新v10/v11/v12每run未知0不结算旧unknown，缺失值不当0。

后续建议为Core在大产物生成/修订前预览完整证据packet和native审查reservation，
并引入显式的新artifact/revision预算合同，在原总额内为完整审查和有界返工留量。
这些只是建议，尚未实现；不得减少完整来源/回执要求、追授权限或靠提高预算宣称通过。
本轮后端源码与九份文档冻结于v12，Phase2核心复杂任务目标仍未达成。
`docs/audit/phase2/verification.json`汇总本轮实际部分状态和未过门槛，不是Phase2完成证书。





后续[自产物修订与 Reviewer 修复](audit/phase2/sprint-deg.md)局部组为 **153 passed / 40.63 秒**，租约恢复组 **90 passed / 32.30 秒**，返工 verdict 提交后重排队恢复组 **28 passed / 13.95 秒**。这些仍是有重叠的本地/Mock 工程验证，不构成新的 live 成功结论。

[固定数值套件失败到同 Worker 修订重验](audit/phase2/sprint-ci.md)扩大组为 **119 passed / 44.14 秒**，含首轮 21 个新增案例；后续[语义反馈恢复组](audit/phase2/semantic-recovery-20261115T090801.xml)为 **74 passed / 34.65 秒**，语义测试文件现有 30 个案例。本地真实数值子进程与脚本模型不算真实 Provider 验收。反馈需有来源、显式修订权限、有效 grant、原预算余量且无未结算动作/调用；修订后仍须真实套件通过。反馈提交后 callback 前崩溃可在重验当前结果、真实语义来源、权限/grant 和原预算后幂等恢复，无需发模型审查；旧结果、变化来源、撤权、预算耗尽、未知动作和回调异常仍阻断。

[审查输入无损表示组](audit/phase2/review-context-20261115T091955.xml)为 **118 passed / 36.18 秒**：完整 source、精确字符范围和共享元数据保留原 packet/PASS 哈希；确切 L2 fixture 的本地 native Gateway reserve 为 64,245，仍受 65,536 上限约束。未发网络请求、未追认旧运行，更大输入超界仍拒绝。新发布的运行配置只含白名单字段，旧记录缺失值保持 null。

后续 [Composer 显式 null 选择 Core schema](audit/phase2/composer-contract-20261115T093043.xml)为 **45 passed / 5.89 秒**；动态合同仍必须完整，角色与权限未扩展。[审查节点范围与输出容量组](audit/phase2/review-scope-cap-tests.xml)为 **123 passed / 31.80 秒**：只审查当前节点目标、criteria 与批准角色，最终交付节点仍承担全部原始要求；输出同时受原输出、context 和累计 token 余量约束，最低自适应容量为 `min(1024, original output cap)`，不足或原费用权限不足仍拒绝。原 packet/PASS 哈希和完整来源保持不变，本地构造不算 live 成功。

用户已暂缓 M7 桌面验收；单一持久聊天的桌面联调、Core 生命周期、设置、审批范围、凭据、备份恢复及安装包仍须以最终实际结果为准。历史[后端加固](PHASE1_MILESTONE7_BACKEND_HARDENING.md)冻结快照为 **488 passed，1 warning，107.62 秒**，双工作目录 Ruff、格式及依赖检查通过，见[验证记录](audit/phase1/milestone7-backend-hardening-verification.json)。它不是当前 Phase 2 的完整套件或桌面发布验收。总体桌面待验项见 [M7 报告草稿](PHASE1_MILESTONE7_REPORT.md)。

Milestone 5 的 Native Worker、动态团队、工具治理与独立证据链已完成本机冻结验收：**282 passed，1 warning，44.99 秒**；真实供应商完成有限的 numeric AST 和 JSON 产物演示，范围见 [M5 报告](PHASE1_MILESTONE5_REPORT.md)。Milestone 6 的最终快照本机完整测试为 **372 passed，1 warning，77.78 秒**，静态、依赖与历史证据核验通过；一次有界真实供应商任务恢复成功，前五次诊断失败均保留。有限合成研究及容量边界见 [M6 报告](PHASE1_MILESTONE6_REPORT.md)。这些结果不能证明无限对话或通用任务正确性。

Router、Planner、Composer、benchmark、Worker 与独立 Reviewer 的模型请求沿用统一 Gateway 准入。价格未知时使用 token-only，不推算费用。当前默认 Review 审批保持启用；通用剩余 DAG 自主重规划、完整类型 payload 合同、学习式语义召回和独立 Reviewer 模型选择仍未完成。当前限制和质量门槛见 [QUALITY_GATES](QUALITY_GATES.md)，配置及恢复复核步骤见 [README](../README.md)。

当前 Worker 使用固定工具、语法检查和有界数值 AST，尚无任意代码/测试命令执行或通用代码补丁工具。`file_write` 仅新建；显式批准的 `file_revise` 可凭最新回执链和未变化哈希，原子修订当前 Worker 自己产出的文件并保留不可变版本，不能修改用户文件或前驱产物。Reviewer 的 Core JSON 解析反馈明确标记来源，不冒充模型审查；已结算截断重试消耗原预算与轮次。租约仅在已结算步骤间续期，仍受当前权限、代次围栏和任务截止约束。运行中团队替换保持同一 DAG；这不等于自主重规划。检索为有来源的词法召回，以上缺口不因局部测试通过而视为完成。

## 历史阶段与证据

各阶段报告和审计记录保留当时的测试结果、限制和待办，其历史描述不代表当前状态：

- [Phase 0 审计](PHASE0_AUDIT.md)及[验证证据](audit/phase0/verification.json)。
- [Milestone 1](PHASE1_MILESTONE1_REPORT.md)、[Milestone 2](PHASE1_MILESTONE2_REPORT.md)、[Milestone 3](PHASE1_MILESTONE3_REPORT.md) 和 [Milestone 4](PHASE1_MILESTONE4_REPORT.md) 报告。
- [Milestone 5](PHASE1_MILESTONE5_REPORT.md) 和 [Milestone 6](PHASE1_MILESTONE6_REPORT.md) 报告及各自审计证据。

历史审计脚本可能覆盖历史证据，重跑前应使用独立输出位置。阶段路线及尚未验证的验收目标见 [ROADMAP](ROADMAP.md)。
