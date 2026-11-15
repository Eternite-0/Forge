# C 组只读审计：团队组合 / 模型画像 / Provider Gateway

- 审计对象：FORGE 仓库 `D:\Competition_this\Agent`，分支 `codex/phase1-continuous`（`git rev-parse` 已确认）
- 审计方式：只读阅读源码 + 只读测试执行（`pytest` 81 项全通过，见 §10）；未修改任何源码/文档，未发起任何 provider 调用
- 审计范围：`team_composition.py` / `model_intelligence.py` / `registry*.py` / `model_call_budget.py` / `model_budget_schema.py` / `provider_pool.py` / `providers/*` / `benchmark.py`，以及它们的生产接线（`forge_api/main.py`、`worker_runtime.py`、`router.py`、`evidence.py`）
- 不在范围：未跟踪目录 `backend/scripts/phase2/`（本轮开始前已存在于工作区、非本审计产物）未评估

## 0. 结论速览

| 编号 | 主题 | 分类 | 一句话 |
| --- | --- | --- | --- |
| C1 | 团队组合算法（4 策略 + 硬约束 + beam 搜索） | **A** | 真实实现，单测覆盖；但 4 策略的行为差异只被部分断言 |
| C2 | 评分函数（utility / coverage / coordination penalty） | **A（有缺口）** | 公式真实存在且确定；coordination penalty 仅 `complementarity` 生效且权重写死 |
| C3 | capability provenance 权重 | **部分（B 级证据）** | 有 confidence 权重，无 source 权重；生产写入口把 confidence 清成 `None`，权重实际失效 |
| C4 | 模型能力画像结构与来源 | **A/D（混合）** | 结构完整（value/source/n_samples/observed_at/confidence）；线上唯一入口是人工 `user_override` |
| C5 | 线上表现统计（gateway observations） | **B + D** | 采集与聚合已实现并接线；`summarize()` 无任何生产调用点，未回灌画像 |
| C6 | benchmark | **B + D** | 3 条 smoke 任务、MockTransport 验证；无 API/CLI 入口，不写能力分，live 分支无测试 |
| C7 | 模型与角色解耦 | **A** | 真解耦：RoleSpec 与 ModelProfile 独立，成员记录 role_id + provider_id + model_id |
| C8 | 动态重组/替换约束 | **A** | 3 次上限、必须 settled、succeeded 节点保留、不退款，均有测试 |
| C9 | Provider Gateway（协议/工具/流式/重试/错误/预算） | **A（仅 Mock 协议）** | 实现厚实，581 行测试全为 `httpx.MockTransport`；真实上游兼容性只有一次失败证据 |
| C10 | 运行时 model 能力 → Router/Worker 接线 | **A + 部分** | 接线真实；缺 provider pool 可达性前置校验，失败在调度期被静默吞掉 |
| C11 | 用户消息 → Worker 证据落库 全链路 | **E（当前不可端到端复现）** | 代码链路完整；真实跑通只留下 1 份 JSON 用例报告，需凭据 + 本地代理才能复现 |

分类口径（严格区分，未夸大）：**A** 真实实现且被测试覆盖；**B** 仅在 Mock 下验证；**C** 已通过真实 Provider 验证（附证据文件）；**D** 只有接口/数据模型/占位；**E** 目前无法端到端工作。

## 1. 团队组合算法：真实实现了哪几种策略

4 种策略是**枚举字面量**而非注释：`team_composition.py:198`（`TeamWire.strategy`）、`:346`、`:354` 都做白名单校验，未知策略直接报错。

| 策略 | 真实语义 | 证据 |
| --- | --- | --- |
| `single_model` | 在 beam 扩展时禁止换模型：`entry["profile_id"] != partial[0]["profile_id"]` 即跳过 | `team_composition.py:419-424` |
| `greedy` | 每层只保留 1 个最优部分解（beam 宽度 1） | `team_composition.py:438` |
| `coverage` | 目标函数换成"被覆盖能力的最优值之和"，utility 降为 1% 的 tie-breaker | `team_composition.py:394-405` |
| `complementarity` | utility + 覆盖分 ×0.25 − 协调惩罚 | `team_composition.py:406-411` |

搜索是**逐角色分层 beam**：`beam = [[]]`（`:414`），逐层扩展、按 `(-score, profile_id 元组)` 排序（`:435-437`），非 greedy 保留 64 宽（`:438`）。成本上限在扩展前剪枝（`:426-431`），无解即 `raise ValueError("No team satisfies the combined budget")`（`:433-434`）。

### 评分函数具体形式

**候选级 utility**（`team_composition.py:270-296`）：

```
fit   = Σ measurement.value[name] / max(1, len(required))        # :273-275
util  = fit
      + 10 * reliability          (无值则 0)                        # :283-285
      + min(speed,100)/20         (无值则 0)                        # :286
      + confidence_weight * mean(confidence of required caps)       # :288-294
      - min(cost_microunits / 1e6, 10)                              # :295-296
```

- `confidence_weight` 默认 **5.0**，`coordination_weight` 默认 **1.0**，均限 0..10（`:234-241`）；生产构造用默认值 `TeamComposerEngine()`（`:659`），**无法从配置覆盖**，只把权重原样回显到 `scoring_weights`（`:452-455`）。
- reliability 权重是 10 倍，一旦有值即压过 fit（fit 上限 100，实际常见 80）；speed 贡献最多 5 分（`:286`）；成本贡献最多 −10（`:296`）。
- **没有** latency_p50/p95、ttft、timeout_rate 进入 utility；`latency_p50_seconds`/`latency_p95_seconds`/`ttft_seconds` 在 `ModelProfile` 里存在（`domain.py:200-202`）但打分未用。

**团队级 score**（`team_composition.py:392-412`）：

```
utility = Σ member.utility                                    # :393
coverage: best[name] = max(member.capabilities[name]) for 必需能力  # :394-401
profile_handoffs  = max(0, 去重 profile_id 数 - 1)             # :402
provider_handoffs = max(0, 去重 provider_id 数 - 1)            # :403
coverage        -> Σ best + utility*0.01                       # :405
complementarity -> utility + Σ best*0.25
                   - coordination_weight*(profile_handoffs*5 + provider_handoffs*3)   # :407-411
其它(含 greedy/single_model) -> utility                         # :412
```

### coordination penalty 与 capability provenance 权重

- **coordination penalty：有，但只作用于 `complementarity`**（`:407-411`）。`greedy`/`coverage`/`single_model` 完全不含换模型/换供应商惩罚。`coverage` 甚至可能刻意挑"每个角色都不同模型"的组合，因为它只奖励能力覆盖。
- **capability provenance 权重：部分**。写入侧记录了来源与置信度，`capability_sources` 与 `performance_sources` 会落进成员对象（`:310-313`、`:332-335`），下游 Worker 还会把它转成 provider 的能力门禁来源（`worker_runtime.py:488-495`）。但**打分侧只乘 confidence，不按 source 加权**——`benchmark` 测出来的 80 分与用户手填的 80 分在 utility 中完全等价。
- 关键失真：生产唯一能力写入口 `POST /api/models` 会把所有 measurement 改成 `source="user_override"` 并清空 `confidence/n_samples/observed_at/benchmark_version`（`main.py:1270-1287`）。因此线上 `confidence` 恒为 `None`，`:288-294` 的置信度项**实际不生效**；测试里 `test_candidate_audit_excludes_irrelevant_capability_and_uses_known_confidence` 是直接改内存对象塞 `confidence=0.9`（`test_team_composition_m5.py:120`），这条路径在线上不可达。

### 硬约束（先剪枝后打分）

`_rejection`（`team_composition.py:244-268`）依次拒绝：禁用/隔离/缺身份（`:245`）、角色深度与工具越界（`:247`）、上下文/输出未知或不足（`:249-255`）、需要工具但工具能力未知（`:256-259`）、必需能力未知或低于阈值（`:260-265`）、有钱上限但价格未知或币种不符（`:266-267`）。**未知一律当不满足**，这是它最有价值的安全性设计，且有测试（`test_team_composition_m5.py:134-162`）。

提案层还有一层"不得弱化已批准计划"的校验：角色必须与计划节点一一对应（`:616-619`）、团队工具集必须等于成员工具集之并（`:621-622`）、验收标准不得少于节点 success_criteria（`:627-630`）、能力阈值不得低于节点要求（`:640-644`）、节点 token 预算不得超（`:645-649`）。均有测试（`test_team_composition_m5.py:317-365`）。

## 2. 模型能力画像：来源与结构

结构（`domain.py:137-144`）：`Measurement{value: 0..100, source: str, benchmark_version?, n_samples? >=1, observed_at?: datetime, confidence?: 0..1}`；性能侧 `PerformanceMeasurement` 同构但 value>=0（`domain.py:147-154`）。注意用户提到的 `sample_count`/`measured_at` 在代码中的实际字段名是 **`n_samples`/`observed_at`**，没有单独的时间戳字段。

三个并存的数据面，容易误判：

1. `capabilities: dict[str, float]` —— 遗留便捷字段，`model_validator` 会把它**提升为** `capability_measurements`，source 固定 `user_override`，且与已有 measurement 冲突时报错（`domain.py:229-244`）。测试：`test_model_registry_m3.py:12-21`。
2. `capability_measurements` —— 组合算法真正读取的面（`team_composition.py:260-265`、`:271`）。
3. `performance_measurements` —— 6 个键（tokens_per_second / ttft_seconds / latency_p50_seconds / latency_p95_seconds / reliability / timeout_rate），同样由同名标量提升为 `user_override`（`domain.py:245-267`）。

来源实际只有两种：
- **人工**：`POST /api/models`（`main.py:1270-1297`）、`PATCH /api/models/{id}`（`main.py:1234-1267`），source 一律被改写为 `user_override`。
- **隔离**：非法历史记录 `decode_profile` 会 quarantine 但保留原文、`enabled=False`（`model_intelligence.py:11-25`），组合时被 `:245` 拒绝。测试 `test_model_registry_m3.py:30-48`。

**线上表现统计：采集有、聚合有、回灌缺失。** `ObservationStore.record` 在每次网关调用后落 `model_observations`（`model_intelligence.py:52-73`，表结构 `registry_schema.py:9-17`），生产在 `main.py:135` 与 `main.py:176-178` 都接上了 observer；`summarize` 能算 reliability/timeout_rate/ttft/tokens_per_second/p50/p95 与逐项 n_samples（`model_intelligence.py:75-137`）。但 **`summarize(..)` 在整个 `backend/src` 中没有任何调用点**（grep 结果仅测试 `test_model_registry_m3.py:63`、`test_providers.py:627-630` 调用），没有任何代码把观测写回 `ModelProfile.performance_measurements`。因此"画像随线上表现自我更新"当前不存在。

benchmark 同样不闭环：`BenchmarkRunner.run`（`benchmark.py:21-96`）只写 `benchmark_runs` 表（`:74-86`），不更新 profile；任务集是 3 条 smoke（`:9-14`），文件头自述"scores are not capability estimates"（`:1`）；生产模块从未 import `BenchmarkRunner`（grep 仅命中自身）。live 分支的 provenance 断言（`:47-52`）没有测试覆盖。

## 3. 模型与角色是否真正解耦

**是，解耦是结构性而非约定性的。**

- 角色定义在 `RoleSpec`（`team_composition.py:15-58`）：只描述能力阈值、工具范围、token/步数/时长预算、合同（responsibility / input_contract / output_schema / acceptance_criteria），**不含 model 字段**。
- 模型定义在 `ModelProfile`（`domain.py:179-267`），与角色无引用关系。
- 绑定只发生在提案成员对象里，且三者并列保存：`role_id`、`profile_id`（registry 行 id）、`provider_id`+`model_id`（`team_composition.py:297-306`）。
- 落库后 `worker_runs` 同时存 `role_id`/`provider_id`/`model_id`/`member_index`（`worker_schema.py:13`、`worker_runtime.py:511-545`），证据链会**交叉校验三者一致**（`evidence.py:392-400`、`worker_runtime.py:124-129`）。

一个模型可服务多角色（`coverage`/`complementarity` 允许复用），也可强制单模型（`single_model`，`team_composition.py:419-424`）。角色也可以动态合成，但要求完整合同，否则 `RoleSpec` 直接拒绝（`:42-55`，测试 `test_team_composition_m5.py:60-96`）。

## 4. 动态重组/替换的真实约束

入口 `TeamCompositionService.propose_reassignment`（`team_composition.py:888-912`）只是给 `propose` 加上 `reassignment_run_id`/`reassignment_reason`，**不退款**（`:901` 注释与 `test_team_reassignment_m5.py:211-257` 的配额断言一致）。

真实约束（全部在写入事务内执行，`_reassignment_target` `team_composition.py:857-886`）：

1. **次数上限 3**：按 `events` 中 `team.reassigned` 计数，`>= 3` 直接 `DomainError("Quest reassignment limit reached")`（`:858-863`）。测试：`test_team_reassignment_m5.py:222-269`（第 4 次被拒）。
2. **旧 run 必须已结算**：state ∈ {blocked, failed, cancelled}（`:867-868`）。
3. **动作必须结算**：存在 `action_intents` state ∈ {prepared,executing,uncertain} 或 `worker_tool_calls` ∈ {running,uncertain} 即拒绝（`:869-879`）。测试 `test_reassignment_rejects_unsettled_actions_without_mutating_old_team`（`test_team_reassignment_m5.py:128-154`）。
4. **已完成节点保留**：目标节点 `status == 'succeeded'` 或 `worker_id` 不是该 run 的 worker 就拒绝（`:880-885`）；批准替换时只把**非 succeeded** 的节点复位：`UPDATE task_nodes ... WHERE ... AND status<>'succeeded'`（`:813-816`）。测试断言已完成节点 `status=='succeeded'` 且 `worker_id` 不变（`test_team_reassignment_m5.py:96-99`）。
5. **不允许并行团队**：存在 `team_digest<>新提案` 的未结算 run 时拒绝批准/撤销（`:828-834`、`:925-932`）。测试 `test_team_composition_m5.py:368-445`。
6. **历史批准只读**：`get_historical_approved`（`:950-995`）允许 superseded 但要求 `approved_by=='local_user'`、run 晚于批准时间、早于后继批准、且跳过 profile 哈希校验；不能用于新执行。测试 `test_team_reassignment_m5.py:157-208`。

**限制（真实缺口）**：重组提案仍要求角色覆盖**全部**计划节点（`team_composition.py:616-619`），因此"只替换一个失败角色"必须重新提交整队角色定义，无法表达"其余成员原样复用"。这让重组开销随团队规模线性增长，也让 `single_model` 等策略在重组时可能连坐换掉健康成员。

## 5. Provider Gateway

适配器种类：`openai_compatible` / `deepseek` / `anthropic`（白名单 `providers/config.py:30`）。凭证只存引用名，必须匹配 `FORGE_PROVIDER_KEY_[A-Z0-9_]+`（`config.py:51`），值在请求时从进程环境解析（`config.py:119-120`、`providers/__init__.py:70-87`）；base_url 必须 HTTPS，http 只允许显式 `allow_insecure_loopback` + 数值回环（`config.py:32-48`，测试 `test_providers.py:570-594`）。

| 维度 | 实现情况 | 证据 |
| --- | --- | --- |
| 请求构造 | `_payload` 逐字段校验后拼 payload；路径按 kind 分叉 `/messages` vs `/chat/completions` | `providers/__init__.py:187-340`、`:662-670`、`:748-756` |
| tool_calls（请求侧） | OpenAI 形状 `{type:function,function:{name,description,parameters}}`；Anthropic 转 `input_schema`；`tool_choice` 支持 auto/required→any | `:271-300`、`:301-309` |
| tool_calls（响应侧） | 解析 `id/name/arguments`，arguments 必须 JSON 成 dict；重复 id 或 finish 不一致判 `protocol_error` | `:485-492`、`:511-531` |
| response_format | 仅 `openai_compatible` + `supports_json_schema` + 模型能力 `json_schema` 为真/操作员覆盖时才允许；同时把 schema 作为 system 指令插入 | `:241-262`、`:310-324`、测试 `test_providers.py:551-567` |
| Anthropic 结构化输出 | **不支持，fail closed**（schema 分支要求 openai_compatible） | `:257-260`、测试 `test_providers.py:128-129` |
| 流式 | `stream()` 增量聚合 SSE：文本增量、tool_call 分片参数按 index 合并、`message_start/…/message_stop` 与 `[DONE]` 双协议 | `:724-903`、SSE 行解析 `:398-418` |
| 重试 | `max_retries` 0..3；仅 429/502/503/504 重试，429 用 `Retry-After`（秒或 HTTP-date）并广播冷却 | `:533-637`、`:611-630` |
| 错误映射（脱敏） | `auth`(401/403) / `rate_limit`(429) / `provider_error` / `timeout` / `network` / `truncated` / `refused` / `protocol_error` / `configuration` / `unsupported` / `context_exceeded` / `budget_exhausted` / `concurrency_unavailable` / `attempt_unknown` | `:631-637`、`:511-515`、`:338-339`、`:556-588` |
| 预算准入 | **每次 POST 一次 admit**，service 层再来一次 reject/settle；无 ledger 时直接 `budget_unconfigured` 且不发送 | `:538-598`、`:676`、`:690`、`:883`、`:898`；测试 `test_model_call_budget.py:146-165` |
| 响应体上限 | JSON/SSE/单行/总字节均 8MB 或 1MB 上限，超限 `protocol_error` | `:386-418` |
| 观测 | `_record` 记 health 并回调 observer（异常被吞，不影响结果） | `:100-128` |
| Mock | `MockProvider` 明确标注非真实响应 | `:906-921` |

预算账本本体（`model_call_budget.py`）质量较高：单文件 SQLite `BEGIN IMMEDIATE` 串行化（`:168-169`、`:455`），四个作用域（workspace/quest/worker/team）交叉校验（`:194-213`、`:274-395`），超用即 `state='overrun'` 并 fence 整个 ledger（`:468-483`），未结算的 reservation 靠 `recover_after` 兜底（`:441-445`）。测试覆盖了跨进程竞争（`test_model_call_budget.py:63-79`）、重启后仍占额（`:82-98`）、超额 fence（`:121-143`）、429 冷却共享（`:280-338`）、早停流保留未知 reservation（`:341-374`）、金额需 operator price bound + 对账（`:377-407`）。

## 6. 运行时如何把 model 能力与 provider 配置接到 Router/Worker

- **单一 Router provider**：`FORGE_PROVIDER_*` 四件套齐备才构造 `ProviderGateway`，否则 `ProviderGateway.unconfigured()`（`main.py:126-140`）；Router 模型来自 `FORGE_ROUTER_MODEL_ID` + `FORGE_ROUTER_CONTEXT_LIMIT`（`main.py:141-158`）。
- **Router 能力门禁**：非 mock 时必须能从 registry 取到该 provider 的 profile，且 `tool_calling_support.supported` 与 `structured_output_support.supported` 皆真，否则 `DomainError`（`router.py:583-599`）；随后把 `{"tools": True, "json_schema": True}` 与来源一起塞进 `ModelRequest`（`router.py:662-680`）。
- **Worker provider 池**：`FORGE_PROVIDERS_JSON` 多 provider 定义 + `FORGE_PRICE_BOUNDS_JSON` 价格上限，构造 `ProviderPool`（`provider_pool.py:56-88`），在 lifespan 里并入 Router 的 provider（`main.py:176-184`），再交给 `WorkerManager`（`main.py:182-184`）。
- **Worker 绑定**：`start` 从已批准团队取 `member`，校验 `task_node_key`/`depth` 一致（`worker_runtime.py:359-361`），把 `member["provider_id"]`/`member["model_id"]` 写进 run（`:511-545`），并把 `member["capability_sources"]` + 工具协议来源写成 `capability_sources_json`（`:488-495`）。运行时 `providers.get(row["provider_id"])`（`:1471`）并要求 `execution_mode` 与建 run 时一致（`:1475-1477`）。
- **Worker 请求**：`ModelRequest(model_id, provider_id, messages, tools, max_output_tokens, context_limit, timeout, max_retries=0, model_capabilities={"tools": True} if definitions else None, model_capability_sources=…)`（`worker_runtime.py:1478-1498`）。
- **缺口 1**：Worker 不传 `response_format`/`response_schema`，输出合同只靠 system 提示词里的 schema 文本 + 本地 `jsonschema.validate`（`worker_runtime.py:614-643`、`:1677-1683`）。对有结构化输出能力的上游是一处未用上的硬约束。
- **缺口 2**：组合/批准阶段**不校验** `member.provider_id` 是否真的在 `ProviderPool` 里。池里没有该 provider 时，失败推迟到 `WorkerScheduler.tick`，被 `except (DomainError, KeyError, ValueError, TypeError)` 静默计成 `last_failure_code="assignment_not_ready"`（`worker_runtime.py:1886-1897`），用户侧只看到"团队批了但不动"。
- **缺口 3**：生产路径全走 `generate()`（`router.py`、`worker_runtime.py:1553`），`stream()` 没有 API/UI 消费者。

## 7. 当前执行链路图（文字版，用户消息 → Worker 证据落库）

```
[1] POST /api/conversation/message                    main.py:1020
      -> SessionGuard
      -> RouterService.handle_user_message            router.py:546
           _snapshot / _context                       router.py:96 / :175
           provider.generate(ModelRequest(...))       router.py:662-680
           _commit -> router_turns + events           router.py:821
[2] Dispatcher.tick -> claim -> deliver               dispatcher.py:441 / :163 / :238
      -> RouterService.handle_pending_events          router.py:328
      -> _handle_notice                               router.py:399
      -> 事件 quest.plan_approved 进入 consumer_id='composer' 的 inbox
[3] process_pending_plan                              main.py:258
      -> QuestGraphService.generate / propose         quest_graph.py:426 / :64
      -> 用户 POST /api/quests/{id}/plan/approve       main.py:1113
      -> QuestGraphService.approve -> quest.plan_approved   quest_graph.py:159
[4] process_pending_team (composer pump)              main.py:346
      router._model_profile                           main.py:421 -> router.py:295
      ModelRequest(purpose="team_composer",
                   response_format=TeamWire schema)   main.py:479-503
      -> TeamWire.inputs()                            team_composition.py:200
      -> TeamCompositionService.propose               team_composition.py:511
           _current_plan 校验 plan/digest/状态         :486-509
           角色 vs 节点/工具/验收/能力 防弱化          :616-649
           TeamComposerEngine.compose                 :340  (硬约束 :244-268 / 打分 :270-296 / beam :414-438)
      -> INSERT team_proposals + 事件 team.proposed   :696-722
[5] 用户 POST /api/quests/{id}/teams/approve           main.py:1156
      -> TeamCompositionService.approve               team_composition.py:781
           （重组时）取消旧 run、非 succeeded 节点复位   :801-827
      -> 事件 team.approved                           :843
[6] WorkerScheduler.tick 读取已批准团队                worker_runtime.py:1802 / :1851-1862
      -> WorkerManager.start                          worker_runtime.py:309
           _team -> TeamCompositionService.get_approved  :267 -> team_composition.py:942
           预算 _reserve(global/quest/team)          worker_runtime.py:292 / :450-476
           INSERT worker_runs(provider_id, model_id,
                              execution_mode, capability_sources_json)   :511-545
      -> 事件 worker.proposed                         :657-668
[7] WorkerScheduler.tick -> asyncio.create_task(manager.run)   worker_runtime.py:1912-1916
      -> WorkerManager.run -> _claim                  worker_runtime.py:842
      -> token/cost reserve                           worker_runtime.py:1405-1467
      -> providers.get(row["provider_id"])            worker_runtime.py:1471
      -> provider.generate(ModelRequest(...))         worker_runtime.py:1478-1554
             ProviderGateway.generate                providers/__init__.py:639
             _payload / _send / 预算 admit+settle     :187 / :533 / model_call_budget.py:145,457
[8] 工具轮：response.tool_calls -> worker_tool_calls('pending')   worker_runtime.py:1744-1751
      -> ToolEngine（intent / approval / execute）     tool_engine.py
      -> action_receipts + evidence(kind='tool')      tool_engine.py:952
      -> 工具结果回填 worker_messages(role='tool')
[9] 终局：无 tool_calls
      jsonschema.validate(final_value, capsule["output_schema"])   worker_runtime.py:1680
      -> run='review_pending'，事件 worker.result（payload.claim）  :1756-1782
[10] NodeVerifier.tick -> verify                       evidence.py:607 / :709
      -> CompletionGate._check_live_node               evidence.py:370
           TeamCompositionService.get_historical_approved  team_composition.py:950
           member 与 run 的 role/provider/model 交叉校验   evidence.py:392-400
           receipt/approval/provenance 校验              evidence.py:438-493
      -> JsonVerifier.verify: 独立进程 + evidence(artifact/test/review)
                                                      evidence.py:93-168
      -> CompletionGate._settle_conditions             evidence.py:663
      -> task_nodes.status='succeeded' -> quest 完成
```

要点：**证据落库的最后一道门要求 `execution_mode='live'`**（`evidence.py:376`、`:544`，以及 `evidence.py:351`、`:434` 对 receipt/intent 的同款要求）。因此任何 Mock provider 跑出来的团队都**不可能**完成节点——Mock 只能验证"提案/授权/预算/协议"，不能验证"完成"。

## 8. 分类明细（A/B/C/D/E）

### A. 真实实现且被测试覆盖

| 能力 | 测试文件:行 | 断言强度 |
| --- | --- | --- |
| 组合硬约束 + 预算剪枝 + 4 策略均不越界 | `test_team_composition_m5.py:201-225` | 中：只断言成员数/总 token/阈值，**未断言 4 策略选出的团队不同**；只有 `single_model` 有专属断言（:224-225） |
| 未知能力/未知价格/币种不符会失败 | `:134-162` | 强：三类 `pytest.raises` |
| 候选审计含拒绝原因与置信度传递 | `:114-131` | 中：断言 `rejected[0].reason` 与 `capability_confidence==0.9`，但该 confidence 由测试直接注入，线上不可达（见 §1） |
| 团队批准绑定 plan+profile 哈希、可撤销、失效后陈旧 | `:228-314` | 强：改 `enabled=False` 后 `get_approved` 抛 "stale" |
| 不得扩权/弱化验收 | `:317-365` | 强 |
| 未结算 Worker 阻止换队/撤销 | `:368-445` | 强 |
| 重组：3 次上限、不退款、保留已完成、历史批准只读 | `test_team_reassignment_m5.py:69-289` | 强（含 5 个 mutation 参数化） |
| Provider 协议（tools/usage/cost/流式/重试/错误/脱敏/上限/loopback） | `test_providers.py:64-693`（19 项） | 强，但**全为 MockTransport**（文件首行自述 `test_providers.py:1`） |
| 预算账本（跨进程、重启、超额 fence、冷却、早停） | `test_model_call_budget.py:17-407`（9 项） | 强 |
| 画像解码/隔离/冲突拒绝 | `test_model_registry_m3.py:12-88` | 强 |
| 观测量统计（mock/live 隔离、百分位、NaN 拒绝） | `test_model_registry_m3.py:51-71`、`test_providers.py:597-630` | 强（但只验证"能算"，不验证"被使用"） |
| API 生命周期自动生成未批准团队 | `test_team_lifespan_m5.py:14-140` | 中：用假 provider 返回固定 TeamWire；断言"有 1 个 proposed 团队 + 未启动 Worker + 无 token 401"，不断言 Worker 执行 |

### B. 仅在 Mock 下验证

- Composer pump 全链路（`test_team_lifespan_m5.py:97-131`：`MockProvider.generate` 返回预置 JSON）。
- M5/M7 生命周期测试中的团队段（`test_m5_lifespan_execution.py:113-128`、`:411-426`）。
- benchmark（`test_benchmark_m3.py:9-81`：3 项全部 mock；`test_benchmark_admits_each_call_through_gateway` 用 MockTransport 但仍标 `mode="mock"`）。
- 发现（discovery）：`test_model_registry_m3.py:74-88` 用桩 gateway。
- 观测采集：`test_providers.py:597-630` 全部 MockTransport（mode 被正确标成 `mock`，这点是对的）。

### C. 已通过真实 Provider 验证（证据文件）

| 证据 | 内容 | 局限 |
| --- | --- | --- |
| `docs/audit/phase1/milestone5/live-probe/completion-20261114T171357Z-587cd661.json` | 真实模型 `deepseek-v4.1-flash` 经本地 `http://127.0.0.1:8788/v1` 跑通 **file_write → receipt → evidence(tool/artifact/test/review) → execution_runs → task succeeded → quest completed**；`:77-79` | 唯一成功端到端用例；判据只有 `json_valid:` 一条（`:91` 脚本侧），不证明任何领域正确性；`cost: null`（`:8`，仅 token 预算） |
| `docs/audit/phase1/milestone5/live-probe/worker-20261114T170953Z-14c91e82.json` | 真实 provider 下 Worker 工具循环被观察到；`schema_note` 明示"最终 JSON 仅本地校验，不声称供应商侧强制 schema"（`:9`） | 同上，单模型单端点 |
| `docs/audit/phase1/provider-compatibility-20261115.json` | 第五次有界 live context probe **失败**：最终决策缺必填字段/含禁用字段（`:21-29`），并注明上游转发不保证严格输出格式（`:20`） | 是**负面**证据：真实上游对 strict schema 的遵循不可靠 |
| `docs/audit/phase1/provider-diagnostic-20261115.json` | 本地代理侧统计：forge 非流式 7 次完成、历史 429/502 记录（`:8-10`） | 未包含任何 FORGE 侧判定 |

复现前提：`FORGE_PROVIDER_KEY_LIVE_TEST`（`live_completion_probe.py:32-34`）+ 本地 `127.0.0.1:8788` 代理 + `allow_insecure_loopback=True`（`:166-176`）。当前环境无该凭据，**无法重跑**。另注意该 probe **绕过了 HTTP API 与 composer LLM**：手工 `QuestGraphService.propose/approve` + `TeamCompositionService.propose(strategy="greedy")`（`:92-164`），因此"Router 自动组队"这一段从未在真实 provider 下被验证。

### D. 只有接口/数据模型/占位

- `registry.TeamComposer`（`registry.py:106-128`）：自述"complementarity 优化留待后续里程碑"，唯一消费点是 `GET /api/team/preview`（`main.py:1318-1320`），不经计划、不经审批、不落库。`ModelRegistry.candidates`（`registry.py:85-103`）的 fit 公式（`value*threshold/100` 求均值）与 `TeamComposerEngine` 的 fit（`value` 均值）不同，两套打分并存。
- `DEFAULT_ROLES`（`registry.py:6-37`）用了 `math`/`reasoning`/`tool_use`，而 `CAPABILITY_NAMES`（`domain.py:165-176`）是 `mathematics`/`tool_calling`、且**没有** `reasoning`。名字对不上，preview 对多数真实画像是空候选。
- `ModelRegistry.discover`（`registry.py:51-83`）：生产无调用点（`ProviderGateway.discover` 存在，`providers/__init__.py:697-722`）。
- `BenchmarkRunner`：无 API/CLI 入口，生产无引用。
- `ObservationStore.summarize`：无调用点（见 §2）。

### E. 目前无法端到端工作

1. **带真实 provider 的完整链路不可复现**：需要外部凭据 + 本地代理；仓库内只有 1 份一次性的 JSON 用例报告（C 节）。默认配置下 `ProviderGateway.unconfigured()`（`main.py:132-140`）使 Router/planner/composer 全部静默禁用（`main.py:216-224` 以 `bool(router.model_id)` 为开关）。
2. **团队批准后可能永久不动**：provider 不在 pool 时无前置校验（§6 缺口 2），只在调度期静默跳过。
3. **Mock 团队永远无法完成节点**：证据门要求 `execution_mode='live'`（`evidence.py:376`、`:544`），因此"演示账号/离线模式"下流程止步于 `review_pending`。
4. **重组无法只替换单成员**（§4 限制），失败恢复在 3 次后彻底锁死（`team_composition.py:858-863`）。

## 9. 与 Phase 2 要求的差距

对照口径：用户给出的能力清单（团队组合策略 / 能力画像 / 模型-角色解耦 / 动态重组 / Provider Gateway / 运行时接线）为逐条基准；同时参照 `docs/ROADMAP.md:40-64`（原始 Phase 3 Model Registry 与 Phase 5 Dynamic Team Composer 条目）。注意 ROADMAP 自身的"Phase 2"指 Quest Graph，与本清单不同编号。

| # | 能力要求 | 状态 | 证据 | 最小必要改动（不重写） |
| --- | --- | --- | --- | --- |
| 1 | 四种组合策略可执行 | **已有** | `team_composition.py:340-466`；`test_team_composition_m5.py:201-225` | 仅需补一组"两策略选出不同团队"的断言（约 40 行测试），否则回归时策略退化为同义无人发现 |
| 2 | 可用性/能力硬约束（未知即拒） | **已有** | `team_composition.py:244-268` | 无 |
| 3 | 评分函数可解释、含成本项 | **已有** | `:270-296`、`:392-412` | 无（但见 5、6） |
| 4 | coordination penalty | **部分** | 仅 `complementarity`（`:407-411`），权重写死 1.0（`:234-241`、`:659`） | 把两种权重提到 `TeamCompositionService` 构造参数/环境变量，并把 penalty 也接入 `coverage`（各 2~5 行）；补 1 个 penalty 改变结果的测试 |
| 5 | capability provenance 加权 | **部分** | `capability_sources` 有记录（`:310-313`），打分只乘 confidence（`:288-294`） | 在 `_candidate` 里按 `measurement.source` 给一个系数（如 benchmark 1.0 / gateway 0.9 / user_override 0.7），落进 `scoring_weights` 以便审计；约 15 行 |
| 6 | confidence 在生产可用 | **缺失** | 写入口清空 `confidence`（`main.py:1281`），线上恒 `None` | 允许"由受信内部服务写入"的旁路（例如 Core 内部函数直接 `store.save_model`，不经 `POST /api/models`），或新增只读推导：由 `summarize` 产出的 n_samples 计算 confidence；约 20 行 |
| 7 | 能力画像结构与来源可审计 | **已有** | `domain.py:137-154`、`:229-267`、`model_intelligence.py:11-25` | 若要贴合规格字段名，可加只读别名属性 `sample_count`/`measured_at`（property，不迁移）；约 6 行 |
| 8 | 线上表现统计并回灌画像 | **部分** | 采集/聚合齐备（`model_intelligence.py:52-137`、接线 `main.py:135`/`:177`），无调用点 | 增加一个 Core 内部任务：对 `model_observations` 按 (model,provider,mode=live) 聚合后写入 `performance_measurements`（source=`gateway_observation`，带 n_samples/observed_at）；约 30 行 + 1 处测试 |
| 9 | benchmark 定义/版本/运行/provenance | **部分** | `benchmark.py:9-14`（3 条 smoke）、`:21-31`（provenance 校验）、`:74-86`（落库）；无入口、不写能力 | 加一个受鉴权的 `POST /api/models/{id}/benchmark`（内部只有 10 行转发）+ 落库后**不**自动改能力，只作为 8 的输入；同时为 `mode="live"` 分支补一条 MockTransport 用例（当前 `:47-52` 无覆盖） |
| 10 | 模型与角色解耦 | **已有** | `team_composition.py:15-58` vs `domain.py:179-267`；`worker_schema.py:13`；交叉校验 `evidence.py:392-400` | 无 |
| 11 | 动态重组次数上限 | **已有** | `team_composition.py:857-863`；测试 `test_team_reassignment_m5.py:222-269` | 无（上限值建议配置化，1 行） |
| 12 | 已完成节点保留 | **已有** | `:813-816`、`:880-885`；测试 `test_team_reassignment_m5.py:96-99` | 无 |
| 13 | 只替换受影响成员（增量重组） | **缺失** | 角色必须覆盖全部节点：`team_composition.py:616-619` | 放宽为"角色覆盖节点的**未完成子集**"，并在 `start` 侧继续用现有 `task_nodes.worker_id IS NULL AND status='proposed'` 守卫（`worker_runtime.py:482`）自动跳过已完成节点；约 15 行校验 + 1 条测试 |
| 14 | 适配器种类（OpenAI 兼容 / DeepSeek / Anthropic） | **已有** | `providers/config.py:30`、`providers/__init__.py:455-492`、`:775-846` | 无 |
| 15 | tool_calls 全链路 | **已有** | 请求 `:271-309`；响应 `:485-531`；流式分片 `:833-846`；单测 `test_providers.py:64-105`、`:227-244`、`:466-506` | 无 |
| 16 | response_format / strict json_schema | **部分** | `:241-262`、`:310-324`；Anthropic 不支持（`:257-260`）；**Worker 路径根本不传**（`worker_runtime.py:1478-1498`） | Worker 在 `member["tool_calling_support"]`… 实为模型 json_schema 能力已知时传 `response_schema=member["output_schema"]`，仍保留 `:1680` 的本地校验兜底；约 12 行 |
| 17 | 流式可用 | **部分** | `stream()` 实现完整（`:724-903`），但生产全走 `generate()` | 若要接 UI：加一个 SSE 端点转发 `StreamEvent`；约 30 行（本轮不建议动，先确保 16） |
| 18 | 重试与退避 | **已有** | `:533-637`（仅 429/5xx；Worker 强制 `max_retries=0`，`worker_runtime.py:1495`） | 无（Worker 不重试是刻意的预算保守） |
| 19 | 错误映射与脱敏 | **已有** | `:631-637`、`:511-515`；测试 `:132-156`、`:535-548`、`:666-678` | 无 |
| 20 | 预算准入（每次尝试） | **已有** | `providers/__init__.py:538-598`、`model_call_budget.py:145-455` | 无 |
| 21 | Provider 可达性在批准前可知 | **缺失** | 组合阶段不查 pool；失败静默 `worker_runtime.py:1886-1897` | 在 `approve` 事务里加一条：所有成员的 `provider_id` 必须能被 pool/工厂解析，否则 409 并给出 provider_id 列表；约 10 行 + 1 条测试 |
| 22 | 真实 provider 端到端证据 | **部分** | 仅 `docs/audit/phase1/milestone5/live-probe/completion-*.json`（1 例、json_valid 判据） | 复用 `backend/tests/live_completion_probe.py`，把 Router 自动组队这一段也纳入（agent 侧跑，不要进 CI）；约 40 行 |
| 23 | Router 自动组队在真实 provider 下验证 | **缺失** | 唯一 live probe 绕过 API/composer（`live_completion_probe.py:92-164`）；composer 只有 mock 证据（`test_team_lifespan_m5.py:97-131`） | 用现有 `POST /api/quests/{id}/plan/approve` 驱动 composer pump 跑一次 live（环境变量已有，无需改代码），把报告落到 `docs/audit/phase2/` |
| 24 | 两套注册表打分统一 | **部分** | `registry.py:85-128` 与 `team_composition.py:270-296` 并存且公式不同 | `/api/team/preview` 改调 `TeamComposerEngine().compose(...)`（约 10 行），让 preview 与真实组队同源 |

## 10. 复现命令与结果

```
# 只读收集（81 项）
backend\.venv\Scripts\python.exe -m pytest --collect-only -q \
  tests/test_team_composition_m5.py tests/test_model_registry_m3.py tests/test_providers.py \
  tests/test_model_call_budget.py tests/test_benchmark_m3.py \
  tests/test_team_reassignment_m5.py tests/test_team_lifespan_m5.py
# -> 81 tests collected

# 实际执行（全部离线，MockTransport / 假 provider，无 provider 依赖）
# -> 81 passed, 1 warning in 8.76s
```

上述 7 个文件共 81 项测试全部通过；执行前后 `git status --porcelain` 除审计前已存在的未跟踪 `backend/scripts/phase2/` 外无变化，未修改任何源码或既有文档。

## 11. 审计结论（一句话）

团队组合的**约束层**（硬性能力/预算/越权护栏、审批-计划-画像三重哈希绑定、重组守卫）与**预算账本**是这批代码里最扎实的部分（A 级）；真正落后的是**反馈闭环**——线上观测与 benchmark 已经"能采能算"，却没有任何代码把它们变成画像分数，而唯一的能力写入口又把 provenance 一律降级成 `user_override` 并清空 confidence，使 `confidence_weight` 在线上成为死代码（D/B 级）；Provider Gateway 的协议实现厚实但**只有 Mock 证据**，真实上游对 strict schema 的遵循已被记录为失败（C 级中的负面样本），端到端链路因此处于"代码完整、默认不可运行、仅一次一次性报告可证"的 E 级状态。
