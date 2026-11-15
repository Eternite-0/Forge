# Phase 2 状态

Current acceptance — v15, 2026-11-15: **1,031 backend tests passed**, one
existing TestClient warning, 227.13 seconds. Ruff, formatting of 164 Python files,
dependency and whitespace checks passed on the unchanged 167-file frozen source.
The three current-source strict live runs **all failed**: L1 requested help and
its approved concurrency exceeded the scenario cap; L2 lacked cumulative review budget;
L3 revised after real NEEDS_REVISION, then exhausted review capacity. Neither
complex run reached its mid-execution restart. Phase 2 remains incomplete.
See [this round's evidence](audit/phase2/acceptance-v15.md) and
[machine-readable verification](audit/phase2/verification-v15.json).
The following earlier-round results are historical; the original v12 verification
and documents remain byte-identical under `audit/phase2/checkpoint-v12/`.


更新：2026-11-15（Asia/Shanghai）。分支：`codex/phase1-continuous`。
集成负责人：root agent。**阶段进行中；v15后端冻结检查通过，本轮L1/L2/L3 strict均失败。历史L1通过记录不能认证当前源码。**

当前目标是现有后端的 Agent Intelligence；M7 桌面 Alpha、安装包及原生联调仍暂缓。
产品继续只有一个长期用户对话，由 Persistent Router 协调私有 Worker。

| Sprint | 当前状态 | 证据与未完成项 |
| --- | --- | --- |
| A：审计与 Provider | 执行链审计、两次真实协议探测已记录 | proxy default 为 11/11；显式 `reasoning_effort=none` 为 10/11，精确文本对话检查失败保留；参数不证明上游内部 reasoning；[执行链审计](audit/phase2/execution-chain-audit.md) |
| B：Router/Planner | 分离规划、原文/约束输入、有界 schema repair 已实现 | B/F 合并 53 passed；推理与编排回归为 Mock，真实复杂规划以 L1–L3 为准 |
| C：动态团队 | 已有策略复用，增加有来源的线上表现输入及剩余节点替换 | C/I 42 passed；能力选择为有限合成实验；[C/I 报告](audit/phase2/sprint-ci.md) |
| D：Native Worker | 自有循环、checkpoint、步骤间租约续期、有界修正及显式批准的自产物修订已接线 | 修订/审查/工具组 153 passed，租约组 90 passed；真实本地工具配合 Mock 模型，不代表通用自主修复；[报告](audit/phase2/sprint-deg.md) |
| E：独立审查 | 新鲜上下文、无损完整来源表示、四种 verdict、Core JSON/固定数值套件反馈、已结算截断重试和明确返工目标已接线 | 修订/审查组 153 passed、返工重排队恢复组 28 passed、语义返工扩大组 119 passed；当前与 Worker 使用同一批准模型；复杂 live 返工待验收 |
| F：Context/Memory | 来源约束、有界压缩与原文读回、词项及别名召回已实现 | 53 项 B/F 回归、9/9 固定探索性召回案例；非 embedding；[Sprint F](audit/phase2/sprint-f.md) |
| G：ACP/恢复 | 复用持久事件总线、类型投影、任务身份和租约围栏 | D/E/G 128 项包含 Dispatcher 回归；完整 payload 合同、任意在途副作用恢复仍不完整 |
| H：真实端到端 | v15 L1/L2/L3 strict均失败 | L1请求帮助且批准并发超场景上限；L2累计审查预算不足；L3真实返工后审查容量不足。L2/L3各有一次Worker archive，但没有原文读回、中途重启或Router压缩连续性；历史恢复记录另存 |
| I：算法基准 | 三类固定离线机制基准及消融已保留 | 尚缺相同预算的真实模型任务质量/Reviewer 对照；[实验](PHASE2_EXPERIMENTS.md) |

局部测试不可相加作为完整套件数量。当前已记录：B/F 53 passed / 18.36 秒，
driver 5 passed / 0.57 秒，C/I 42 passed / 14.61 秒，D/E/G 128 passed / 46.34 秒。
后续自产物修订/Reviewer 修复组为 153 passed / 40.63 秒，租约组 90 passed /
32.30 秒，返工 verdict 提交后重排队恢复组 28 passed / 13.95 秒，见
[D/E/G 记录](audit/phase2/sprint-deg.md)。这些仍是有重叠的本地/Mock 测试。
真实本地固定数值套件失败到同 Worker 返工的扩大组为 119 passed / 44.14 秒，
含 21 个新增案例，见 [C/I 记录](audit/phase2/sprint-ci.md)；脚本模型无 Provider 调用。
后续[语义反馈崩溃恢复组](audit/phase2/semantic-recovery-20261115T090801.xml)为
74 passed / 34.65 秒；对应语义测试文件现有 30 个案例，仍属本地/Mock 证据。
后续[审查来源包/轮次配置组](audit/phase2/review-context-20261115T091955.xml)为
118 passed / 36.18 秒，含 15 个新增案例。确切 L2 fixture 的完整三份来源与原
packet/PASS 哈希不变，本地 native Gateway reserve 为 64,245，未提高 65,536
context 上限；这不构成 live 审查成功，更大包超界仍拒绝。
另有 Provider/原始与更新目标来源测试 42 passed / 4.56 秒，见[记录](audit/phase2/provider-source-regressions.md)。本轮v12冻结快照的完整测试、
静态检查及L1/L2/L3指标汇总于`docs/audit/phase2/verification.json`，记录整体incomplete状态。

离线 v2 使用零次 Provider 调用：48 个团队提案均满足硬约束；压缩加来源读回保留
12/12 约束；治理 11 个应拒绝、4 个应允许均符合预期；固定召回 9/9。其小样本、
合成画像、字节预算和 Mock fixture 限制见[完整报告](PHASE2_AGENT_INTELLIGENCE_REPORT.md)。

已保留的 L1 `20261115T012115Z-L1-2a34ad` 在 103.621 秒通过 strict 验收：19 次模型、
6 次工具，报告输入/输出 87,788/10,429 token，未知调用 0；NEEDS_REVISION → PASS，
审查返工和自修各 1 次，但没有 archive 或重启。同快照
`c5e1674f38345a24a26aaf37473ebad97ed67f41fe445ef6f14d7a3c375c9134` 的 L2
`20261115T012115Z-L2-8e64f5` 在 96.416 秒失败：19 次模型、6 次工具，92,916/10,436
输入/输出，未知 0；Reviewer 错向中间只读研究索要下游产物，返工后 context limit，未重启。
L3 `20261115T012340Z-L3-17c603` 在 447.913 秒失败：有效七节点图，5 次 Composer notice、
10 次已结算 Composer 调用，未组队或执行 Worker；无原始模型回复，不能断言缺失字段。

后续 Composer 显式 null/default-schema 修复组 45 passed / 5.89 秒，审查当前节点 scope/
自适应 cap 组 123 passed / 31.80 秒。全量 v4 为 781 passed、6 failed、1 warning /
290.98 秒，失败的 6 个旧 M4 Mock Planner criteria fixture 已保留；仅将 fixture 改为
可执行 delivery_review 后 21 项回归通过 / 12.89 秒。[v5 全量](audit/phase2/frozen-tests-v5.xml)
及[日志](audit/phase2/frozen-tests-v5.log)为 820 passed、1 warning / 219.96 秒；manifest
153 文件，哈希 `a1f4646cb9acc810f3d35e8f9609a99ed553efd1ce4c2b3a0906d98c0333ed93`，
Ruff/150 文件格式/diff 通过。v5 早于后续 v6 改动。[v6 全量](audit/phase2/frozen-tests-v6.xml)及[日志](audit/phase2/frozen-tests-v6.log)
保留 848 passed、1 failed、1 warning / 283.77 秒；实际 Worker 并行测试暴露 Gateway 并发 1
与 Worker cap 2 被混同，operator cap 误降 1。v7 恢复独立 Worker cap 2，原测试不改。[v7 全量](audit/phase2/frozen-tests-v7.xml)及[日志](audit/phase2/frozen-tests-v7.log)
849 passed、1 warning / 216.87 秒；[155 文件 manifest](audit/phase2/source-manifest-v7.json)
`58339132008a97956237ec38c2eb19eb7062f46b4ef2f5f946567ad52be26627`，Ruff/152 文件格式通过。
它早于 v8，不覆盖新严格 schema/范围/repair 改动，最终待验。固定
[benchmark v4](audit/phase2/benchmark-final-v4.json) 为 6.3642 秒、0 Provider 调用。

后续 L2 `20261115T013328Z-L2-fed968` 在 80.711 秒失败：16 次模型、6 次工具，
54,597/9,558 输入/输出、未知 0，raw/native 68,294/68,550 超过 65,536，并发 3 超过 cap 2。
L3 `20261115T013328Z-L3-4e61d3` 在 151.834 秒失败，最终为合法 7 角色/并发 2；研究
产物 15,776 字节，raw/native 67,009/68,224；Worker 已用 38,989/70,000、剩余 31,011
不足完整审查。两次均无 `review.started` 或重启。

v6 对新中间 JSON 提供约 6 KiB 软指导，完整原要求、代码和最终报告保留，不截断旧产物；
Planner 在原总额内指导预留完整 context 审查/响应/修订预算。新团队/替换提案及审批执行
operator cap；v7 main/Worker 使用 Worker cap 2，独立于 Gateway 模型并发；harness 总 token/并发
取 CLI 与场景较小值并记录有效值。[Composer 组](audit/phase2/composer-concurrency-20261115T094900.xml)
69 passed / 5.70 秒、[Worker 指导组](audit/phase2/worker-report-guidance-tests.xml) 99 passed /
28.05 秒均为本地/Mock，不能代替全量/live；原 packet/PASS/context/权限未扩展。

v6 L1 `20261115T014735Z-L1-b48614` 在 122.096 秒失败，两轮 NEEDS_REVISION 与前自修
耗尽原 Worker max_repairs；最终未知 0。L2 `20261115T014735Z-L2-cd59cb` 84.678 秒失败，
14,060 字节只读 delivery 未受 JSON-artifact 指导；raw/native 60,371/60,425，已用
18,143/78,000、剩 59,857，单审查输入已差 568。L3 `20261115T014735Z-L3-4ce51e`
165.77 秒失败，产物 11,651 字节、raw/native 65,637/66,278，已用 57,093/90,000、
剩 32,907；27 调用、123,506/22,569 输入/输出、未知 0。L2/L3 均未审查或重启。
v7 对有后继的 delivery_review 也提供软指导，引用集中避免反复哈希，中间角色 output 建议
2048（v8 已取消该偏好）；source-heavy 首节点在总额允许时约两 context 累计预算，至少 10% 协调 reserve。
完整要求和原限制保留。

v7 三次 live 均失败、最终未知 0：L1 `20261115T015415Z-L1-d9dedd` 98.792 秒，
16 模型/4 工具、57,113/7,877 输入/输出，2048 截断耗尽 repair；L2 `20261115T015415Z-L2-b0e9f0`
93.078 秒，19/6、78,714/11,191，两节点违背 3–4 专业 Worker 要求，14,141 字节产物/context limit；
L3 `20261115T015415Z-L3-2732f0` 99.483 秒，24/9、127,932/11,268，真实 NEEDS_REVISION
后耗尽 10 步，返工未完成。压缩/重启复杂连续性未证实。

v8 取消 2048 偏好，Worker 明确当前节点 scope；Planner 只在整个任务简单只读时单节点，
保留专长/数量；Composer role ID 唯一/专长正确，用户允许时建议 max_steps=16；native main
自修和审查返工共享 max_repairs=4，原步骤/token/工具/费用/时间/轮次与已有计数保留。
仅新 TeamWire 显式 null 选择 STRICT 八字段、confidence 0–1，模型必须提供且 Core 不填默认；
历史/direct/显式旧 schema 不改。非有限 JSON 按原 repair 拒绝。[合同组](audit/phase2/native-delivery-contract-20261115T151500.xml)
185 passed / 47.49 秒、[最新文件](audit/phase2/native-delivery-nonfinite-20261115T152200.xml)
36 passed / 5.97 秒是有重叠本地/Mock。[v8 全量](audit/phase2/frozen-tests-v8.xml)及[日志](audit/phase2/frozen-tests-v8.log)
886 passed、1 warning / 283.46 秒；[156 文件 manifest](audit/phase2/source-manifest-v8.json)
`92bb6067ff7bc1dadfa6aa9b5994089969603b53b3980a9a5601d0a140e6b9e3`，Ruff/153 文件格式/pip通过。
它早于 v9；v12冻结检查见下，机器汇总记录本轮实际部分状态。

v8 L1 `20261115T020604Z-L1-dceb58` 136.466 秒失败，23 模型/14 工具，已结算
128,890/14,443 输入/输出；L2 `20261115T020604Z-L2-7f6370` 99.597 秒失败，25/15、
109,862/9,567。两次实际 NEEDS_REVISION 后反复 file_read 耗尽 tool budget，停机时各有
1 项 Router notice 未结算，reservation 保留，真实总用量/费用未知。L1 两 archive 为17/6来源，
L2 四 archive 为9/6/6/4，均无 Core 中途重启/停机 Router 压缩。L3 `20261115T020604Z-L3-30acf0`
103.132 秒失败，18/6、84,067/12,506、未知0；批准artifact criterion却仅read tools，实际文件不存在，
未审查、无后继。见[独立连续性审计](audit/phase2/continuity-and-live-diagnosis-v8.md)，不据archive推断循环成因。

[无损refs表示](audit/phase2/reviewer-lossless-v8-diagnostic.json)本地省524 native input reserve字节，
64,346+1,190=65,536；原packet/hash/raw cap不变，[124项](audit/phase2/reviewer-lossless-v8-tests.xml)
60.43秒仅离线构造。[benchmark v8](audit/phase2/benchmark-final-v8.json)17.598702秒/0Provider调用。
v9对生成节点具体artifact verifier硬验同节点file_write，不通过走原有界schema修复、不自动赋权；
Worker显示原steps/tools/tokens余量与archive source_refs，超额batch在任何工具执行前按原repair修复。
预算提示仅在有效单次 allowance ≥8192 时注入；首次无条件提示造成的旧 context 回归保留于
[诊断](audit/phase2/budget-feedback-intermediate-failure-v9.md)，修复后旧文件48项/15.81秒、
Planner/publication36项/3.64秒通过。这些是有重叠局部测试。Publisher仅新增retry数字及
review reason/context白名单。原预算/步骤/工具/权限不扩大。

[v9 全量](audit/phase2/frozen-tests-v9.xml)及[日志](audit/phase2/frozen-tests-v9.log)为
895 passed、1 warning / 237.83秒；[157文件manifest](audit/phase2/source-manifest-v9.json)
`9a33b199404b777428ff0f0e1a1c1da356ad93b23b4395565fc0d211b3d9203a`，Ruff/154文件格式/pip通过。
它早于v10容量反馈/重试计数修改，最终快照仍待验。[benchmark v9](audit/phase2/benchmark-final-v9.json)
5.9372256秒/0 Provider调用，仍是固定合成机制实验。

同代码快照`624f0ac7cb1873163e2e0a5b1849642bb363d553aedab4331096a0286b134db8`的
v9 L2 `20261115T021801Z-L2-b11011`在80.141秒失败：17模型/6工具，已结算65,607/9,348
输入/输出，未知0；L3 `20261115T021801Z-L3-290b7f`在97.584秒失败：18/6、71,908/14,522，
未知1个Router notice，保留9,985+4,096 reservation，真实总用量/费用未知。实际中间产物分别为
15,235/10,788字节并匹配回执哈希；只读重建raw packet分别68,207/68,967字节，超过原65,536。
两次均`review_packet_too_large`、`review.started=0`，首Worker停在review_pending；无后继/重启。
全部39份已结束driver记录已安全发布到[snapshot-v2](audit/phase2/live-runs/snapshot-v2/index.json)，
保留原run.json与旧diagnostic-snapshot-v1；发布不发Provider请求，也不追认失败任务。

v10修正未来driver的重试统计：原`repairs`包含审查返工，与review revision相加可能重复，
改为分开计数实际self_repair事件并标记`disjoint_event_counts_v2`；历史数字保持原样。
[局部回归](audit/phase2/retry-accounting-v10-fixed.xml)27项通过，不能替代新全量或live。
v10已接入Core容量诊断、原预算返工守卫与停机前只读settlement等待；结果如下，容量反馈自身不是模型审查或PASS。

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





当前不能宣称 FORGE 已普遍自主完成复杂任务。通过真实失败、修复、独立审查返工、
上下文压缩和 Core 重启后仍连续完成复杂任务，是本阶段待证实的核心门槛。
通用剩余DAG自动重规划、任意Agent层级扩展、学习式语义检索和独立审查模型选择仍未完成。
Review 默认保持启用；计划、团队与替换仍由已认证本地用户批准。
新发布记录的 `runtime_settings` 仅含白名单中的 reasoning、审查轮次、Worker
租约、Provider 超时和重试上限，以及有效模型总 token/并发上限；旧记录缺失值保持 null，不推断或追改历史配置。

当前检索仅为词项与有限双语别名。工具只支持固定诊断、Python 语法检查和有界数值
AST，未提供任意 shell、通用 Python 执行、任意测试命令或代码补丁工具。
`file_write` 仅能新建；显式批准的 R1 `file_revise` 可按最新成功回执链和未变化哈希，
原子替换当前 Worker 自己产出的文件，并保留不可变历史。既有合同不自动增权，
用户文件、前驱产物和未知结果均不在权限内。Reviewer 按当前节点目标、criteria 与批准角色
审查；原请求完整保留，最终交付节点仍承担全部原始要求。输出受 8192、角色/模型上限、
剩余 context 和原累计 token 余量约束，自适应 floor 为 `min(1024, original output cap)`；
不足或原费用权限不足仍拒绝，原本低于 1024 的上限仍有效。native Gateway 按含 schema/
reasoning 的实际序列化 payload 预留输入，与 Core 准入一致。Core 解析器反馈明确标记来源，
不冒充模型审查或 PASS；已结算截断重试继续消耗原预算和审查轮次。
真实固定数值套件失败可生成 `deterministic_semantic_validation` 反馈，仅在
显式修订权限、有效 grant、原预算余量且无未结算动作/模型调用时返工；新交付必须
重跑真实套件。反馈提交后 callback 前崩溃可在重新检查原始语义来源、当前结果、
权限/grant、原预算和未结算调用后幂等恢复，semantic-only 节点无需发模型审查；
旧结果、变化来源、撤权、预算耗尽、未知动作与回调异常仍 fail closed。
Worker 只在已结算步骤间续租，保持代次围栏、审批检查和任务截止；不能复活过期 claim。
团队替换沿用当前 DAG，
新 DAG 与 Router 目标更新只接受 proposed/awaiting-approval 阶段，未实现运行中
剩余 DAG 的通用自主重规划。
