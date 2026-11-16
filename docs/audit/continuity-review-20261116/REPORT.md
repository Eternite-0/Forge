# Continuity fixture 独立审查与原 17 项失败分类

日期：2026-11-16（Asia/Shanghai）。写集仅 `/Users/tracy/Documents/Forge/docs/audit/continuity-review-20261116/`。

## 1. 结论与交付边界

- **原 17 项均已分类，并各有对应离线通过观察。** 不建议为此撤回当前 fixture 修复；在所审合同范围内未发现通过放宽权限、篡改来源、抹去费用或制造执行回执来“修绿”的变化。
- 四项旧成功用例改为“原限额不足必须拒绝”是合理的保守合同变化，**不代表原任务仍然可行**。原审查指出的正向覆盖损失已通知主代理。主代理随后新增合法小双独立分支（原 360k）和可负担 operator 交集（170k ≤ 原 180k）用例；它们是独立的小任务，不是给旧失败任务涨预算。提供方 `positive-capacity.log` 已见 **19 passed in 4.60s**；本审计未再次运行，仍须 final full 观察。
- 新增的三项 provenance 失败不属于原 17。已按 `context_only` / 执行回执拆分修复，独立补测确认三项通过；唯一真实 `file_write`、无上下文执行标签、无额外 action receipt 和原篡改拒绝断言仍在。
- Cicero 补充的 Thread artifact omission 对照独立补测 **6 passed**：读取完整但遗漏产物要求时不能凭自述直接过关，必须实际修正产物。它是 scripted/literal reviewer 的离线证明，**不是实际模型语义验收**。
- **没有启动真实 Provider、没有修改源码或测试、没有运行真实 probe。** 离线测试中的 `execution_mode=live` 是 fixture scripted adapter 的标签；本地 loopback Gateway/Core 不等于外部 Provider。
- **尚不能宣布当前工作树 full backend 全绿。** 已提供的 201 / 226 / 81 passed 和本审计的定向组不能跨快照相加，不能代替主代理完整回归。收到主代理收敛要求后，本审计停止新增 pytest 运行。主代理报告 final full 于 **2026-11-16 17:58 +08:00** 启动、预计约 11 分钟；本报告冻结时仍在进行，结果待主代理最终报告。

## 2. 身份、原始失败与不可变证据

读取了仓库 `/Users/tracy/Documents/Forge/AGENTS.md`、`/Users/tracy/Documents/Forge/README.md`、相关实现与测试；没有发现 docs 下更具体的 AGENTS。

| 身份 | 值 |
| --- | --- |
| 指定 baseline | `0bef3316a5783348c1f5687d9d584a2cf1eb5e7a` |
| 审查 HEAD | `7c1763bb2ce962a924f2266c55978e1483bd112b` |
| 两者的 backend Git tree | **均为 `016a4eadb5034871c00148afda88279224e36822`** |
| 两提交 backend 差异 | 空；其间提交为 desktop 变化 |

证据：`evidence/baseline-to-head.txt`、空的 `evidence/backend-baseline-to-head.diff`、各阶段 manifest/diff。上述结论是**已提交 Git backend 相同**；原日志没有逐文件执行冻结清单，因此不能据此额外证明原 pytest 运行时无未提交改动，也不能将资源策略失配归因于这两个提交之间的 backend 变化。

原 `.local/continuity-review/baseline-full.xml` 记录：2026-11-16 **16:28:23 +08:00** 开始，2961 项，**2932 passed / 17 failed / 12 skipped**，XML 时长 688.954 秒，原 log 汇总 689.37 秒。原始 log/XML 在原位置未修改；压缩时未删改任何字符，解压与原始字节相等。

| 原文件 | 字节数 | 原始 SHA-256 |
| --- | ---: | --- |
| `baseline-full.log` | 159199 | `62fee10bca46bc85e5086f98f32a7d28199f3bddbb1b89855e2609398f2176ef` |
| `baseline-full.xml` | 609173 | `fceef78021d8bf399239fbe2c56e754307099ca004832c7de597458e55a63b2b` |

对应 gzip：

- `evidence/baseline-full.log.gz`：`7f04e0680a8cf41c89f07397073838999451c7a8f3199857dac2bc8586eb2d9a`
- `evidence/baseline-full.xml.gz`：`91c215fa74241e3813a0f7d3c8fc5cb14e407f5ac09a80e6453e7db06a38249c`

`evidence/baseline-failures.json` 保留每一原始 test ID、错误消息及 failure 文本哈希；`evidence/failure-classification.json` 逐项对应分类、合同变化、判断及离线通过位置。不得用修复后的 XML 替换这些失败证据。

## 3. 原 17 项逐项分类

表内模块名对应 `/Users/tracy/Documents/Forge/backend/tests/` 下同名 `.py`；完整 ID 与原错误详见机器报告。分类计数：**资源策略 4、原限额不足 4、mock 模型资格 5、平台 1、parser 1、完整 Thread 读取合同 2，共 17**。

| # | 原失败：模块 / 测试 | 原因与合同变化 | 判断和不弱化安全的依据 |
| --- | --- | --- | --- |
| 1 | `test_composer_resources_v21` / `test_production_pump_uses_semantic_wire_then_full_native_service_admission` | 原写死 output 2048，策略实际为 4096；改为策略函数且固定锚定 4096。 | 合理；不是仅用同源函数自证，strict schema、实际准入、总预算断言保留。 |
| 2 | `test_core_measured_planner_v19` / `test_core_protocol_quantifies_single_chain_and_independent_branches[branch]` | 原三节点独立分支超出原 360000；由构造成功改为拒绝。 | 合理但改变成功覆盖；要求 `required_total > original_ceiling == 360000`、无计划历史、wire 不变。新小双分支另补，不能声称旧三节点成功。 |
| 3 | 同模块 / `test_core_uses_original_operator_intersection_without_accepting_model_caps` | 原 operator 120000 不足；改为拒绝而非提升上限。 | 合理；准确保留 120000、诊断超限、空 history。新的 170k∩180k 正向小任务不替代这项旧拒绝。 |
| 4 | 同模块 / `test_selected_team_is_remeasured_and_approved_with_explicit_stage_margin` | 角色固定 output 2048，与测量 4096 档不一致；output 用策略，input 等额扣减。 | 合理；input+output 不增加，长 responsibility 引起的 actual 重测、minimum 与审批检查保留。 |
| 5 | 同模块 / `test_core_preserves_large_output_bounds_and_context_rejection` | 32768-byte 交付需要 8192 档，旧 max_output=4096 mock 在目标诊断前被排除。 | 仅改该测试 mock profile 上限至 8192；仍拒绝、恰两请求、wire 不变；不改真实 context 或任务限额。 |
| 6 | `test_core_measured_serial_v17` / `test_actual_team_contract_is_remeasured_with_approved_role_cycle_headroom` | 与 #4 相同的输出档位前提失配；input 减少策略 output、output 同步。 | 合理；不增加总额，串行共享储备、每成员 minimum ≤ execution_token_limit、审批保留。 |
| 7 | `test_live_core_private_control` / `test_private_real_core_restarts_and_stops_without_provider_calls` | harness 写死 Windows `.venv/Scripts/python.exe`，macOS 不存在。 | 源码改 `Path(sys.executable)`；非 skip/放宽断言，鉴权、密钥管道、shutdown 不变；本地两次启停、账本 0 attempts。 |
| 8 | `test_planner_dataflow_v21` / 原 `test_constructed_original_router_source_capacity_is_feasible[1]` | L1 当前完整周期测量超出原 180000。 | 重命名为 `respects_current_ceiling`；要求 CapacityPreflightError、准确原 ceil、原 bounds/edges 不变、空 history；不再证明原 L1 可行。 |
| 9 | 同模块 / 原测试 `[2]` | L2 当前测量超出原 360000。 | 同 #8；不涨预算，L3=630000 正向保留，但不能以 L3 通过替代原 L2 可行性。 |
| 10 | `test_planner_guidance_v24` / `test_complete_preview_explains_all_cycles_and_extra_edge_loss_of_serial_pool` | 现完整周期 5 次，旧预期 4 次。 | 改为 5；保留 minimum=initial+reserve、headroom≥0、加额外边即失去串行共享池；没有缩短审核/修正周期。 |
| 11 | `test_planner_protocol_v23` / `test_valid_nodes_get_all_raw_native_correction_metrics_while_bad_criteria_stay_unknown` | 大输出诊断候选先被 4096 mock profile 淘汰。 | 局部 `diagnostic_setup` mock 上限 8192；overflow、无效 criteria unavailable、PRIVATE 不泄露及不调用真实 provider 等原检查保留。 |
| 12 | 同模块 / `test_first_rejection_combines_schema_and_all_independent_capacity_measurements` | 同资格问题导致缺第二次请求。 | 局部 8192 mock；仍首轮拒绝、完整反馈、恰两请求、空 history，不接受原坏 proposal。 |
| 13 | 同模块 / `test_normal_preflight_still_rejects_a_packet_which_diagnostic_only_can_measure` | 同资格问题；diagnostic 能测与 admission 能过是不同合同。 | 局部 8192 mock；正常 preflight 仍拒绝该 packet，不改 schema、context 或 bounds。 |
| 14 | `test_planner_protocol_v25` / `test_rejected_custom_bounds_get_one_compact_repair_without_changing_their_values` | 同资格问题导致 compact diagnostic 缺 rows。 | 此一测试复用局部 setup；保留一次 compact 修复、原 custom bounds 不变；不是允许模型重写资源上界。 |
| 15 | `test_router_json_protocol` / `test_parser_resource_limits_have_bounded_fixed_diagnostics[text1-nesting_limit]` | CPython JSON decoder 的递归行为不同，原 3000 层报 expected_value 而非 nesting_limit。 | 源码增加 quote/escape-aware 扫描，深度>1000 固定拒绝；仍走原 json/schema 检查，不接受非法 JSON、不泄露 parser 内容。另两项字符串内括号/转义引号测试已补并独立通过。 |
| 16 | `test_superagent_loop` / `test_semantic_trigger_source_reaches_worker_prompt_and_actual_scoped_read` | 原只从 offset 2150 读尾部，不满足完整来源合同。 | 改 offset0/max4096 完整读，仍两请求；没有伪造读证明或提升运行限额。 |
| 17 | 同模块 / `test_user_conversation_autonomously_admits_worker_reads_sources_and_relays_only_via_router` | 原请求的读取不能消费后来新增 Thread 请求。 | 增加真实 `read_trigger(followup.id)` 和返回文本断言，请求 2→3；保留隐藏 Worker、Router 唯一会话出口等合同，不自动标记已读。 |

这 17 项中的 #7、#15 是实际跨平台/运行时源码修复，不应全部表述为“只改 fixture”。其余 mock profile 提高只是让测试具备到达目标诊断的资格，绝不代表提高真实模型能力或旧任务预算。资源策略相关变化并非本次未提交 diff 新增策略本身，而是让旧 fixture 与已存在策略一致。

## 4. 新 Thread review 合同与三项 provenance 后续修复

实现审查对象：`/Users/tracy/Documents/Forge/backend/src/forge_core/independent_review.py`、`worker_runtime.py`、`worker_continuations.py` 以及对应 provenance/continuation/artifact 测试。

1. 每个 Worker 模型回合从持久来源证明重新计算 pending，包括首轮、后续、compaction/restart 后；提示内容只是有界 metadata，不伪造读取。其字节开销计入原容量，来源不可用时 `continuation_source_unavailable` fail closed。原 steps/tools/tokens/output/权限不重置。
2. `_review_receipts` 先调用 `_verify_worker_context_call`，再把 `read_trigger` / `handback_trigger` 投影给独立 reviewer；`context_only=True`，不带 `execution_mode`，不生成 artifact/action receipt。Recall 引用也不被当成“当前任务已经执行”的证明。
3. reviewer 提示明确“读过不等于产物落实”，范围内要求应对照真实产物；不采用须解释或 proved handback；Thread 内容不授予新工具/权限。提示本身不保证模型判断，因此单列离线产物对照与真实验收边界。
4. 原审计 safety 组发现三个旧 `len(receipts)==1` 失败（read_trigger、recall_object、handback_trigger）。baseline 中这三项原本通过，故单独记新增回归，不混入原 17。历史失败 XML/log 原样保留。
5. 后续 fixture 按两类过滤：执行集合恰一个 `file_write` 且 `execution_mode=live`；上下文非空，仅 read/handback 且没有 execution_mode，并含当前 trigger。原 `total_changes` 不变、`call.receipt_id is None`、action_receipts 计数=1、篡改拒绝没有删除。**此合同调整合理，独立补测三个参数均通过；live 标签只是离线适配器的测试身份。**
6. 新 artifact 对照检查 reviewer 可见完整 source 的 hash/分页、真实本地产物和 receipt；故意遗漏范围内要求即 NEEDS_REVISION，修复使用 prior receipt/expected hash 的 `file_revise`。范围外解释/handback 不扩大 authority；仅 context 无 action 无法准入 review。mock PASS 仍不匹配 live completion，不偷换真实成功。

另外审阅的 project/Recall diff 改善路径边界、嵌套/重名项目消歧，Router 给出 project/archive typed next hops，并在 router archive 中提供原消息引用；不把路由焦点当作工具授权。新增 restart-Recall 离线组用实际工具结果跟随引用、分页历史原文，并检查跨项目猜测、来源变更及撤权等拒绝。**它证明编排与来源路径，不证明真实模型会主动 Recall，也不是新的生产验收。**

## 5. 运行证据与时间线（不可合并为 full）

### 提供方原日志（本审计保全，不冒称独立复现）

| 文件基名 | 原结果 | 说明 |
| --- | --- | --- |
| `baseline-full` | 2932 passed / 17 failed / 12 skipped | 原始失败证据，不覆盖。 |
| `failure-clusters` | 244 passed / 15 failed | 259 项中间态。 |
| `failure-clusters-fixed` | 195 passed / 6 failed | 201 项中间态；5 项为 `dataclasses.replace` 用在 Pydantic ModelProfile 的 TypeError，当前已改为 `model_copy`；另有旧请求数断言。不可把历史错误归给当前 model_copy。 |
| `failure-clusters-fixed2` | **201 passed**, 1 warning，36.52s | 主代理更新的修复结果；平台/parser 另测，不能把 201 当 17 项全集。 |
| `review-targeted` | **226 passed**, 97.97s | 定向 review 组，不是全后端。 |
| `continuity-targeted.log` | 125 passed / 3 failed | provenance 修复前中间态也保留。 |
| `provenance-fixed.log` | **81 passed**, 22.25s | 主代理确认含 JSON；提供方只有 log，无对应 XML 可核验逐项集合。 |
| `positive-capacity.log` | **19 passed**, 4.60s | 交付前只读取得的后续正向覆盖组日志；本审计未重复运行，没有 XML 逐项归属。 |
| `new-execution-regressions` | **57 passed**, 1 warning，35.14s | 提供方后续 XML 可核验 continuation30、restart-Recall21、artifact-review6；只读纳入，不是新增独立复跑。 |

以上分别在 `evidence/manifest.json`、`evidence/update-manifest.json`、`evidence/handoff-manifest.json` 记录原字节和 gzip SHA-256；保留 `recall-parser.log.gz` 等早期证据。

### 本审计独立执行（均发生在主代理要求停止新增 pytest 之前）

| 组 | 观察 | 快照与解释 |
| --- | --- | --- |
| `runs/offline-fixtures` | **275 passed**, 1 warning，36.42s | 17:24:49–17:25:26；10 个 fixture 文件，覆盖 16 个原失败；前后 tracked diff hash 均为 `098807d1ab3a66417aef1121fa735d5ab406b0a732020fcbd8ca7698af5e0c38`。 |
| `runs/safety-fixtures` | **145 passed / 3 failed**，89.89s | 17:29:30–17:31:01；原生命周期失败已通过，新失败为三项 provenance 旧计数；前后 hash `ac07b52a6fc9fbdf214067a9f319230ed5e2c921cca89ea9fb40456f0f5abfb8`。 |
| `runs/followup-fixtures` | **157 passed / 7 failed**, 1 warning，71.35s | 17:48:24 开始；前后 tracked diff 相同；三项 provenance 已过，7 失败均为写集与 minimal-probe workspace 保护冲突，见下。 |

前两组之间 worker_runtime 新增了 DomainError fail-closed，因此不能把它们相加视作一次冻结源套件。followup 的 tracked diff 前后相同（`d38898eacd6f8ba0c511bf5e548a1376169cb44751e9a765a5dce6681bbc13fe`）；其 runner 的 before/after 清单只记录 tracked 文件，新增 untracked 测试有单独内容快照，但不冒称为原子执行冻结。

followup 分模块：provenance **21 passed**，continuation **30 passed**，artifact-review **6 passed**，restart-Recall **21 passed**，parser **60 passed**；minimal-probe **19 passed / 7 failed**。详细 ID/分类在 `evidence/followup-classification.json`。

**7 项限制性失败：** 本审计必须把 pytest tmp 放在自身 docs 写集内，而新 probe 的 `fresh_workspace` 拒绝仓库内非 `.local` 路径，报 `workspace_must_not_modify_repository_sources`；两个用例因此先于它们原期望的错误/成功返回。这里保留原失败，不删断言、不绕过路径保护、不搬到未授权写集外。它们不是证明产品功能通过，也不是要求撤销正确的 fail-closed 安全策略。本审计不独立背书整个 probe 文件通过。

安全措施：审计 runner 清除选定 Provider 环境变量，设置禁止字节码与 pytest cache，阻止非 loopback DNS/连接，并通过 `sitecustomize.py` 传递到 Python 子进程；本地 Gateway 由 scripted fixture 提供，不连接真实端点。并非仅凭 loopback 允许策略就断言“无真实 Provider”，还核对了 fixture/script 的 mock/禁止真实调用路径。私有 Core 生命周期同库两次启停后 `model_budget_attempts=0`、SQLite integrity ok，见 `evidence/lifecycle-zero-attempts.json`。临时工作区移除前已抽取计数；仅删除本任务自己的 scratch，保留运行日志/XML。

## 6. 通知、后续修正与 final full 待观察

- 发现时已在任务进度消息及 `MAIN_AGENT_NOTICE.md` 提醒三项 provenance 回归、正向 cap 覆盖损失、产物落实的离线对照不足。原通知另保存在 `evidence/main-agent-notice-original.md.gz`，后续状态不抹去历史。
- **主代理通过 send_input 明确表示收到 MAIN_AGENT_NOTICE**，并提供 provenance 修复后 81（含 JSON）passed、Cicero 补 artifact omission 对照和正向 cap 补测的进展。这不是终端用户确认。本审计没有调用跨任务发送工具，不虚构工具送达；以主代理确认收到为交接依据。
- 已读新增正向用例：双独立节点继续受原 360000 限制，断言独立周期、无串行池、总分配不超 cap；operator 170000 与源 180000 取交集、reserve17000、总额不超170000。旧 360k 大分支和 120k operator 拒绝仍保留。**这合理弥补“仅剩拒绝”的局部覆盖提醒，不是给原失败任务追加预算。** 交付前已读取提供方 positive-capacity 的 19 passed 汇总，但未由本审计复跑；不把该汇总推广为 full。
- **最终仍须观察主代理 final full：完整命令/环境、执行源快照、XML/log、通过/失败/跳过计数及新失败归因。** 主代理报告其于 2026-11-16 **17:58 +08:00** 启动、预计约 11 分钟；本报告就此冻结，运行结果待主代理最终报告，不持续抓取快照。运行中不应报告当前树 full green；若变化发生在审计快照之后，应以新快照归属，不回写原 baseline。
- 真实 Provider run / 全部语义能力验收未启动，仍不通过；后续 full 的离线通过也不能自动改变这条边界。

## 7. 文件索引与复核

所有相对条目均位于 `/Users/tracy/Documents/Forge/docs/audit/continuity-review-20261116/`，不引用临时 pytest 工作区作为保留证据。

- 本报告：`REPORT.md`；主代理交接：`MAIN_AGENT_NOTICE.md`。
- 原 17 项精确错误：`evidence/baseline-failures.json`；逐项合同判断：`evidence/failure-classification.json`。
- 原始与新增日志：`evidence/*.log.gz` / `evidence/*.xml.gz`；初始及更新哈希：`evidence/manifest.json` / `evidence/update-manifest.json`。
- 快照时间线：`reviewed-working-tree.diff`、`review-end.diff`、`review-final.diff` 和 `handoff-final.diff`；相应 manifest 保存具体时刻与每文件 hash。`working-files` / `end-working-files` / `final-working-files` / `handoff-working-files` 保留当时内容；reference-files 保存相关未改合同实现。
- 独立日志/XML、before/after diff、结果和审计 runner：`runs/`。7 项 probe 限制解释：`evidence/followup-classification.json`。
- 交付文件校验：`SHA256SUMS`；gzip 等同性和 17 项映射等验证：`evidence/verification.json`。这些校验不代替代码正确性或真实验收。

**最终审查状态：原 17 项修复合同有条件认可；三项 provenance 回归关闭；离线产物对照已补；合法正向 cap 后续修正已记录。仍须 final full 观察，真实 Provider 验收未做。**
