本次只读审计未发现把累计余额写入实际 `ModelRequest.context_limit` 的混用。冻结的 342 个文件哈希全部一致；未运行测试、未调用 Provider、未读取凭据，SQLite 使用 `mode=ro` 与 `query_only`，连接修改计数为零。

Worker、Worker Compactor、Router、Router Compactor、语义 Recall 和 Reviewer 分别保留模型容量与累计预算检查。新 continuity 的两个 Worker 均记录 100,000,000 模型窗口，累计分配分别为 166,667 与 166,666；failover 备用 GLM 模型保留其自身的 1,000,000 窗口。100M 来自用户明确声明，没有被测量证明，也不授予额外执行预算。原生 framing 使用 UTF-8 字节作保守 Token 上界。

新的 continuity 有两个 `invalid_compaction_handoff`。四次压缩调用均在 Gateway 与本地账本 settled；前两次分别产生 7,733 / 9,343 UTF-8 字节的有效归档，后两次结算后未产生下一份归档，随后分别阻断 Worker。压缩交接要求纯文本，不要求 JSON，因此不能将这两个失败归因于 JSON 或 Markdown fence。

现有拒绝事件合并了响应内容、长度、完成状态、工具、身份与来源摘要校验，且不记录失败调用 ID、响应哈希、实际字节数、动态摘要上限或具体失败谓词。账本只保留用量和预留，失败正文没有进入 Worker 消息或归档。没有足够持久证据确定具体失败类别；输出 Token 数也不能代替正文或字节数。

另有三个较小边界：

- 有 approved capacity policy 时，提示字段 `remaining_cumulative_tokens` 实际使用 `current_request_allowance`，会把已扣除后续保留的单次额度标成累计余额。本次两个 continuity capsule 均无此 policy，因此不是本次已观察到的失败原因。
- 拒绝事件的 request allowance 在压缩前计算，cumulative remaining 在压缩调用结算后重读，可能显示 50,223 对 36,826 或 50,052 对 40,680。这是两个时点的诊断，不能据此认定越预算准入。
- Router 与 Router Compactor 按模型容量组包后，仍可能在累计预算准入时安全拒绝；它们未主动按余额选择更小的完整消息前缀。这是可用性限制，本次没有把它作为已观察到的失败原因。

后续最小诊断建议是增加固定子原因、原 call/run/generation、输入与 frontier 摘要、响应 SHA-256、实际 UTF-8 字节数、摘要字节上限、有限完成状态分类和工具数量。保持正文私密，保持原验证、账本与 checkpoint fencing，不增加输出、预算或重试。

完整来源、行号、只读运行快照与源码哈希见 [机器审计](capability-capacity-compaction-readonly-audit.json)。实际请求的 `context_limit` 没有逐调用持久快照，不能仅凭账本预留反推所有请求字段。上述观察不改变原验收结论。
