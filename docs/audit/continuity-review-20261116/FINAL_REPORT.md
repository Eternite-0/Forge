# 连续工作能力收敛报告（2026-11-16）

## 身份与当前状态

- 审查基线：`0bef3316a5783348c1f5687d9d584a2cf1eb5e7a`。
- 开始时本地为 `a0fd096`，已检查远端并在原分支 fast-forward 至
  `7c1763bb2ce962a924f2266c55978e1483bd112b`；这两个后续提交只改 desktop。
  基线与该 HEAD 的 backend Git tree 均为 `016a4eadb5034871c00148afda88279224e36822`。
- 分支始终是 `codex/phase1-continuous`。本轮不改前端，不发布 tag，不改变
  Persistent Router / Hidden Workers / Trigger / Pass-through / Chain of Recall 架构。
- 本文件先随实现冻结记录检查合同。最终完整回归和新的真实条件结果将在执行后
  追加，未执行/未闭合的结果不得按通过报告。最终真实协议会记录准确实现 commit
  及实际源码文件 SHA-256，不只引用旧 HEAD。

## 源码实现与失败链路

### 1. 项目路径与身份

原 `project_context` 在原文上做 root 子串匹配，因此 alpha 会命中 alphabet、
alpha-plugin；同名目录又会从路径中的 basename 误选。现在复用 discovery 的
`_named_paths`，解析路径后按目录分隔边界匹配，选择最长的已知项目 root，
从剩余 prose 排除路径，再独立识别名称/ID。CJK 邻接保留，普通词和连字符续接拒绝。

只读复审进一步复现了原补丁的规范化缺口：`alpha/../beta` 会误选 alpha，
symlink alias 会丢失 beta fence。已按 discovery 的 resolved identity 规范化。
`evidence/canonical-path-reproduction.json` 以保存的旧源码复现两种失败，再验证
当前修复；新增执行级用例同时检查 discovery、snapshot 项目隔离及错误 continuation
被拒绝。项目身份仍不授予 filesystem 或工具权限。

### 2. Thread 在执行中可见，验收覆盖实际产物

原完整读取 gate 仅在交付时执行。Worker 现在在每次构造模型请求前，从持久
source/proof 重算 unread continuation，注入有界 pending metadata，并纳入现有
context overhead 计量；压缩或重启也不丢失。source/hash 损坏时在新模型调用前
以 `continuation_source_unavailable` 关闭，不伪造已读，不补预算。

推理期间收到的事件在下一模型轮可见。已提交的工具 batch 仍按原执行机制结算，
不丢弃/重放可能有副作用的工具。保留完整分页、真实 source、时序、run proof、
handback、权限和最终交付 gate；通知不是正文，也不等于读取或采用。

另一失败链是 independent review 验证了 Core context call 后将正文丢弃，导致
reviewer 看不到追加要求。现在完整 verified `read_trigger` / `handback_trigger`
arguments/result 作为 `context_only` 进入原 packet 和容量计量，**无 execution_mode，
不能冒充 action receipt**。review 合同明确核对实际 artifact，已读或自述不足以 PASS；
不能采用的补充必须解释或 proved handback，不能扩大 mandate。

新增离线对照：完整读页却遗漏要求的真实 JSON 文件，scripted literal reviewer
返回 NEEDS_REVISION，之后必须经真实 file_revise 才通过。这是编排/产物证据回归，
不是实际 Provider 的语义能力声明。

### 3. 压缩、进程重启后的 Recall

原 Router archive 保留 source IDs，却没有可供 Agent 跟随的原文 typed relation。
现在 Router archive 暴露 `archived_message -> message:`，保留 `previous_archive`，
沿用原分页/hash/revocation/项目边界。Worker ordinal 不外泄，Worker 的 context
source 仍走原权限通道。项目聚焦时 Recall root 为 project ref，非聚焦时提供当前
archive ref；listing/summary 不算历史事实。

离线执行测试使用新的 Store/Router，通过实际模型 tool request/result 推进，覆盖
archive→previous→original 及 project→quest→original/update、分页、Unicode、撤销、
来源篡改；随机历史值不放进 restart prompt。真实 harness 再使用不同 OS 进程。
宿主检查数据库只作 oracle/预算审计，不冒充 Agent Recall。

### 4. 原失败分类与保守合同变化

原始全套：**17 failed / 2932 passed / 12 skipped**，原 log/XML gzip 与 SHA-256
已保存，未用修复后日志覆盖。分类明细见 `REPORT.md` 和
`evidence/failure-classification.json`：资源策略 4、原预算不足 4、fixture 模型资格 5、
平台 1、JSON parser 1、旧 Thread 读取 fixture 2。

- fixture 使用现有 4096 输出策略；诊断模型资格用 8192，不改变生产预算。
- 原 360k 大分支、120k operator 及 L1/L2 不可行 fixture 改为明确拒绝：保留
  required_total > original_ceiling、无计划落库、wire/bounds 不变。没有使旧任务涨额。
  另补原上限内小独立分支、170k∩180k operator 正向覆盖。
- Python subprocess 使用当前 `sys.executable`，不再硬编码 Windows venv 路径。
- JSON nesting 诊断增加 string/escape-aware 深度扫描，跨 Python 构建稳定失败码，
  不泄漏输入；字符串里的括号不计深度。
- Thread fixture 改为从零完整分页且追加源另读，保持来源篡改/无执行证据拒绝。

本轮中间失败也保留。首轮完整修复回归为 **3017 passed / 12 skipped**，但运行期间
driver/tests 变动，标注为 intermediate，不冒充 final。随后冻结回归为
**3012 passed / 1 failed / 12 skipped**：probe CLI 全局禁用日志未恢复，污染后续
watchdog 日志断言；已加 try/finally 与调用前后状态断言。Mock 联跑也暴露了 fixture
读取不属于当前 run 的历史 Trigger，生产权限正确拒绝；fixture 限定真实 subscription，
没有放宽 source 权限。所有阶段原日志保存在 `runs/`。

## 离线验证（最终已闭合）

**最终全套：3013 passed / 0 failed / 12 skipped / 1 warning，711.67 秒。**
362 个 backend Python 文件在执行前后完全一致，manifest SHA-256：
`16d47803c47cba36eb8adc6f7b76a55a31c47cd5d5355c15f84670899f9b0518`。
日志、JUnit、执行前完整 manifest、字节 hash 及前后校验保存在 `runs/final-full/`。
12 个 skipped 的 testcase IDs 与原始完整回归逐项一致，没有新增 skip。

- 两 driver + Thread 五项持久 smoke：**20 passed**，含 native Gateway
  MockTransport、真实 SQLite/工具、三个 OS 子进程、共享 ledger、逐项目 oracle、
  project 对调拒绝及 Mock PASS 不能过 live-only gate。
- logging 修复与 driver/Worker loop 联合：**43 passed**。
- 全部本轮 Python 修改及新增文件 Ruff 通过；`git diff --check` 通过。
- 最终全套在全部 Python 源码冻结后重新执行；精确 manifest 与结果另附。
- 原 12 个 skip 没有整体扩大；Mock 与 live 分开归属。现存 Starlette/httpx
  deprecation warning 单独保留，不为本轮引入新依赖。

## 新的最小真实协议（尚未执行）

本轮用户提供本地 Provider 并授权新条件。仅新隔离 workspace，不恢复任何旧暂停实验。
历史阶段起点严格为 **99 settled attempts / 1089785 charged-or-reserved tokens**，
总 cap **160 / 2500000**；本次一次性新增 cap **18 / 220000**，Router 子分配
10 / 140000，为 Thread 留 8 / 80000。每任务仍32 calls / 500k，Worker output8192、
Router4096，HTTP retries0，concurrency1，共享 SQLite pre-HTTP ledger。

脚本从环境读取 credential，不输出或保存 key。每次调用前检查 source freeze 和
ledger，未知用量保留 reservation，失败不自动重跑，不退款、不重放未知副作用。
真实子条件：两个项目创建/切换；实际压缩后新进程沿 archive typed references 读原文；
Worker generate 尚未完成时注入超过首屏/首分页的随机补充，核对下一轮可见、完整读页、
实际 JSON note、action receipt、独立 review packet 和生产 NodeVerifier 闭合。

Recall 最终 reply 按项目逐项校验 decision/pending，交换项目即失败；实际 raw 模型
reply、Router result 与持久 message 必须一致。Thread 15 项检查必须全部满足，Mock
PASS 永远不满足 live gate。独立 review 后不直接 SQL 改状态；live PASS 必须再经
production NodeVerifier 成为 succeeded/completed，审计不能遗留 active worker。

## 未解决与范围限制

- 当前真实执行结果尚待本轮闭合；历史完整 capability acceptance 仍失败，不能由
  本轮最小场景或离线全绿替代。
- 小 JSON Thread 产物不是任意长任务/任意副作用的正确性证明；不能将脚本 oracle
  推广为一般模型能力结论。
- Router 历史 decision/todo 在最小协议中由用户源指定，证明恢复来源事实，不证明
  所有 Agent 自生决策都能自动恢复。压缩 handoff 使用中性摘要，事实必须回原文。
- source freeze 是运行时完整 backend Python + 明确协议/依赖文件清单，并非任意
  外部进程不可变保证；隔离条件由单一 owner 执行，不与旧实验并跑。
- 本地 workspace/SQLite 保留完整 trace；版本化真实证据将排除凭据与任意远端错误正文。
- 未提交前端改动、未发布 Release、未推送。新增脚本下次运行前必须先对本次 ledger
  重新对账，不能再次套用旧 99 起点来重置历史用量。
