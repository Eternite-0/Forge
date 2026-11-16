# 连续工作能力收敛报告（2026-11-16）

## 身份与当前状态

- 审查基线：`0bef3316a5783348c1f5687d9d584a2cf1eb5e7a`。
- 开始时本地为 `a0fd096`，已检查远端并在原分支 fast-forward 至
  `7c1763bb2ce962a924f2266c55978e1483bd112b`；这两个后续提交只改 desktop。
  基线与该 HEAD 的 backend Git tree 均为 `016a4eadb5034871c00148afda88279224e36822`。
- 分支始终是 `codex/phase1-continuous`。本轮不改前端，不发布 tag，不改变
  Persistent Router / Hidden Workers / Trigger / Pass-through / Chain of Recall 架构。
- 首次实现与两项真实条件实际执行 commit：
  **`9cb619f0b8fa0ad9d93ac9fe6ceaafc4dd358669`**。两项开始时工作区均 clean；
  运行前后完整 source freeze 一致，协议记录 commit 与逐文件 SHA-256。
  真实拒绝后发现并离线修复 tool-round 排序缺口，后续源码的完整回归单列；
  **后续修复未再真实调用**，不能冒充9cb619f的已执行结果。
- 后续排序修复源码 commit：**`61ec4f8570d72e22aac7ca8d30d05473151150ee`**。
  当前源码完整回归：**3015 passed / 0 failed / 12 skipped / 1 warning**；
  该commit没有新增真实调用。
- 结论：**当前源码离线全量回归已闭合；真实连续工作验收未通过。**
  项目创建/切换成功，实际压缩与新进程重启已执行，但 Recall 未闭合；独立 Thread
  场景观察到下一轮 pending，后续 Provider 拒绝，未完成产物与 review 验收。

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

## 首次实现离线验证（9cb619f，已闭合）

**首次冻结全套：3013 passed / 0 failed / 12 skipped / 1 warning，711.67 秒。**
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

## 排序修复后的当前源码离线回归（61ec4f8）

新增两种时序（模型推理中到达、真实工具执行中到达）先在旧源码红灯：
2 failed，分别复现 assistant/user/tool/tool 与 assistant/tool/user/tool。
修复仅延迟 inbox transcript 消费，仍即时 flush 持久 delivery。
新增测试核对整个 batch 连续、tool IDs 一一匹配、两真实工具各执行一次与 receipt、
pending 期间 inbox 未消费、下一轮 pending 可见、提前交付被原 gate 拒绝、
完整分页逐字节相等、原权限/预算不变。

初次修复后 targeted 30 passed/2 failed，原因是新测试误取较后的历史 delivery
feedback 而非前置的本轮 metadata。只修正新测试读取当前 snapshot，并透传 callback
原始断言（不让 Worker 包装为 model_error），没有放松生产 gate 或安全断言。
随后整个文件 **32 passed，21.88秒**，Ruff通过。红灯及中间失败日志全部保留，
不覆盖、不删除。当前源码完整suite已重新执行：

**3015 passed / 0 failed / 0 errors / 12 skipped / 1 warning，788.53秒。**
共3027个testcase；362个Python文件运行前后相同，与当前源码逐项匹配；
12项skip的ID、计数和原因与原始完全一致。完整log/JUnit、源码manifest、
runner核验、Ruff与定向日志保存在 `runs/tool-round-order/`，
`verification.json` 同时记录压缩与原文字节hash。Warning仍为已有Starlette/httpx弃用。

- 源码 commit：`61ec4f8570d72e22aac7ca8d30d05473151150ee`。
- 362文件当前manifest SHA-256：
  `d974f890ace9c03660cd8e85cc54fbb6724a7fcdac7a63a9155fd5945f969383`。
- **真实调用仍停止，排序修复未进行真实验证。**

## 新的最小真实验证（已停止，未通过验收）

### 授权、预算与执行边界

仅在用户本轮提供的本地 Provider 授权内运行新隔离条件，没有恢复旧暂停实验。
使用 `http://127.0.0.1:7863/v1` 与 `global:deepseek-v4.1-flash-sg`；
credential 通过隐藏 TTY 输入置入进程环境，不写 argv、文件或报告。
历史起点保留 **99 settled attempts / 1,089,785 charged-or-reserved tokens**，
阶段总 cap **160 / 2,500,000**；本轮新增总分配最多 **18 / 220,000**。
原每任务 32 calls / 500k、Worker output8192、Router4096 不变，HTTP retries0、
concurrency1，共享持久 pre-HTTP ledger；没有重置历史、退款或重放未知副作用。

### 条件 A：项目切换、压缩、新进程 Recall

原始证据位于 `runs/live-9cb619f/minimal/`，本地原 DB 位于
`/Users/tracy/Documents/Forge/.local/continuity-review/live-20261116-9cb619f/data/forge.sqlite3`。

- 两个真实项目成功创建并绑定；seed 保存 project/quest/source refs 及原文 hash。
- 实际 compaction 已产生 receipt，新 OS 子进程已启动 Recall。
- **Recall 子进程失败，未产生可验收的 recall-result。** Parent 按合同立即停止，
  failure code 为 `child_failed_no_retry_inspect_durable_ledger_and_call_receipts`。
  原计划的 Thread 因而是 `not_run_reserved`，不是通过。
- **4 次调用全部 settled，40,440 tokens**；此条件无未知用量、未决工具、
  action intent、reserved Worker model 或 active worker。
- 首个 harness 只保留 request/response hash，没有保留子进程 stdout/stderr 或
  原始响应。**无法从现有证据确认 Recall 的具体失败根因**，不能用数据库关系
  枚举、脚本推断或重放原条件补成“实际 Agent Recall 成功”。

### 条件 B：新的独立 Thread-only 条件

条件 A 已闭合后，仅对尚未执行的 Thread 创建新隔离 workspace，不恢复 Recall。
Opening 严格继承 **103 attempts / 1,130,225 tokens**；只允许本轮剩余
**14 attempts / 179,560 tokens**。调用已有、完整离线验证过的 `run_thread`，
不改冻结产品源码。一次性 launcher、其执行前 hash、协议、报告、边界 trace
及只读 DB 导出保存在 `runs/live-9cb619f/thread/`。
本地原 DB：
`/Users/tracy/Documents/Forge/.local/continuity-review/thread-20261116-9cb619f/data/forge.sqlite3`。

- 第一轮 generate coroutine 尚未完成时提交 **4,673 字符**的随机补充，关键 note
  位于首分页之后。`inflight_at_commit=true`；不把 coroutine 活跃夸大成“HTTP 已发送”。
- 第一轮真实 Worker 回复实际选择 `read_trigger` 与 `directory_list`，结算
  **4,498 tokens**；这里读的是原始 87 字符要求，不是后来 4,673 字符追加正文。
- **第二轮请求已出现该追加 Trigger 的 pending metadata**（offset0、total4673、
  payload hash），早于交付；由持久报告和完整 generate-boundary request 同时证明。
- 第二轮 Provider 返回 completed rejection，Worker 为 `blocked`，原事件记录
  `worker.model_request_rejected` / `usage_status=unknown` / `reservation_retained=true`。
  未自动重试。Gateway只有在收到非2xx且完整读取响应体后才标记rejected，
  因此这是Gateway对端HTTP拒绝，不是本地预算/配置预检或模型语义拒答；
  trace 没有保存 wire HTTP 状态码或错误正文，因此不能确认具体原因或归因最终上游。
- 追加内容完整分页、实际 JSON 产物落实、action receipt、独立 review 和
  production NodeVerifier **均未完成或未到达**；15 项验收不能按部分观察计 PASS。
- 此条件 **2 次尝试：1 settled / 1 rejected**。拒绝的 input21084 + output8192 =
  **29,276 tokens** 继续保留；另有 **1 个 Worker 本地 reserved model record**
  （28,341 local estimated tokens），它与 Gateway 为同一次调用，不双重加到
  campaign token 分母，也不释放。无 pending/running/uncertain 工具或 intent，
  active_workers=0，但有保留 reservation，**不能称“现场全部结清”**。

### 拒绝后的只读诊断与离线修复

第二轮持久DB与边界trace同时显示 `assistant(tool_calls A,B) → user(Thread通知)
→ tool(A) → tool(B)`。工具ID唯一且逐一匹配，工具都已成功；问题不是配对丢失，
而是 `_checkpoint()` 在处理pending工具前消费inbox，把通知插入未闭合的工具交换。
这是**已确认的本地排序缺口**、本次远端拒绝的首要结构性嫌疑；由于错误正文未保存，
**因果仍未确认**。证据：`thread/message-order-diagnosis.json`。

后续最小修复只延迟inbox进入transcript和consumed标记：pending/running工具批次
结算完后、下一模型请求前再消费。每个checkpoint仍flush持久Trigger，lease、
工具执行/恢复/副作用机制、权限与原预算不变；不丢通知、不提前制造已读证明、
不把追加要求拖到最终交付。先以当前失败源码跑新增离线测试保留红灯，再修复；
新源码完整回归与精确commit已单列。不再次调用Provider、不恢复原失败run。

### 聚合结算与证据

| 项目 | attempts | charged-or-reserved tokens |
|---|---:|---:|
| 历史本阶段起点 | 99 | 1,089,785 |
| 条件 A | 4 | 40,440 |
| 条件 B | 2 | 33,774 |
| **阶段累计** | **105** | **1,163,999** |
| 本轮新增合计 | 6 | 74,214 |

本轮新增的已知完整 usage 为 **44,938**；**1 次 unknown usage** 仍保留29,276。
累计104 settled +1 rejected，不把 rejected 当成功、不把 reservation 当确定账单。
阶段算术剩余55 attempts /1,336,001 tokens，本轮分配算术剩余12 /145,786；
**算术余额不是再次调用授权，也不解除 unknown usage 的停止条件**。
当前不再发真实调用，不重启 blocked run，不清除 reservation。

机器汇总：`runs/live-9cb619f/SUMMARY.json`；两个 DB 均以单次只读事务导出账本、
事件及安全计数；原始 DB 保留本地。JSON 按原始字节归档，Thread 边界 trace
仅确定性 gzip 压缩；归档 hash 见其 `SHA256SUMS`。trace 是模型 generate 边界，
不声称是完整 HTTP wire 捕获。源凭据未写入，长 `sk-` token 扫描为零。

## 未解决与范围限制

- 本轮真实验收仍失败：Recall 子进程未闭合且具体原因缺证；Thread 追加要求
  下一轮可见，但 Provider 拒绝后未证实完整读取和实际产物落实。历史完整 capability
  acceptance 仍失败，不能由本轮局部观察或离线全绿替代。
- Thread 拒绝仍有未知用量和1个保留 Worker reservation。当前证据不足以安全
  释放或重试；后续须先获取可归属的拒绝/usage 证据并核对授权、账本，不重放旧条件。
- 排序修复仅预防后续插队，不重写已损坏的历史transcript，不恢复blocked run。
  新增两例覆盖推理/工具执行时到达，但不直接证明checkpoint读到running的崩溃恢复
  或写类副作用正确性；这些仍沿用既有机制。若任务在工具批次结清前失败，持久
  pending通知不等于模型已看到。
- 小 JSON Thread 产物不是任意长任务/任意副作用的正确性证明；不能将脚本 oracle
  推广为一般模型能力结论。
- Router 历史 decision/todo 在最小协议中由用户源指定，证明恢复来源事实，不证明
  所有 Agent 自生决策都能自动恢复。压缩 handoff 使用中性摘要，事实必须回原文。
- source freeze 是运行时完整 backend Python + 明确协议/依赖文件清单，并非任意
  外部进程不可变保证；隔离条件由单一 owner 执行，不与旧实验并跑。
- 条件 A 仅有响应 hash，诊断不足；条件 B 保留脱敏 generate 边界 trace，
  仍无 wire 错误正文。不能声称两个条件都有完整远端诊断。版本化证据不含凭据。
- 未提交前端改动、未发布 Release、未推送。新增脚本下次运行前必须先对本次 ledger
  重新对账，不能再次套用旧 99 起点来重置历史用量。

## 最终交付核验

- 真实执行结束时362个 backend Python文件仍与首次3013-pass全套manifest相同。
  随后只读诊断定位上述排序缺口，以新增离线红灯复现后再做最小修复，
  不做Prompt试探或真实重跑；新源码已重新跑完整suite（3015 passed），不是旧全绿替代。
- 原 `SHA256SUMS` 162项保持原样；final-full 的8个压缩/原文字节 hash 另在
  `runs/final-full/verification.json` 中核验。新增 `FINAL_SHA256SUMS` 统一覆盖
  最终报告、README、final-full 和两项真实证据，不覆盖或改写原失败记录。
- 12项skip的ID、计数、原因与原始相同：11项Windows专属，1项缺外部参考checkout。
- 本报告不宣称旧容量任务在原ceiling下变得可行；改变的不可行任务合同及原因已逐项
  记录，上限、无落库与权限安全断言未放宽。

统一最终清单可从仓库根目录运行：
`shasum -a 256 -c docs/audit/continuity-review-20261116/FINAL_SHA256SUMS`。
最终证据提交只追加本报告、README和归档；源码仍绑定上述61ec4f8，真实结果仍绑定9cb619f。
