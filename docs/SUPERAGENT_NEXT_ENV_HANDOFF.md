# FORGE 后端迁移交接 — 2026-11-16

后续交付方式已由用户改为将后端与前端一起提交 GitHub，使用
[`codex/phase1-continuous`](GITHUB_CONTINUATION.md)。不需要迁移包。
下文“尚未提交”和迁移包说明保留为此前交接时点；新环境优先按 GitHub 继续开发说明操作。

用户要求尽快交付，准备换环境继续开发。本环境收口当前修复和必要回归，
不再启动新的真实模型条件。**完整联合验收仍未通过**；请勿把任务成功改写为整体验收成功。

## 当前代码与范围

- 仓库：`D:\Competition_this\Agent`；分支：`codex/phase1-continuous`。
- 基准 HEAD：`57a162d31f9b559353fecbde61f95dd72f878abd`。
- 改动尚未提交、推送。新环境必须迁移未跟踪文件和未提交改动，不能只克隆 HEAD。
- 本轮只处理后端。现有前端改动由另一会话维护，迁移时一并保留，不要覆盖。
- 原 Provider 设置未改。显式模型上下文为 **100M**，发现接口返回的 1M 保留为冲突元数据；
  模型容量、单次请求可负担性、累计开销分别计算。

## 最近真实结果

最新已关闭条件为 `20261116T055126Z-capability-continuity-f87e7e`。
33 次调用全部结算，376561 tokens，无未决工具操作。

- L2：Research 与 Reviewer/integrator 实际执行并通过审阅，严格引用及内容检查通过。
- L3：实际失败测试 → 修改代码 → 成功测试；最终候选哈希与成功回执一致，7 个独立留出测试通过，Reviewer PASS。
- 双项目关联、独立任务并行、原 Router 收到审阅后的完成、真实历史压缩、正常停机和同身份重启通过。
- 未通过的原检查只有 `complete_thread_consumption`、
  `post_restart_complete_thread_consumption`、`typed_multihop_recall`。
  续接预览已送到 Worker，但部分完整消息未读，重启后没有真实 Recall 调用。

原报告、独立审计及失败结论保留在
[当前验证索引](audit/superagent/final-verification.json)。历史工作区不得重新启动执行。

## 交付时新增的生产修复

`worker_continuations.py` 将 inbox 的“已送入上下文”与完整读取分开。
交付前必须具备当前 Worker 实际选择的完整分页读取及 Core 来源证明，
或真实成功且有持久理由的显式 handback。来源、哈希、范围、工具转录及调用顺序均需验证。
缺页时保留已经计费的草稿，给出有界反馈，继续使用原步骤、工具、token、费用和期限。
来源损坏时在同一受租约保护的事务中阻塞，不发表成功结果。

此修复不强制或伪造 Recall，不自动给予 PASS，也不扩大事件文本的权限。
当前源码只获得交付前的离线针对性验证，**尚无修复后的真实模型验收**。
确切测试结果及源码哈希见当前验证索引；先前全套 **2886 通过 / 13 失败 / 3 跳过**
属于旧冻结源码，不能视为本次源码的新全套结果。

## 新环境继续顺序

1. 保留完整工作目录或先完整备份，再迁移后端源码、脚本、测试、文档和现有前端改动。
   迁移包只覆盖其清单所列后端内容，不是完整桌面应用或 Git 历史。
2. 安装 Python 3.11+，在 `backend` 创建虚拟环境并安装锁定依赖：

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
   ```

3. 先读 `AGENTS.md`、README、本交接文件及验证索引。针对性复核可运行：

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_worker_continuation_completion.py tests/test_worker_core_provenance_superagent.py tests/test_worker_trigger_backpressure_superagent.py tests/test_superagent_events.py tests/test_worker_first_archive_phase2.py -q
   ```

4. 配置新环境已经授权的 Provider，使用占位变量名传递凭据，勿把密钥写入文档或日志。
   `.local/superagent/run_capability_current.py` 是旧机器凭据包装器，含旧机器路径，
   不能直接当作可移植启动器。不得改动原 Provider 配置来迁就测试。
5. 下一轮真实调用前先独立审核当前修复、运行必要全套回归，再冻结最终源文件。
   `.local/superagent/completion_thread_read_audit.py` 是**未执行的协议草稿**，
   必须先适配新环境的凭据入口及源码清单，创建新的不可覆盖协议记录，再运行一次新隔离条件。
   不得沿用旧冻结哈希为迁移后的修改背书。

## 必须保留的预算与边界

当前完成阶段累计 **99 attempts，全部结算，1089785 charged-or-reserved tokens**。
原阶段上限仍为 **160 attempts / 2500000 tokens**。
拟议的最终新条件最多 60 attempts / 1000000 tokens；它尚未执行，
预算算式为 `99+60=159`、`1089785+1000000=2089785`。
每任务仍为 32 calls / 500000 tokens，观察 900 秒、结算排空最多 130 秒，
Worker/Router 输出 8192/4096，Gateway retries=0。

此前关闭阶段的 155 attempts、历史未知用量及未知工具效果另行保留，不清零、不退款、不重放。
不要改验收判定、扩大单任务限额，或把宿主枚举关系当作 Worker 的实际 Recall。
新环境的完整联合验收仍须观察到真实完整 Thread 读取、重启后 3 个完整对象的 2 跳关系 Recall，
以及 L2/L3 审阅后交付回原 Router。
