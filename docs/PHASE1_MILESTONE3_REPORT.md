# Phase 1 — Milestone 3：Provider、Router 与模型画像

日期：2026-11-14（Asia/Shanghai）。本报告的验证范围以最终验证记录为准；真实供应商凭据、真实模型任务和自主 Worker 完成正路径尚未验证。

## Provider、Router 和迁移

原生 Gateway 提供 OpenAI-compatible、DeepSeek 和 Anthropic 协议转换，以及流式输出、工具调用、超时、重试、并发控制、错误映射、使用量和可配置价格计算。Mock HTTP 测试覆盖正常、失败和流截断等路径；没有使用真实供应商凭据。严格 JSON Schema 目前只在明确支持该能力的 OpenAI-compatible 适配路径开放；DeepSeek、Anthropic 的对应结构化 Schema 能力不能据此宣称已支持。未声明模型级能力的工具或 Schema 请求默认拒绝。

Router 根据结构化模型决策处理单一对话、目标解析与有限只读工具；网络调用在 SQLite 写事务外，决策与消息使用版本校验和事务提交。无配置、无效决策、歧义及状态冲突均有明确处理；审批响应不能自行授权。Context 使用有界快照，当前超过 100 个 Quest 时拒绝解析而非猜测，后续 M4 需扩展检索；长期 Memory、完整压缩及恢复仍属于 M6。模型调用在测试中通过 Mock 和 MockTransport，不证明真实网络或真实模型质量。

数据库 v5 在单一迁移事务中扩展 Router revision/capsule 和幂等请求记录，并添加基准和在线观测表；保留 v1–v4 升级与失败回滚测试。代码和测试文件清单、可重跑命令、实际退出码及各日志哈希见 [M3 验证记录](audit/phase1/milestone3-verification.json)。

| 最终检查 | 实际结果 |
| --- | --- |
| 完整后端测试（含真实本地 Core 启停/重启） | **140 passed，1 warning，13.97 秒，退出 0** |
| Ruff 检查及格式 | check 通过，40 文件格式检查通过，均退出 0 |
| 依赖检查 | pip check 通过；锁文件 pip-audit 报告 No known vulnerabilities found，均退出 0 |
| Git | `git diff --check` 退出 0；未跟踪文件由 Ruff 和文档/JSON 校验补充检查 |

新增 [Windows/Python 3.13 CI 配置](../.github/workflows/backend.yml)，尚未获得远端运行结果。`pyproject.toml` 是兼容范围声明，固定依赖安装使用 `requirements-lock.txt` 的 hash 检验步骤；新增 `httpx` 为运行时依赖。

## 模型画像与基准

模型画像保留 Provider 和模型标识、版本、端点、上下文、输出限制、价格、状态和可扩展属性。能力和性能测量保留来源与可用的样本量、时间、置信度；缺失信息保持 unknown。旧版 capability 数值作为明确的用户覆盖，样本数和测试时间不凭空补全。非法历史画像保留原文并隔离禁用，其他画像仍可读取；用户修正后可以重新保存。团队预览的硬能力要求遇到 unknown 或不足时不视为满足；模型级工具调用和结构化输出支持状态还受 Provider 适配器约束。

`forge-smoke-v1` 是三个固定任务及精确字符串评分器，覆盖算术、简单指令和原样复制。实际运行可用下面命令，传入新建的绝对路径作为隔离 Workspace：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts/run_mock_benchmark.py --workspace 'D:\Example\isolated-benchmark'
```

本机基线通过真实 runner 调用一个固定返回 `4` 的 Mock，得到 **1/3 匹配**；算术通过，另外两项失败。结果持久化，带 suite 版本、运行时间、提供者、model ID 与 mock 标记。该数据只验证任务分派、评分、记录和来源隔离，不是模型能力或算法有效性的实验证据，也没有覆盖十类专业能力，不能认定专业能力 Benchmark 已验收。[Mock 基线输出](audit/phase1/milestone3/mock-benchmark.json) 含实际结果与时间。后续能力评测和 M5 团队算法应使用独立测试集、更多样本、统计不确定度和可复现消融实验，不能把这个 smoke 集当作研究结论。

网关实际观测按 mock/live 隔离，保存成功、超时与错误及可用的延迟、首字时间、输出 token 数；汇总提供样本数和计算方法所需的来源。无数据的指标不填零。MockTransport 的数据仅验证协议，不作为真实供应商性能。

M2 基线的 63 个测试函数全部保留；原有 213 条 `assert` 中 212 条 AST 未变，唯一变化是迁移测试将当前 schema 版本预期从 4 改为 5，并单独保留 v4 的迁移结果断言。自动核对记录见 [测试保留检查](audit/phase1/milestone3/m2-tests-preservation.json)。

## 保留边界

M2 的受控工具执行与完成门槛仍适用。可信 Worker、持久动作意图与审批消费到执行回执的原子关联、幂等恢复、真实完成验证和桌面 Core 引导尚未实现。Task Capsule、长期记忆检索、上下文压缩与完整恢复仍未闭环；有限的状态字段和接口不能代替这些能力。不得仅凭 Provider 响应或模型声称完成绕过治理。锁文件在 Windows/Python 3.13 验证；CI 工作流是新增配置，远端运行状态不能由本机测试推断。详见 [ADR-005](adr/ADR-005-model-observation-provenance.md)。

M2 验证记录冻结后收到两条补充来源信息：核心 red `tests/test_governance.py tests/test_evidence_gate.py` 以 pytest `-q --tb=short --junitxml=..\.local\phase1\milestone2\core-before.xml` 在 backend 执行，退出 2；原 Store 绕过测试使用 pytest `tests/test_evidence_gate.py::test_initial_execution_state_and_direct_cas_are_blocked -o 'pythonpath=../.local/phase1/continuous-baseline/backend/src' -q --tb=short`，退出 1。两份历史日志已保留；本报告补充信息不改写 M2 验证记录。
