# Sprint C / I — 动态团队与可复现实验

日期：2026-11-15（Asia/Shanghai）。本文记录离线工程验证与合成研究；本子任务未调用 Provider，不能据此宣称真实模型任务成功。

## 审计结论与最小调整

既有 `TeamComposerEngine` 已实现 single_model / greedy / coverage / complementarity、上下文/工具/预算硬约束、有界搜索、模型与角色分离。`TeamCompositionService` 已具有审批与画像哈希绑定、三次重组上限、历史批准校验和已完成节点保留。保留这些实现，不另建组队系统。

此前 Gateway 观测“能采能算”，组队却未消费线上表现。新增 `model_performance.performance_snapshot` 从原有 Core 表读取有界最近样本，分别报告 transport success、经过完成门的 Worker terminal success、工具成功、重试、schema compliance、平均/P95 时延、已结算 token/cost、独立 verifier 通过率。各测量包含样本数、来源、时间和来源引用；比例带 Wilson 95% 区间。未知价格、用量或任务结果保持未知。transport success 不等于任务成功。

团队提案在同一事务内读取 live-only 观测快照，以 SHA256 和最多 32 条来源引用固定其依据。默认至少五个观测样本才参与保守的区间下界评分；这是准入下限，不是统计充分性结论。单次成功不会形成能力评分。手动画像、能力测量与批准画像哈希保持原值，线上观测另存于提案快照，不自动覆盖用户评分。评分参数可通过内部 `TeamComposerEngine` 注入，未知数据不自动转为零分能力。

重组提案现在可仅覆盖尚未完成的节点，继续兼容旧的全节点提案。已完成节点及其原 Worker/历史批准不被改写，未结算动作、批准权限和三次重组上限继续生效。预览角色的数学/工具能力名改为注册表的标准名称。`TeamRoleWire` 保留本地可省略字段的兼容性，同时生成 Provider strict schema 要求的 required nullable 字段。

## 可复现实验

从 backend 运行以下命令，输出必须为新路径；脚本拒绝覆盖历史记录，失败章节也落盘：

```powershell
.\.venv\Scripts\python.exe scripts/phase2/intelligence_benchmark.py --output 'D:\path\to\new-benchmark.json'
```

最终子任务结果：`docs/audit/phase2/benchmarks/offline-intelligence-v2.json`，耗时 9.48 秒，Provider 请求 0。v1（3.55 秒）保留为诊断；v2 包含 Sprint F 改进后的固定源时间与从权威原始行独立重算的 source hash 检查。

| 实验 | 固定数据与结果 | 适用范围 |
| --- | --- | --- |
| 团队策略 | 6 个领域/难度场景 × 8 个策略/消融；48 个提案均满足独立硬约束评分。Static 的 specialist proxy 为 0，greedy/coverage/complementarity 为 1，动态失效替换为 0.75 | 合成能力与价格，只测选择机制；不是模型任务成功率 |
| 压缩 | 6 个场景 × full/recency/extractive summary/task-centric/task-centric+recall；统一 6144 UTF-8 字节输入上限；带源召回恢复 12/12 约束，task-centric 为 6/12 | 真实 ContextService 归档、重开数据库、按 source ID 与原文 SHA256 核验；字节不是实际模型 token |
| 治理 | 既有 ToolEngine 15 案例矩阵：11 个应拒绝、4 个应允许，false allow/deny 均 0；3 次执行请求产生 2 个唯一回执 | 真实本地工具 + 明确 Mock Worker fixture；model-claim 等消融是 counterfactual，不授予权限 |
| 召回 | Sprint F 的固定双语数据集 9/9 source-ID/hash 检查通过 | 词项/别名、Quest/时间/来源过滤；不是 embedding 语义评测 |

压缩的 full-context 原文超过共同输入上限而被拒绝，不能解释为原文丢失信息。Conventional summary 是明确的固定 extractive baseline，不是模型摘要。No-confidence 与 no-coordination 在这组六个团队场景中未改变 specialist proxy；没有据此宣称消融有效。动态重组的完成历史保留另由服务回归测试验证。

治理矩阵包含 receipt 检验、固定独立 NodeVerifier 和 evidence gate 的实际拒绝用例。它尚未完成独立 LLM Reviewer 与其余三种治理方式的同预算真实任务质量对照。真实 token、费用、返工质量、跨模型泛化和统计显著性不由本合成实验推断，仍需独立 live 验收。

## Live 验收输入描述器

新增 `backend/scripts/phase2/acceptance_scenarios.py`：

```python
descriptor = seed_scenario(level, workspace_root, repo_root=repo_root)
# descriptor["prompt"] -> Router；没有预置 plan/team/approval/receipt。
```

L1 使用真实 registry/observation 源码只读分析（2 文件，约 10.8 KB）；L2 使用真实 Router/Worker/Composer 的有行号、原文件 SHA256 的有界片段（3 文件，约 28.1 KB）；L3 使用真实 numeric_harness/tool contracts/composer 输入（3 文件，约 26.1 KB）研究并实现两候选调度器。需求定义 eligibility、minimum cost、tie=A，但不复制隐藏测试、期望答案或完成方案。让模型自行规划 DAG 与团队。

三个场景已在临时目录完成 seed smoke：所有输入哈希匹配；重复 seed 拒绝覆盖；无隐藏 `_CASES`。输入为源码数据，输出仅允许新建 work/ 路径。脚本不发模型请求、不启动 Worker、不伪造任何执行记录。实际审批、Provider、重启、压缩、返工与成果验证由主集成驱动负责。

## 检查记录

首次 focused run 为 37 passed / 1 failed：失败是已有 `TeamRoleWire.output_schema_json` 兼容改动导致生成 schema 不满足 strict required 约束；已保留解析兼容性并修正 schema。最终团队/重组/注册表及新增 benchmark/provenance 测试为 **42 passed，14.61 秒**。本子任务七个修改或新建 Python 文件的 Ruff 检查及格式通过；历史测试和审计未删除。

没有数据库迁移、运行依赖或环境变量新增。主要剩余限制：没有自动学习通用能力分数、没有凭一次调用评分、没有声称实现任意 Agent 层级/无限动态扩展；本轮的线上反馈是有界观察驱动的新提案排序。端到端真实性以主验收报告的独立 live 记录为准。

## 独立验收与历史证据发布

`backend/scripts/phase2/acceptance_checks.py` 提供 `evaluate_scenario(scenario, inspection, workspace, run_record=record)`。它只以 SQLite `mode=ro` 打开现有数据库，不创建 Store、不执行候选源码、不发 Provider 请求。逐项结果为 passed / failed / unknown，只有全部 passed 才通过。检查当前批准图与节点投影、历史批准团队、live Worker、精确 Gateway 调用、不可变工具回执、实际产物字节及哈希、八字段交付、输入来源完整性与读取回执。独立内容 PASS 通过 Core 的 `review_pass_matches` 在显式读取事务内重新构造来源包；L3 固定套件使用 `SemanticVerifier.validate_evidence` 重新验证源、回执与可信 provenance。

模型生成图要求 `quest.plan_proposed` 的 `planner_call_id / response_sha256 / graph_digest / source_event_id` 绑定到确切已结算 Planner 调用与当前图摘要；旧记录缺失该绑定为 unknown。L1 检查单 Worker、只读工具、排名发现或明确证据不足；L2 检查三至四个专业 Worker、真实引用和实际最终 JSON 的内容审查；L3 还要求不同有效输入的 numeric_ast 回执，其源码须与最终调度器逐字节相同。执行无关常数例子不能满足真实调度器实验要求。

L3 重启检查比较前后 Router identity 与当前数据库值，且要求已完成节点与剩余执行同时存在、实际 before/after_start 快照均无未结算远端调用或动作、成功节点及精确回执/调用/Worker 预算计数保留、最终数据库仍保留这些来源且计数不回退，并实际成功 resume。仅 approved-plan、尚未执行 Worker 的重启不满足 L3 验收。压缩需要实际 archive 和逐条原始来源 SHA256 回读。无法观测的用量、重启或压缩保持 unknown，费用未知保留 null。来源引用格式与范围可机械检查，引用与结论的语义相关性仍由独立 Reviewer 评估；模型判断不构成一般正确性证明。

`backend/scripts/phase2/publish_evidence.py` 只读取主驱动已经脱敏的 `run.json`，输出允许列表中的状态、失败码、用量、回执身份与哈希，不发布提示词、回复、日志、完整本地路径或产物正文。源快照与发布文件均以 SHA256/字节数索引，输出目录必须全新；后续实现不会追认历史失败为成功。首批 `docs/audit/phase2/live-runs/diagnostic-snapshot-v1/index.json` 包含 9 次主集成 live 诊断快照，其中旧记录未保存的状态保持 null；本子任务只发布已有记录，Provider 请求仍为 0。

```powershell
.\.venv\Scripts\python.exe scripts/phase2/publish_evidence.py --source '..\.local\phase2\runs' --output '..\docs\audit\phase2\live-runs\new-snapshot-name'
```

对抗测试 `test_phase2_integration_security.py` 另验证 Reviewer 未发送容量失败可退让重试、已结算或可信远端时限后的恢复、未知远端结果不重放、旧协程返回被最新 claim 隔离，以及已退款 reservation 的延迟响应仍精确计入实际用量和步数。所有 fixture 明确为离线协议或合成账本，不宣称真实 Provider 任务质量。

后续独立验收审查进一步收紧：L3 需要六个不同 Worker 节点与六个 role ID 分别匹配研究、数学、架构、编码、实验、审查；实验专业本身须执行最终报告引用的真实 numeric_ast 回执，最终调度器也须来自编码专业。单个通才或六个同名编码角色的关键词并集不能满足六专业要求。L1 交付、L2/L3 最终 JSON 报告的来源引用分别检查，同一条引用必须同时给出原始路径、原始 SHA256 和完全处于提供片段内的行范围，不能从另一个来源或中间报告借用行号。

场景 token 总额按隔离 Workspace 的全部 Gateway attempts 和历史 opening 计入，包含 Router、规划、组队、执行、独立审查和 HTTP 重试；未知用量保留其 reservation 的保守上界，不能以零补齐。实际用量或保守承诺超过 descriptor 上限即 failed，整体驱动耗时、所有历史 Worker 的步骤/工具/深度、已执行团队的并发上限和跨返工的审查轮次也按 descriptor 核验。较大的全局 Gateway 限额不能豁免场景限额。L3 压缩必须能回读本 Quest 完整性绑定的原始请求，并实际重建验证过的 Router capsule。

批准合同显式包含 `file_revise` 时，产物检查使用 `verify_file_revision_chain` 只读重验同一 Worker 的不可变修订链，再比较同路径最新成功回执与当前文件字节。历史版本的旧哈希不因合法返工被误判为篡改；无回执的文件修改仍失败。Publisher 只允许额外发布 64 位十六进制 `code_snapshot_sha256`，不展开任何配置或任意嵌套内容。

`docs/audit/phase2/provider-probe.json` 是主集成历史成功协议探针的允许列表摘要，源记录 SHA256 为 `8ca1d329d1f9e4efc8a4d35f78852623f19bf0475be1f5d85f11f627bf65f6c2`。11 个协议案例均成功，11 个 Gateway attempts 均结算，报告输入/输出分别 5444/637 token；代理报告的上游模型身份未独立验证。65536 context 只是操作者上限，实际最大 context/output、价格和速率限制均未测定；本次发布没有发起模型请求。

## 固定语义失败的受控修订与崩溃恢复

`NodeVerifier(store, revision_handler=manager.request_revision)` 只在真实固定数值子进程返回失败后创建 Core `review.result`：`verdict=NEEDS_REVISION`、`review_context=deterministic_semantic_validation`。它绑定当前 `worker.result`、批准计划与修订、Worker/run、真实 `semantic_run_id`、回执、源与报告 SHA256，并提供最多 10 个失败 case 的有界诊断。该事件不是模型审查，也不产生 PASS。

只有原批准图、节点、Worker 与团队成员均允许 `file_revise`，工作区授权仍有效，且 repairs、steps、tokens、tools、费用和 deadline 均有剩余、没有 pending/unknown 工具或未结算 Gateway 调用时，才调用既有修订接口。成功修订保持同一个 run、累计预算和不可变回执链，并清除旧验证 claim；新交付仍须重新执行真实固定套件，通过后才可完成。回调失败或假成功保持阻断；回调已提交排队后才抛错时，该确切 revision 被隔离为 blocked。

首轮新增 `backend/tests/test_semantic_revision_phase2.py` 的 21 个案例覆盖真实 bad scheduler → Core feedback → governed `file_revise` → fixed suite PASS，以及权限、预算、过期结果/claim、回调异常和未知动作。下列扩大检查为 **119 passed，44.14 秒**；历史命令没有传入 `--junitxml`，因此本段记录控制台文字证据，不补造 XML：

```powershell
# 在 backend 目录执行
.\.venv\Scripts\python.exe -m pytest -q tests/test_semantic_revision_phase2.py tests/test_semantic_gate_m7.py tests/test_semantic_evidence_m7.py tests/test_file_revise_phase2.py tests/test_worker_intelligence_phase2.py tests/test_acceptance_checks_phase2.py
```

后续窄修复将 `IndependentReviewService.tick()` 的模型审查 criteria 过滤移至已有 pending-revision 恢复检查之后；semantic-only 节点可恢复已持久化的 Core 修订反馈，仍不发模型审查请求。恢复前重新核验 `revision_available=true`、真实 semantic receipt/source 与当前批准执行的绑定、当前 scope/grant、工具/费用余量，以及 pending/unknown Gateway 和工具调用；`WorkerManager.request_revision` 继续核验当前 result、批准团队、累计 repairs/steps/tokens/deadline 和幂等消费。旧结果、变化来源、撤销授权、未知动作、预算耗尽和原先不可修订的事件均不能排队。

新增 `KeyboardInterrupt` 模拟反馈事务已提交、回调前进程退出；重开 Store、重建 WorkerManager 后恢复一次，再次 tick 不重放，真实受控修订与固定套件仍可完成。该测试文件现有 **30 个案例**。恢复检查组为 **74 passed，34.65 秒**，实际 JUnit 证据为 [semantic-recovery-20261115T090801.xml](semantic-recovery-20261115T090801.xml)：

```powershell
# 在 backend 目录执行；审计文件名由本次运行时间生成
$semanticAudit = Join-Path '..\docs\audit\phase2' ('semantic-recovery-' + (Get-Date -Format 'yyyyMMddTHHmmss') + '.xml')
.\.venv\Scripts\python.exe -m pytest -q tests/test_semantic_revision_phase2.py tests/test_reviewer_repair_phase2.py tests/test_reviewer_gateway_guard_phase2.py tests/test_file_revise_phase2.py tests/test_phase2_integration_security.py --junitxml=$semanticAudit
```

Ruff 与 `git diff --check` 通过。冻结源码 SHA256：`independent_review.py` 为 `2bcc601f6d3b6faa3bedb9bab36e661a78cb809807f66b19f49055da3ca68337`，`evidence.py` 为 `c38ad3012df4c1581a20728df89ed1aed4ad203599cebd6f6b8196a43f575075`。上述模型协议为明确的脚本 fixture，磁盘操作和数值子进程为真实本地执行，Provider 网络调用为 0；这些回归不建立真实模型任务成功率。

## Reviewer 来源包的无损表示与明确回合上限

主集成 L2 诊断 `20261115T010151Z-L2-07090a` 的 `research-sources` 在没有耗尽任务 token 时触发 `review_context_limit`。本子任务以 `mode=ro` 打开原数据库，使用 SQLite backup 复制到临时数据库，再构造 `_packet`；原运行、审批、回执和状态均未改动。该确切包为 56,755 UTF-8 字节，其中 receipts 为 33,225、delivery 为 9,872、user_constraints 为 8,024。三份原始源码、manifest 和目录回执均存在；JSON 文本嵌入 JSON 消息的重复转义、重复约束元数据以及重复 schema 将保守 reserve 推到 73,065，超过既有 65,536 context 上限。

新增 `_review_messages` 仅对大包改变传输表示：大段 `file_read` 内容以完整、标明不可信的来源块发送一次，metadata 用 receipt reference、原文本 SHA256 和 UTF-8 字节数精确绑定。约束的相同字段集中于 `shared_fields`；已包含于原始/更新用户消息的逐字 statement 用同一 message 来源和 Unicode `[start,end)` 范围引用，未逐字匹配的 statement 保留全文。每个原值均可精确重建，原 `_packet` 及 PASS 的 `packet_sha256` 绑定保持原值。具有原生结构化输出 schema 的调用不再于 system prompt 重复同一 schema；Gateway 原有 schema 格式指令和响应 schema 保留。没有截短交付、来源内容、证据或审查要求，原始包和 context 超界仍拒绝。

该确切历史快照的保守 review reserve 降为 **64,622**；通过实际 `ProviderGateway._payload` 仅在本地构造请求，完整三份原始 source 均在消息中，Gateway 的保守 reserve 为 **64,245**，低于同一个 65,536 上限。该观测只说明此 fixture 可准入，不保证更大终稿或更多来源能容纳，也没有重新运行或追认旧 L2。

`review_max_rounds_from_env()` 读取 `FORGE_REVIEW_MAX_ROUNDS`：默认 `2`，只接受字符串整数 `1`–`4`，其他配置明确拒绝；生产接线及新运行的显式 `3` 由主集成负责。达到既定上限现在记录 `review_round_limit`，不再混同于一般合同或预算错误。提高新运行的显式回合上限不修改旧运行，也不能绕过真实审查结论。

新 `backend/tests/test_reviewer_context_phase2.py` 的 **15 个案例**验证合成大包逐字无损重建、原 packet 不变、真实本地回执的审查绑定、配置范围和回合耗尽不发额外模型调用。大 payload 合成只用于表示测试，不作为 Core 回执或 live PASS。下列扩大组为 **118 passed，36.18 秒**，JUnit 记录为 [review-context-20261115T091955.xml](review-context-20261115T091955.xml)：

```powershell
# 在 backend 目录执行；审计文件名由本次运行时间生成
$reviewAudit = Join-Path '..\docs\audit\phase2' ('review-context-' + (Get-Date -Format 'yyyyMMddTHHmmss') + '.xml')
.\.venv\Scripts\python.exe -m pytest -q tests/test_reviewer_context_phase2.py tests/test_semantic_revision_phase2.py tests/test_reviewer_repair_phase2.py tests/test_reviewer_gateway_guard_phase2.py tests/test_file_revise_phase2.py tests/test_phase2_integration_security.py tests/test_worker_intelligence_phase2.py --junitxml=$reviewAudit
```

Ruff 和 `git diff --check` 通过。此轮最终冻结 `independent_review.py` SHA256 为 `bf32881dd80ca2080417740a9519d436f10a8c08e9b73d6a48d87b3a50ca7ba8`，前一节 hash 是恢复补丁完成时的历史快照；`evidence.py` 未再改动。Provider 网络调用仍为 0。

## Composer 显式默认 schema 与动态合同

主集成 L3 `20261115T012340Z-L3-17c603` 的批准 Planner 图有七个节点，但 Composer 五次 notice、十次 Gateway 调用全部结算后仍因 `Dynamic_role_requires_a_complete_contract` 停止，未产生团队提案。本子任务只读核对原数据库，确认 `team_count=0`；没有保存原模型 response 的表，因此不能恢复其具体 role id 或断言究竟哪个合同字段缺失，旧失败保持原状。

可重现的源码矛盾是：提示词说明 `output_schema_json=null` 选择已展示的 Core 八字段交付结构，但 `TeamWire.inputs()` 将其变成 `None`，随后动态 `RoleSpec` 以“缺少输出合同”拒绝。最小修复仅将 wire 中**显式提供**的 null 解释为对该固定 schema 的选择，并深拷贝 schema。动态角色仍必须由模型提供非空 responsibility、input_contract 和 acceptance_criteria；遗漏 schema 字段、空白字符串、字符串 `"null"`、空 schema 对象仍不能构成完整动态合同。直接构造动态 `RoleSpec` 而不提供输出 schema 也继续拒绝。没有生成职责、验收条件、工具权限或审批。

预设 id 仍严格为 `research / math / planner / architect / coding / design / experiment / reviewer`。`architecture / report / review` 不会被静默当作别名；它们需要显式 `dynamic=true` 和完整合同，或由模型改用准确预设 id。`task_node_key` 是独立字段，不要求与 role id 相同。主集成在 main 的提示词和输入合同中同步说明这些规则，本子任务未改 main。

新增 `backend/tests/test_composer_contract_phase2.py` 的 **21 个案例**核验显式选择、深拷贝、缺失合同拒绝、严格 schema required 字段、预设兼容性和精确 role id。真实本地 Composer pump 测试证明：完整 dynamic report/null 一次模型 fixture 响应即可持久化待审批提案，不启动 Worker；缺少 input_contract 的提案必须收到有界反馈，再由模型 fixture 提供第二个完整响应才能落库。

最终相关组为 **45 passed，5.89 秒**，JUnit 为 [composer-contract-20261115T093043.xml](composer-contract-20261115T093043.xml)：

```powershell
# 在 backend 目录执行；审计文件名由本次运行时间生成
$composerAudit = Join-Path '..\docs\audit\phase2' ('composer-contract-' + (Get-Date -Format 'yyyyMMddTHHmmss') + '.xml')
.\.venv\Scripts\python.exe -m pytest -q tests/test_composer_contract_phase2.py tests/test_composer_repair_phase2.py tests/test_team_composition_m5.py tests/test_team_intelligence_phase2.py --junitxml=$composerAudit
```

Ruff 与 `git diff --check` 通过。本次冻结 `team_composition.py` SHA256 为 `3b7b5265f51297516a10cabebf9752ad2b675329af36b6745b12213412b239d9`。脚本模型仅为离线协议 fixture，Provider 网络调用为 0，不据此宣称旧 L3 或新 live 组队成功。
