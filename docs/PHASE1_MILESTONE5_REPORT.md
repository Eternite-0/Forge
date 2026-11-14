# Phase 1 — Milestone 5：Native Worker、团队与可信工具执行

日期：2026-11-15（Asia/Shanghai）。冻结实现已完成本机验收：完整后端 **282 passed，1 warning，44.99 秒，退出 0**。最终 Ruff check、74 文件格式检查、pip check、git diff --check 均通过；锁定的 36 项 Python 依赖扫描未发现已知漏洞。实际命令、原始失败/通过日志与哈希见 [验证记录](audit/phase1/milestone5-verification.json)。

## 实现范围

Core 自有 Worker Agent Loop、结构化工具调用、SQLite checkpoint/租约 fencing、隔离 Worker 上下文、取消/恢复与有界预算接入批准的 Quest DAG。角色与模型分离：8 个预置角色和完整的动态角色合同，模型画像保留来源与未知项；single_model、greedy、coverage 和 complementarity 均先执行硬约束过滤，再解释候选选择。每个批准节点恰有一个成员合同；团队变更仍需用户批准。

Worker 求助通过持久事件交给 Router 仲裁。重试和换组有次数限制；换组只允许已结算的 blocked/failed/cancelled 旧 Worker，存在 prepared/executing/uncertain 工具动作时拒绝。新审批在同一事务安全结束旧分配并开放剩余节点，已完成节点保留历史归属。预算不退还；Quest 总上限来自批准计划，团队限额约束各次分配。历史证据只可读取可证明曾批准的 approved/superseded 团队，不能把该接口用于新执行。

固定工具合同包含文件、目录、诊断、Python 编译检查、纯数值 AST 子集、Git 状态、允许主机 HTTPS 与预配置 MCP；不提供任意 shell 注册或模型赋予权限。持久动作意图与精确审批原子绑定，可信回执记录实际执行结果；回放复用同一回执，中途失联的外部效果标记 uncertain 而非自动重做。独立验证器校验产物哈希、实际工具回执和批准合同，Mock 与模型自述不得使任务完成。

## 真实供应商证据

授权的 OpenAI-compatible 端点为 `http://127.0.0.1:8788/v1`，模型 `deepseek-v4.1-flash`。密钥只由实验进程环境提供，不落盘。协议探测的普通对话、forced tool、JSON schema 请求均返回 200；schema 仅本次响应符合，不能推广为普遍保证。GET models 自报 context_length=1000000、max_output_tokens=128000，来源是 provider_metadata，并未实测最大容量；专业能力分与价格保持 unknown。

真实 numeric AST Worker 演示经过 2 轮模型调用、1591 tokens、1 个实际工具回执，计算结果为 10，停在 review_pending。单独的 JSON 产物演示经过 2 轮调用、1640 tokens、1 个真实 file_write 回执和独立 JSON 解析验证，Quest 达到 completed；仅证明 JSON 语法合同，不证明领域结果正确性或通用自主任务成功。价格未知，运行使用 token-only 预算，费用保持 null。原始日志、SQLite 与产物保留在 `.local/phase1/milestone5/live-probe/`。

## 可复现研究实验

`backend/scripts/run_team_experiment.py` 使用 synthetic-team-v2、seed 42、3 个预先确定的场景，比较四个算法与 no_confidence/no_coordination 两个评分消融，共 18 次选择。输出逐场景覆盖强度、约束违规、估计费用、协调代理和算法耗时；拒绝无可行选择单列，违规率分母仅为已选可行团队。所有选择违规数为 0；single_model 与 greedy 各拒绝 1/3，coverage 与 complementarity 各选中 3/3。后两者合计覆盖强度 427 与 425，协调代理 2 与 1；并非所有指标同时占优。confidence 消融在相同覆盖指标下改变了置信场景的选择；no_coordination 提高合计覆盖但增加协调代理。价格与能力是合成输入，耗时仅选择算法本机耗时，不能宣称真实模型质量/费用/延迟收益。

`backend/scripts/run_governance_experiment.py` 在临时数据库中种入明确的 Mock Worker 合同，调用真实 ToolEngine：15 个固定案例，预期拒绝 11、允许 4，误放行 0/11、误拒绝 0/4；3 次执行请求包含 1 次回执回放，实际创建 2 个本地动作回执。覆盖权限缺失、精确参数替换、路径与敏感文件攻击、执行前撤销、并发审批单次消费和伪回执。R2 仅执行真实 intent preparation，未发送外网请求。相同标签集另计算 model_claim_only/no_binding/no_revocation_recheck 的 synthetic counterfactual 消融；不运行降级生产策略、不估计攻击总体发生率。最初仅 6 项 policy-only/0 tools 的实验保留为历史初稿，不能冒充最终工具矩阵。

## 验证记录与边界

最终完整 pytest、Ruff、依赖扫描、历史测试保留与报告哈希校验已写入验证记录。定向团队/换组回归首轮通过 16 项；后续扩展的历史合同、累计预算、三次重组上限及 completed 后重核共 9 项通过。`reassignment-first.txt` 与早期命名为 `reassignment-green.txt` 的日志实际失败，分别捕获节点 pending/proposed 不一致和以当次团队限额错误绑定 Quest ledger；真实通过日志是 `reassignment-green2.txt`，不覆盖失败证据。

冻结前独立全量检查为 9 failed、263 passed；其中旧 M4 Mock 响应未包含新增严格 wire 必需的 allowed_tools/cost_currency，现显式补空权限/未知币种，生产 wire 校验不降级。相关 21 项回归通过。通知重试夹具曾只等 claim 次数就人为清空下一次重试时间，可能自行 fence 尚在执行的租约；现等待本轮 notice_token 清空再推进，加入 80ms 确定性 claim/read 延迟，保留原有 calls==5 断言并通过。M4 基线的 153 个测试函数全部保留，508 条 assert 中 507 条 AST 不变，唯一变化是当前 schema 版本预期 6→7；214 个历史报告/证据文件在 M5 baseline 加 final-docs 覆盖基线上哈希一致。

全局预算当前覆盖全部 Worker，不含 Router、planner、composer 的模型调用；M6 需将统一 ModelCallBudgetLedger 接到 Gateway 请求准入，默认 token-only，货币限制仅使用可信 operator 价格。长期 Memory、完整 compaction、Recall 与连续性验收属于 M6；原生 Tauri Core 生命周期与桌面 Alpha 属于 M7。Rust 1.90.0 minimal 额外工具链安装退出 0，默认 stable 保持不变；这不构成原生构建通过。

最终前两轮完整回归均为 279 passed；之后主审发现 R2 pending 审批去重缺少 plan_revision/policy/risk，且并发同参数调用可能因多审批匹配而无法恢复。补充红测后，审批请求按 call/run 原子绑定，各调用保留独立审批；最终第三轮 282 passed 覆盖此修复及 completed 历史重核。最终源码冻结后未再修改应用逻辑。
