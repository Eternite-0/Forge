# 给主代理：continuity fixture 独立审查发现

审查写集仅 `/Users/tracy/Documents/Forge/docs/audit/continuity-review-20261116/`，未改源码/测试，未调用真实 Provider。

## 已处理的遗漏：3 个新增回归（原 P2，后续独立确认关闭）

2026-11-16 后续更新：主代理已按执行/上下文两类拆分断言。独立补测 `runs/followup-fixtures.xml` 中本文件 **21 passed**，包含原三参数回归。唯一真实 `file_write` 仍要求 `execution_mode=live`；上下文仅 `read_trigger` / `handback_trigger` 且无 `execution_mode`，须含当前 trigger；原无数据库副作用、`receipt_id is None`、唯一 action receipt 与篡改拒绝断言未删。修复合理，不是简单放宽总数。这里的 live 标签由离线 scripted adapter 提供，不代表真实 Provider 调用。以下保留最初发现的历史，不再作为待修失败。

审计自跑的 `/Users/tracy/Documents/Forge/docs/audit/continuity-review-20261116/runs/safety-fixtures.xml`：**145 passed / 3 failed**。

失败位置：`/Users/tracy/Documents/Forge/backend/tests/test_worker_core_provenance_superagent.py:103`，测试 `test_real_core_checkpoints_pass_review_and_completion_without_fabricated_action_receipts` 的 `read_trigger`、`recall_object`、`handback_trigger` 三个参数。

原因：`/Users/tracy/Documents/Forge/backend/src/forge_core/independent_review.py` 的 `_review_receipts()` 新增已验证、`context_only=True` 的 Thread 上下文投影；原测试依然要求整个 `receipts` 只有一个 `file_write`。原始 baseline XML 中这三项均通过，因此不属于原 17 个失败。

建议修复合同而非放宽安全：分别验证执行回执与上下文回执。执行集合仍只能含原来真实的 `file_write`；上下文必须先通过 `_verify_worker_context_call`，必须 `context_only=True`，不能伪造 action receipt，不能生成 artifact。继续保留 `call.receipt_id is None`、action_receipts 数量不增、读取无数据库副作用、篡改/跨 run/来源不匹配拒绝等原断言。不能简单删掉断言或改为 `len(receipts)>=1`。

## 覆盖提醒的后续修正（不能宣称原案例可行性仍通过）

360k branch、120k operator、L1=180k/L2=360k 四项由“成功构造”改为“原限额不足必须拒绝”在安全意义上合理；没有增加原预算。但这些分支提前返回，不再覆盖原来的成功路径。应保留拒绝用例，并另有明确、合法、预算内的正向分支/独立池/预算交集测试；不能把旧任务涨预算后仍称原任务通过。

主代理已补两个正向小用例，审查确认：双独立节点保留原 360k ceiling、无串行池、总分配不越界；operator170k 与源180k 取交集、reserve17000，不增加源任务预算。提供方 `positive-capacity.log` 已见 **19 passed in 4.60s**，本审计只读日志、不重复跑 pytest。此修正回应局部正向覆盖损失，不复活旧大分支/120k/L1/L2 的可行性结论。**仍须观察 final full**。

## 离线产物对照已补；真实模型语义验收仍未做

最初建议：完整读取后故意遗漏**范围内必需**新增要求，应不能 PASS；范围外 handback 不应授予新工具或权限。随后新增 `backend/tests/test_continuation_artifact_review.py`，独立补测 **6 passed**：检查真实本地产物，遗漏时由 scripted literal reviewer 返回 NEEDS_REVISION，再通过绑定旧 receipt/hash 的真实 `file_revise` 修正；范围外解释/handback 不增加权限；只有上下文而无 action 时不能准入 review。此离线对照缺口已补，不再报为完全缺失。它只证明包构造、编排和产物修复路径，不能证明真实模型会正确判断全部语义，mock PASS 不晋升为 live acceptance。

## 本审计写集约束引出的补测限制（非产品放宽）

独立补测总计 **157 passed / 7 failed**。7 项均来自新 `test_continuity_minimal_probe.py`：audit 的 pytest tmp 只能放在本任务 docs 写集内，脚本则拒绝在仓库非 `.local` 目录创建新 workspace，触发 `workspace_must_not_modify_repository_sources`（包括前置拒绝覆盖掉后续期望错误码的断言）。保留完整失败 XML/log 和 `evidence/followup-classification.json`；没有修改安全保护、搬到写集外或将其伪报为通过。该文件另外 **19 passed**；本审计不能独立背书该文件全部通过。无需为审计削弱此拒绝策略。

## 已确认的结果

- 原 17 个失败均有对应离线通过观察：10 个 fixture 文件 **275 passed**，另一个生命周期测试在安全扩展组通过（同库两次启停，`model_budget_attempts=0`）。不是新的全后端通过结论。
- 两组各自测试前后 diff 一致，但两组之间 `worker_runtime.py` 增加了 DomainError fail-closed 处理，不能合并成一个冻结源完整套件。
- `failure-clusters-fixed.xml` 的 6 failures 是中间态：5 个 `dataclasses.replace(ModelProfile, ...)` 类型错误在当前 fixture 已改为 `model_copy`；剩余旧请求数断言也已修复。不要把中间态当最终，也不要抹去历史。
- 已保全主代理提供的新结果：`failure-clusters-fixed2` **201 passed**（平台/parser 另测）、`review-targeted` **226 passed**、`provenance-fixed.log` **81 passed**；这些提供方日志与独立复跑分开记账。
- 独立补测 provenance **21 passed**、continuation **30 passed**、artifact-review **6 passed**、restart-Recall **21 passed**、parser **60 passed**。不能与先前不同快照组相加后声称新的全后端回归通过。
- `0bef3316a5783348c1f5687d9d584a2cf1eb5e7a` 与 `7c1763bb2ce962a924f2266c55978e1483bd112b` 的 `backend` Git tree 均为 `016a4eadb5034871c00148afda88279224e36822`。

通知方式：本审计任务没有暴露 `send_message_to_thread` 等协调工具；已用任务进度消息即时指出发现，并在此保留可直接转交的消息。主代理通过 send_input 确认“收到 MAIN_AGENT_NOTICE”，不是终端用户确认；交接以主代理的确认归属。未声称本审计通过不存在的工具送达。依主代理收敛要求不再新增 pytest 或快照。主代理报告 final full 于 2026-11-16 17:58 +08:00 启动、预计约 11 分钟；本审计报告先冻结，运行结果待主代理最终报告。
