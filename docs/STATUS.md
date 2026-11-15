# FORGE 项目状态

更新：2026-11-15（Asia/Shanghai）

## 当前状态

**Milestone 7 实施中，尚未验收。** 用户已暂缓桌面验收，本轮聚焦后端加固；进展及待验项见[后端加固报告](PHASE1_MILESTONE7_BACKEND_HARDENING.md)。单一持久聊天、桌面 Core 生命周期、设置、审批与权限范围、凭据及备份恢复仍需最终集成验证。Windows 安装包、完整原生联调和有界 Alpha 场景须以最终冻结版本的实际结果为准；早期隔离启动和 Mock 浏览器测试不代表发布通过。总体进展见 [M7 报告草稿](PHASE1_MILESTONE7_REPORT.md)。

本轮后端加固已完成本机验证：冻结快照完整测试 **488 passed，1 warning，107.62 秒**；双工作目录 Ruff、格式及依赖检查通过，历史文件和原测试保留核验见[验证记录](audit/phase1/milestone7-backend-hardening-verification.json)。六角色审批、Reviewer 与重启的完整 Mock 链以及付费 Alpha 尚未完成。

Milestone 5 的 Native Worker、动态团队、工具治理与独立证据链已完成本机冻结验收：**282 passed，1 warning，44.99 秒**；真实供应商完成有限的 numeric AST 和 JSON 产物演示，范围见 [M5 报告](PHASE1_MILESTONE5_REPORT.md)。Milestone 6 的最终快照本机完整测试为 **372 passed，1 warning，77.78 秒**，静态、依赖与历史证据核验通过；一次有界真实供应商任务恢复成功，前五次诊断失败均保留。有限合成研究及容量边界见 [M6 报告](PHASE1_MILESTONE6_REPORT.md)。这些结果不能证明无限对话或通用任务正确性。

M6 已将 Router、planner、composer、benchmark 和 Worker 的模型请求接入统一 Gateway 准入。价格未知时使用 token-only，不推算费用。当前限制和质量门槛见 [QUALITY_GATES](QUALITY_GATES.md)，配置及恢复复核步骤见 [README](../README.md)。

## 历史阶段与证据

各阶段报告和审计记录保留当时的测试结果、限制和待办，其历史描述不代表当前状态：

- [Phase 0 审计](PHASE0_AUDIT.md)及[验证证据](audit/phase0/verification.json)。
- [Milestone 1](PHASE1_MILESTONE1_REPORT.md)、[Milestone 2](PHASE1_MILESTONE2_REPORT.md)、[Milestone 3](PHASE1_MILESTONE3_REPORT.md) 和 [Milestone 4](PHASE1_MILESTONE4_REPORT.md) 报告。
- [Milestone 5](PHASE1_MILESTONE5_REPORT.md) 和 [Milestone 6](PHASE1_MILESTONE6_REPORT.md) 报告及各自审计证据。

历史审计脚本可能覆盖历史证据，重跑前应使用独立输出位置。阶段路线及尚未验证的验收目标见 [ROADMAP](ROADMAP.md)。
