# Phase 1 — Milestone 1：数据可靠性重构报告

日期：2026-11-14（Asia/Shanghai）。状态：**本里程碑实现和本机验证完成，等待用户验收；Milestone 2 未开始。**

最终结果：**63 passed，1 warning，7.38 秒，退出码 0**，包含原有 8 个测试和新增 55 个回归用例（含参数化用例）。Ruff 检查、格式检查、pip check 和 Python 语法编译均退出 0。本报告只认定数据层里程碑的验证结果，不代表自主 Agent 或原生桌面产品验收通过。

机器可读入口：[milestone1-verification.json](audit/phase1/milestone1-verification.json)。原始输出、JUnit、源码前后差异及哈希清单位于 [milestone1 证据目录](audit/phase1/milestone1/)。

## 1. 范围与基线保留

以 [Phase 0 审计](PHASE0_AUDIT.md) 的 F03、F04、F05、F06、F07 为主要修复对象，落实 F08 的事务 Outbox 基础。开始修改前已阅读本次指定的审计、STATUS、ROADMAP、PRODUCT_ARCHITECTURE，以及 README、适用 AGENTS 和相关源码。没有调整单一 Conversation、Persistent Router、多 Quest、Hidden Worker 的产品设计。

工作分支：`codex/phase1-milestone1`，基于原 `codex/phase0-audit` 工作区。Git 已提交基线仍为 `38686cf`；发布包导入和 Phase 0 文件原本尚未提交，因此不能把普通 Git diff 当成全部源码改动。已保存修复前 [源码快照](audit/phase1/milestone1/baseline-source.json) 和 [逐文件差异](audit/phase1/milestone1/source-changes.diff)，供审阅本里程碑实际修改。未自动提交或推送。

`docs/PHASE0_AUDIT.md` 和 `docs/audit/phase0/` 共 12 个文件的 SHA-256 与修改前全部一致，见 [保留核对](audit/phase1/milestone1/phase0-preservation.json)。未重跑会覆盖历史证据的 Phase 0 脚本。原报告中的“等待 Phase 1”是当时的历史结论，当前状态以本报告和 STATUS 为准。

## 2. 修改文件清单

| 类型 | 文件 | 本次修改 |
| --- | --- | --- |
| 新增 | `backend/src/forge_core/migrations.py` | 单一版本来源、逐版本迁移、兼容检查、全链条回滚 |
| 重构 | `backend/src/forge_core/store.py` | 显式连接生命周期、Repository/UoW、CAS、Outbox、Workspace 绑定、WAL 初始化竞争重试 |
| 新增 | `backend/src/forge_core/settings.py` | 用户数据目录、绝对 Workspace、ID、明确的 `.env` 和环境变量优先级 |
| 修改 | `backend/src/forge_core/domain.py` | Quest.version、QuestVersionConflict；UTC/排版规范化 |
| 修改 | `backend/src/forge_core/quest_service.py` | 版本校验、合法状态检查、CAS、Event/Outbox 同事务 |
| 修改 | `backend/src/forge_core/router.py` | 用户消息、Quest、Event/Outbox、助手消息同事务；分类规则和回复内容保持原逻辑 |
| 修改 | `backend/src/forge_api/main.py` | 使用统一 Settings；接收 expected_version；Worker 提案与 Event/Outbox 同事务 |
| 新增 | `backend/src/forge_api/__main__.py` | 统一 `python -m forge_api` 启动入口及 `--workspace-root` |
| 修改 | `backend/pyproject.toml` | 将已在环境中存在的 python-dotenv 声明为直接运行依赖 |
| 修改 | `backend/.env.example`、`scripts/run-backend.ps1`、`README.md` | 配置规则、稳定目录、统一启动、迁移及检查说明；不依赖 shell Activate |
| 规范整理 | `backend/src/forge_core/registry.py`、`runtime.py` | Ruff 的导入/格式整理，没有新增业务行为 |
| 原测试整理 | `backend/tests/test_api.py`、`test_core.py` | 导入/格式整理；8 个测试函数及原有断言 AST 均保留 |
| 新增测试 | `backend/tests/conftest.py`、`fixtures/legacy_v1.sql` | 独立旧版数据库样本及 fixture，包含原版重复版本行和全部历史实体 |
| 新增测试 | `backend/tests/test_migrations.py` | 版本唯一、旧库升级、未来/混杂版本拒绝、逐步迁移、失败回滚、并发初始化 |
| 新增测试 | `backend/tests/test_transactions.py` | 跨实体失败、提交失败、崩溃、隔离可见性、Outbox、连接释放 |
| 新增测试 | `backend/tests/test_concurrency.py` | 真实竞争、陈旧 CAS、四种终态保护、API 409 |
| 新增测试 | `backend/tests/test_settings.py`、`test_startup.py` | 跨 CWD/进程、配置优先级、Workspace 绑定、真实 HTTP 重启 |
| 交付 | `docs/STATUS.md`、本报告、`docs/audit/phase1/milestone1-verification.json`、其引用证据 | 本次状态、验收依据及未解决边界 |

## 3. 修复前后对比

| 问题 | 修复前 | 修复后及证据 |
| --- | --- | --- |
| F05：版本管理 | 每次初始化追加 version=1；忽略 version=999 | 当前版本 3；约束单行元数据；重复旧版 1 合并；未来版、空版本、混杂版本拒绝。`test_migrations.py` |
| F03：半提交 | Event 失败仍留下已更新 Quest；Router 失败遗留用户消息/Quest | Router、状态转换、Worker 提案分别拥有完整 UoW；Event 和 Outbox 插入失败一起回滚。`test_transactions.py` |
| F04：读写竞争 | 先读 proposed 后无条件写，能覆盖另一个连接提交的 rejected | Quest.version + BEGIN IMMEDIATE + status/version CAS；成功版本递增；过期版本或非法转移为明确 409。`test_concurrency.py` |
| F06：连接泄漏 | sqlite3 的 with 只提交/回滚，没有 close | 所有生产连接由 Store.connection 的 finally 显式关闭；BEGIN、事务体和 COMMIT 异常都释放。30 轮真实读写及异常测试验证 |
| F07：路径与配置 | 默认库相对 CWD；`.env` 没有加载 | 默认用户数据目录；所有 DB 路径绝对化；`.env` 从 Workspace 读取；跨 CWD 重启保留 Router/历史。`test_settings.py`、`test_startup.py` |
| F08：事件持久化基础 | 只有 events，没有待投递记录 | Event/Outbox 同连接同事务；可读取 pending、按 seq 排序、持久化幂等确认；投递运行时仍待实现 |
| 实现中发现的 WAL 竞争 | 四连接首次打开时，PRAGMA journal_mode=WAL 偶发立即 SQLITE_BUSY | 保留 busy_timeout；只对 WAL 初始化的 SQLITE_BUSY 做有时限重试，其他错误直接抛出并关闭；最终 5 轮、每轮 4 连接真实并发初始化通过 |

## 4. 数据库迁移设计

版本唯一来源为 `forge_core.migrations.SCHEMA_VERSION = 3`。`store` 仅兼容性导出该常量，不维护第二份版本值。

| 路径 | 变化 | 数据保留规则 |
| --- | --- | --- |
| 空库 → v1 | 顺序创建历史表结构 | 保留独立可测试的初始步骤；这是迁移内部状态 |
| v1 → v2 | 重建单行 schema_meta；Quest 加 version；创建 Outbox 和索引 | 接受历史多个相同的 1；现有 Quest.version=0；历史事件全部回填为 pending |
| v2 → v3 | 创建 Workspace 单行绑定表 | 原消息、Quest、Worker、Model、Router/capsule/cursor 和事件均保留；成功迁移后初始化事务绑定 Workspace |

当前版本表结构核心为：

```sql
CREATE TABLE schema_meta(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    version INTEGER NOT NULL CHECK(version>=1)
);
```

应用要求恰有一行 `(1, 3)`。初次空库和旧版升级的所有待执行步骤统一放在一个 `BEGIN IMMEDIATE` 事务内，锁内重新读取版本，逐步执行并更新版本；成功后一次 COMMIT。任意步骤或提交失败均 ROLLBACK。使用逐条 `execute`，避免 `executescript` 的隐式提交破坏回滚。

v1 历史重复元数据是明确的兼容例外；空元数据、不同版本混杂、无元数据但有业务表、非法值、未知新版本或降级请求均拒绝处理。拒绝未来版的测试核对其版本记录和 schema 没有被改写，没有把新版静默当作旧版重新初始化。

测试分别验证各迁移步骤，并在每个真实步骤之后执行错误 SQL，使 DDL、数据、元数据全链条回滚；旧库升级中途失败后可重新打开重试。旧版 fixture 含中文消息、Router ID `router-legacy`、event_cursor=7、Quest、Worker、Model 和 Event；升级后逐项保留，并通过 integrity_check/foreign_key_check。

迁移与 Workspace/Conversation/Router 的 seed 分属两个明确事务：迁移成功而 seed 失败时，数据库可已处于 v3，但 seed 不留下部分绑定或会话，重开可重试。迁移回滚不等于自动备份；旧库应停机备份后，显式指定 `FORGE_DB_PATH` 原地升级。不会自动搜索/搬移旧 CWD 下的库，也不支持自动降级。

## 5. 事务、Outbox 与并发设计

`Repository` 只使用调用方连接执行 SQL，不自行提交或关闭。`Store` 保留原有外观方法；独立写操作也通过 UoW，读取通过显式关闭的连接上下文。`UnitOfWork` 单次使用、先 BEGIN IMMEDIATE、正常退出提交、任何异常回滚，最后关闭连接。迁移调用方同样必须管理连接生命周期，Store 和迁移测试均显式关闭。

以下跨实体操作在一个事务里完成：

- Router：用户消息 → Quest（创建分支）→ Event → Outbox → 助手消息。
- Quest 状态：读取状态和 version → 校验 → CAS 更新 → Event → Outbox。
- Worker 提案：检查 Quest → Worker → Event → Outbox。
- 初始化：Workspace 绑定 → main Conversation → Router identity。

当前 TeamComposer 仅做本地只读模型评分，没有外部模型或工具调用。后续接入 Provider 时，应先生成提案、再做短事务提交，并引入幂等请求/执行协议，避免持有 SQLite 写锁等待外部网络。客户端响应丢失后的重复业务请求去重不属于本次 UoW 保证。

状态写入使用：

```sql
UPDATE quests SET status=?, updated_at=?, version=version+1
WHERE id=? AND status=? AND version=?;
```

服务先校验状态机，再核对 CAS 影响行数。`expected_version` 对 API 暂为可选，以兼容原调用；携带过期版本返回 `QuestVersionConflict`（HTTP 409），不携带时也必须在写事务里读取当前状态并校验，不能绕过终态。非法状态转换返回含源/目标状态的 DomainError（HTTP 409）。这保护数据一致性，**尚不构成审批/完成证据检查**。

每条 Event 同事务生成一条 Outbox，`event_id` 为主键并引用 events.id。接口为 `list_outbox(pending_only=True, limit=200)` 和 `mark_outbox_delivered(event_id)`。pending 在重启后保留；确认可重复，未知事件返回 False。旧事件从未有消费确认，故升级后作为 pending；后续消费者必须以事件 ID 做持久化幂等处理。这里没有 exactly-once、dispatcher、lease 或自动重试的声明。

每条连接保留 WAL、foreign_keys=ON、busy_timeout=5000ms。WAL 初始化重试使用相同超时预算、仅捕获 SQLITE_BUSY；BEGIN 超时仍明确失败并释放连接，不无限等待或静默吞掉其他错误。重复读写、RuntimeError、KeyboardInterrupt、真实延迟外键 COMMIT 错误、真实锁争用和子进程 `os._exit(23)` 都有验证。崩溃测试重开无未提交消息/Quest/Event/Outbox，且能继续写入。

## 6. Workspace 与启动规则

Windows 默认根为 `%LOCALAPPDATA%/FORGE/workspaces/default`；macOS 为 `~/Library/Application Support/FORGE/workspaces/default`；Linux 为 `${XDG_DATA_HOME:-~/.local/share}/forge/workspaces/default`。DB 默认为根下 `data/forge.sqlite3`。

根优先级：显式 `--workspace-root`/Settings 参数 → `FORGE_WORKSPACE_ROOT` 进程变量 → OS 默认。根必须是绝对路径；`.env` 不得设置根。根下 `.env` 可缺省，或由进程变量 `FORGE_ENV_FILE` 选择；显式文件不存在则报错。其余配置遵循进程变量 > 文件 > 默认；相对 DB/配置文件路径均从 Workspace 根解析。空值、非法端口、不同 Workspace ID 复用已绑定数据库均报错。默认 ID 从规范化根派生；移动时应保持显式稳定 ID。

统一入口 `python -m forge_api --workspace-root <绝对目录>` 在导入 API 前确定根，按相同 Settings 的 host/port 启动。PowerShell 脚本直接使用虚拟环境 Python，检查原生命令退出码并恢复 shell 目录；已通过语法解析检查。

真实 HTTP 测试在两个不同 CWD 启停 Core，两处放置诱导 `.env`，并设置冲突进程根，证明显式根和 Workspace 文件规则实际生效。本次两次 `/health` 的 Router ID 均为 **`router_e9e316652877`**；2 条消息、rejected Quest version=1、2 条 Event 和 2 条 pending Outbox 保留，旧版本和非法转换请求均为 409。详见 [HTTP/重启记录](audit/phase1/milestone1/http-restart.json)。

**这验证了同一 Workspace 下 CLI/未来桌面 Core 共享入口的数据一致性。当前 Tauri 壳尚未管理 Core，本次没有验证原生桌面启动、WebView、sidecar 或安装包。**

## 7. 实际测试命令、输出与退出码

除特别说明，工作目录为 `D:\Competition_this\Agent\backend`，Python 为 `.venv\Scripts\python.exe`（3.13.9）。以下没有删除原测试或 mock SQLite；monkeypatch 只用于隔离环境变量。数据库故障来自真实 trigger/SQL/外键/锁，竞争来自真实独立连接/线程，崩溃来自真实子进程。

| 阶段 | 命令（省略共同 Python 前缀） | 实际输出摘要 | 退出码 |
| --- | --- | --- | --- |
| 先写回归，旧实现运行 | `-m pytest -q --tb=short --junitxml=..\.local\phase1\milestone1\before-pytest.xml` | `37 failed, 12 passed, 1 warning in 3.13s` | 1（预期失败） |
| Workspace 补强修复前 | `-m pytest tests/test_settings.py -q --tb=short --junitxml=..\.local\phase1\milestone1\settings-before.xml` | `3 failed, 6 passed in 1.20s` | 1（预期失败） |
| 启动入口实现前 | `-m pytest tests/test_startup.py -q --tb=short --junitxml=..\.local\phase1\milestone1\startup-before.xml` | `No module named forge_api.__main__`；`1 failed in 1.49s` | 1（预期失败） |
| WAL 竞争修复前的检查 | `-m pytest -q --tb=short -o junit_logging=all --junitxml=..\.local\phase1\milestone1\final-pytest.xml` | `sqlite3.OperationalError: database is locked`；`1 failed, 58 passed, 1 warning in 6.59s` | 1（发现新增问题，保留为 initialization-race 证据） |
| 最终完整测试 | 同上 pytest 命令，修复 WAL 竞争并扩为 5 轮并发初始化后 | **`63 passed, 1 warning in 7.38s`** | **0** |
| Ruff 检查 | `-m ruff check --no-cache src tests --config pyproject.toml` | `All checks passed!` | 0 |
| Ruff 格式检查 | `-m ruff format --check --no-cache src tests` | `20 files already formatted` | 0 |
| 依赖一致性 | `-m pip check` | `No broken requirements found.` | 0 |
| 语法编译 | `-m compileall -q src tests` | 无输出 | 0 |
| 依赖安装 | `-m pip install -e ".[dev]"` | `Successfully installed forge-agent-core-0.2.0`；python-dotenv 1.2.4 已满足 | 0 |

完整 stdout、时间、命令、cwd、退出码和哈希见 [验证 JSON](audit/phase1/milestone1-verification.json) 与 [最终命令结果](audit/phase1/milestone1/passed-command-results.json)。一次并行 Ruff 缓存写入曾报告访问冲突，最终命令使用 `--no-cache` 取得无缓存干扰的检查结果，没有关闭规则。未修复的 1 个测试 warning 为既有 Starlette TestClient/httpx 弃用提示。

原 8 个测试在最终 JUnit 中全部通过；[保留核对](audit/phase1/milestone1/original-tests-preserved.json) 记录测试名称及断言 AST 一致性。55 个新增回归用例覆盖每一步迁移、跨实体回滚、CAS、终态、资源关闭、崩溃和跨目录启动。`git diff --check` 退出 0；PowerShell 启动脚本语法检查通过。由于发布包源码尚未 Git 提交，另外提供修复前快照差异，避免只凭 Git diff 误判范围。

## 8. 未解决问题与验证边界

- Phase 0 F01/F02 仍未解决：身份/来源约束、权限审批、可信完成证据尚未强制执行。状态机一致性不能替代授权。不得据本里程碑接通不受控工具执行。
- Router 仍为原关键词逻辑；Worker 只有提案记录。Provider、自主 Loop、调度、任务图、事件消费/回传、长期记忆和运行恢复均未在本次实现。Outbox 只完成持久化基础接口，F08 不能整体关闭。
- F07 的稳定目录和共享 Settings 已验证；原生桌面 Core 生命周期仍属于 F12。API 仍在模块导入时组合初始化，但 Store 不保留长期连接；完整 app lifespan/桌面进程管理留待后续。
- SQLite events 的历史业务引用外键/领域引用校验、统一 API 数据库错误响应、请求幂等去重、备份/恢复 UI、可复现 Python 依赖锁和 CI 仍需后续工作。
- 本机 Windows/Python 3.13.9 已运行；最低 Python 3.11、macOS/Linux 分支没有实际环境验证。进程中途退出不等于断电测试；磁盘满、网络文件系统和长时间压力未测。
- 未重跑无关前端/原生构建。Phase 0 记录的 Vite/esbuild 开发依赖漏洞、Cargo 网络阻塞导致的 Tauri 原生编译未验证、`codex/MASTER_PROMPT.md` 缺失仍保留。
- 未自动发现或迁移用户未知位置的数据库。旧库位置需要显式确认配置；事务迁移不替代运营备份策略。

## 9. 下一里程碑依赖与停止点

本次对应 Phase 0 计划中 P1-A/P1-B 的数据基础；其完整资源 lifespan 仍留待应用生命周期实现。建议下一里程碑在用户验收后，优先落实 P1-C 的 Core 信任边界、Action/Approval/Policy 和可信 Evidence 完成门槛；依赖本次单行版本、UoW/CAS 和持久化事件基础。新增审批、权限及证据表必须以 v4 及后续迁移承接，保留 v1→v3 的测试历史。

随后才依次推进：依赖锁/CI 与安全合同 → 真实 Provider gateway → 严格 RouterAction/Quest Resolver/capsule/cursor → 幂等事件消费/Outbox 投递与 DAG → 受治理的 Hidden Worker/工具。桌面 Core 管理器依赖共享 Settings、身份/令牌和生命周期合同，使用同一个 Workspace，不创建额外用户对话。

**当前停在 Milestone 1 待验收；未开始 Milestone 2。**
