# GitHub 交付与继续开发

2026-11-16，用户要求将当前后端与“根据 Prompt 推进前端开发”窗口的全部前端工作
一起提交到 GitHub。本交付使用仓库原有分支 `codex/phase1-continuous`。
无需迁移压缩包；后端交接文档中“尚未提交”的段落记录的是此前交接时点。

```powershell
git clone --branch codex/phase1-continuous https://github.com/Eternite-0/Forge.git
cd Forge
```

已有克隆应先保留自己的未提交工作，再切换或更新该分支，不要覆盖本地修改。
提交包含后端、前端、测试、文档和仓库明确版本化的验收截图。
虚拟环境、node_modules、dist、数据库、凭据和 `.local` 调试目录继续由 `.gitignore` 排除。

## 已验证结果

- 当前后端 Thread 完整读取／真实 handback 交付门槛：**101 passed / 1 existing skip**；
  Ruff、相关 diff 检查通过，独立源码审查 12 项通过。
- 当前前端：**41/41 单元测试通过**，`npm run build` 的 TypeScript 与 Vite 构建成功。
  旧前端报告内的 31/37 属于历史计数。本次没有重新执行浏览器、真实 Provider 联调或原生发布打包。
- 最新已关闭真实条件的 L2/L3 实际任务、审阅和交回原 Router 均通过；整体仍有
  完整 Thread 读取、重启后的完整 Thread 读取和真实多跳 Recall 三项失败。
  新门槛仅完成离线验证，尚未重跑真实联合条件。
- 旧后端全套的 **2886 passed / 13 failed / 3 skipped** 只属于其原冻结源码，
  不是当前提交的新全套结果。

完整证据入口是 [当前验证索引](audit/superagent/final-verification.json)、
[后端交接](SUPERAGENT_NEXT_ENV_HANDOFF.md)、[前端报告](../desktop/CODEX-REFACTOR-REPORT.md)
及 [desktop 启动说明](../desktop/README.md)。本次 Git 提交不代表完整能力验收或桌面发布已通过。

## 新环境准备

后端使用 Python 3.11+，锁文件在原环境由 Python 3.13 生成；前端要求 Node.js 22.18+。
依赖和启动步骤分别见根 README 与 desktop README。沿用当前架构与锁定依赖。
Provider 凭据需要在新环境重新配置，不能从仓库推断或上传密钥。
Tauri 的本机 Python runtime 和开发工作空间也须按原说明配置。

`.local` 中的旧凭据包装器、协议草稿、原始数据库和本机源码快照不在 Git 中。
历史 JSON/XML 报告继续保留；其中 `.local` 引用是原环境证据位置，不保证在新克隆中存在。
继续验收时依据已提交协议与预算记录创建新的明确协议、冻结当前源码，使用新隔离工作空间，
不得重启旧失败数据库或沿用旧源码哈希为新修改背书。

当前完成阶段为 **99 次调用全部结算 / 1089785 charged-or-reserved tokens**，
上限仍为 **160 次 / 2500000 tokens**。此前拟议的最终新条件最多 60 次 / 1000000 tokens，
尚未执行。每任务 32 次 / 500000 tokens、8192/4096 输出和零额外 Gateway 重试不变。
模型显式上下文仍为用户声明的 **100M**，与请求可负担性和累计预算分别计算。
