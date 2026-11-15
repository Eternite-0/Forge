# Phase 1 — Milestone 4：Quest Graph 与事件投递

日期：2026-11-15（Asia/Shanghai）。本机验证结果与实际命令以本阶段验证记录为准；真实 Provider、Worker 执行和外部副作用尚未验收。

冻结代码的完整后端测试为 **196 passed，1 warning，32.86 秒，退出 0**。首次 Ruff 检查发现两处导入排序，修正后 Ruff check 和 50 文件格式检查通过；排序修正后的启动、任务图和独立集成回归为 **34 passed，1 warning，退出 0**。pip check 和 git diff --check 均退出 0；Python 依赖锁与 M3 扫描时的版本及哈希一致。完整命令、输出、红测和哈希见 [验证记录](audit/phase1/milestone4-verification.json)。

## 实现范围

v6 迁移将 Quest 计划、节点与依赖，以及消费者、投递租约、Inbox、死信和重放记录持久化。任务图校验节点身份、预算、依赖归属和无环性；计划以摘要和修订绑定用户审批，控制操作通过版本 CAS 防止陈旧写入。暂停、恢复和取消改变计划与 Quest 状态，并不代表 Worker 已运行；失败 Quest 的重试创建需要重新批准的新 Quest。

Dispatcher 使用逐消费者持久队列、过期租约和令牌 fencing，handler 的数据库写入、Inbox 和确认同事务，失败使用有限重试和死信。重要事件可优先投递，游标不得越过较早未处理事件。应用 lifespan 启动独立投递和 Router 处理循环；没有 Provider 时保留待处理通知，Mock 模型处理后最多在 `main` Conversation 追加一条 assistant 消息，不能伪造 user 消息或绕过完成门槛。

用户创建 Quest 后，自动规划通过独立持久消费者处理；模型在数据库写事务外调用，规划结果、版本校验和源事件确认共同提交。失败、取消及重启保留 claim 和有界尝试状态。图规划使用严格的结构化 wire schema；本机通过 MockTransport 校验请求结构，并未验证真实供应商接收。Provider 模型请求不构成用户批准，也不执行工具。

独立集成覆盖双 Dispatcher 竞争、租约过期与重开后的 fencing、handler 失败回滚与死信、重要事件和较早游标缺口、慢模型不阻塞投递、通知失败退避与上限、实际 lifespan 启停和 Mock 自动规划。先写的 lifespan 红测曾因缺少后台投递而失败，修复后在完整测试中通过。早期慢模型与退避红测使用了后来不在通知白名单的 `worker.notice` 夹具，其日志保留但不单独作为最终事件类型的复现证明；最终测试改用支持的 `approval.requested`。自动规划首次独立测试已通过，不标记为红测。

M3 基线的 107 个测试函数全部保留；原有 342 条 `assert` 中 341 条 AST 未变，唯一变化是当前 schema 预期从 5 改为 6，并继续显式校验 v5 迁移。Phase 0 至 M3 的 155 个历史报告及证据文件哈希未变。

## 验证边界

本阶段的模型推理、Worker 事件和状态均为显式测试夹具或 Mock；没有真实 Provider 调用、真实 Worker 模型/工具闭环或任务完成演示。至少一次事件投递不保证外部副作用恰好一次；M5 接入执行前仍须完成用户授权绑定、持久执行意图、可信回执、幂等及恢复。长期 Memory、完整压缩和 Recall 保留给 M6；Tauri 原生 Core 生命周期和实际桌面验收保留给 M7。

设计取舍见 [ADR-006](adr/ADR-006-durable-event-delivery.md)。
