# Retained intermediate context-capacity regression

The first v9 local focused run added an approximately 850-byte current-budget
message to every Worker request. Six legacy archived-context cases then failed
at `context_budget`; the original approved allowance could no longer fit its
task/tool context. An isolated `-x` reproduction failed
`test_worker_reads_only_archived_own_context_and_charges_tool[arguments0-review_pending-False]`
after 27 passing cases in 15.50 seconds. This was a local scripted-model test,
not a live Provider result.

The larger pre-fix groups were interrupted after the six failures when subsequent
event-barrier tests waited for the model request that the exhausted context had
prevented. Those interrupted groups did not produce a completed JUnit result and
are not counted as passes. Their tool outputs remain in the chat history.

The correction omits the additional budget hint when the effective per-request
allowance is below 8192. It preserves the original task/tool context and original
admission checks. Larger native requests still receive the hint, whose complete
serialized bytes participate in input admission.

The unchanged legacy Worker file then passed 48 cases in 15.81 seconds:
`budget-feedback-legacy-v9.xml`. Planner/publication tests passed 36 cases in
3.64 seconds: `planner-publication-final-v9.xml`. The frozen-source full suite
and live results are recorded separately; these focused results do not certify
them.
