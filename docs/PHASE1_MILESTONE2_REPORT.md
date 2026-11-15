# Phase 1 — Milestone 2：Governance 与信任边界

日期：2026-11-14（Asia/Shanghai）。本机代码和自动化验证完成；实际桌面 Core 引导、真实 Worker 执行及其完成正路径仍未验收，详见下文。连续开发授权允许在阶段门槛审查通过后继续，不需要重新请求用户验收。

本次保护 Phase 0 与 Milestone 1 历史交付：写入前保存了源码、测试、配置及历史报告快照；报告和历史审计证据共 40 个文件的 SHA-256 与基线一致。生成的 egg-info 不作为不可变证据。结果和日志见 [验证汇总](audit/phase1/milestone2-verification.json) 和 [证据目录](audit/phase1/milestone2/)。

## 实现与验证

- Core 对业务接口实施进程会话认证、回环地址、Host 和 Origin 检查，请求 ID 与统一错误响应；CORS 不是写入授权。恶意网页的简单 POST 在旧代码返回 200；修复后，携带正确 bearer 的不可信 Origin、`null` 和伪造 localhost 来源均被拒绝，Worker、Event 和 Outbox 不变。健康检查的公开范围和凭据注入方式以 README 为准。
- v4 迁移引入审批、任务授权、证据、计划修订与执行记录等表；原 v1–v3 路径保留。策略对未知动作和风险默认拒绝；审批绑定任务、计划修订、动作、参数摘要、范围及策略，包含并发消费和回滚测试。此处的消费记录尚不是外部动作实际执行的证明。
- 通过普通状态接口不能直接批准、运行或完成 Quest。完成门槛在状态 CAS 的工作单元中核验条件；模型声明不构成可信证据。固定 JSON 校验器真实启动隔离子进程，记录实际命令、退出码与输出摘要；文件读取有大小限制，子进程不继承 Core secret。文件哈希只证明完整性，JSON review 只验证语法，不代表整个目标通过独立审查。当前没有可信模型 Worker 验证器，也没有可验证的审批与动作执行回执，相关条件缺失时关闭完成路径。
- 带 SHA-256 的 [Python 依赖锁](../backend/requirements-lock.txt) 在 Windows/Python 3.13 下生成，包含开发和构建依赖；全新隔离环境按锁安装、无依赖重解析安装项目及 `pip check` 均通过。锁的跨平台和其他 Python 版本未验证。

| 检查 | 结果 |
| --- | --- |
| 完整测试 | **87 passed，1 warning，9.91 秒，退出 0**；warning 为 Starlette TestClient/httpx 弃用提示 |
| Ruff | check 通过；format 检查 29 文件通过，均退出 0 |
| 依赖 | pip check 通过；pip-audit 对固定锁报告 No known vulnerabilities found，退出 0；这是当次数据库查询结果 |
| 干净安装 | 锁文件 `--require-hashes` 安装和 `--no-deps --no-build-isolation -e` 安装均退出 0 |
| 历史证据 | 40 个 Phase 0/M1 报告和证据文件哈希未变 |
| 前端 | 项目目录两次 `npm run build` 实际退出 0（4.947 秒、15.368 秒）；官方源 npm audit 报告 0 个漏洞；隔离故障注入分别返回 TypeScript 退出 2、Vite 退出 1 |

## 未完成及后续依赖

Tauri 尚未启动 Core、协商凭据或完成原生 WebView 端到端测试；现有本地 API 认证不能视为桌面集成完成。真实模型 Worker、可验证的 live 运行凭据、执行恢复以及审批消费与实际动作的原子绑定均未实现。`approval_executions` 表是预留基础；在可信回执不存在时，要求审批的完成门槛拒绝通过。任何外部副作用工具接入之前，必须实现并验证受控执行、持久意图、绑定、幂等与恢复。尚未实际验证真实模型调用、用户任务完成正路径或跨平台行为。

锁文件及本机扫描不覆盖 Node/Rust 依赖，也不保证不存在未知漏洞。Phase 0 已记录的 Tauri 原生编译阻断和开发依赖问题需在对应阶段复核。架构取舍见 [ADR-004](adr/ADR-004-governance-trust-boundary.md)。

补充复核：一次前端 `npm.cmd run build` 输出产物后被手动中断，不能认定为命令成功，原始输出见 [中断日志](audit/phase1/milestone2/frontend-build-interrupted.txt)；更早的一次没有保存退出码，同样不能计为通过。隔离重现中原版构建延迟约 46 秒自然退出 0；诊断观察到 Windows Vite 路径解析涉及 `net use` 子进程，不据此断言永久挂起。随后精确固定 Vite 6.4.6 等依赖，项目目录干净安装后两次构建实际退出 0；完整日志、结果和审计已归档。项目保留标准 Vite CLI，没有通过强制退出掩盖进程状态。
