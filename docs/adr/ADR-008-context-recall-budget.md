# ADR 008: Bounded context, sourced recall and model-call admission

Status: M6 local implementation; see the verification report for evidence and limits.

The Router and Workers use structured, integrity-checked archives instead of
discarding history. User messages remain unverified and retain their source
references; Worker tool calls and results retain linked source and receipt data.
Recall derives project, Quest, decision, artifact and failure observations from
Core records and labels manually supplied claims as hypotheses. Source snapshots,
plan bindings, revocation and historical freshness are checked at retrieval.
Unbound artifact observations remain historical and cannot prove a current plan
or file. Indexed synchronization and retrieval use bounded batches and expose
incomplete results instead of silently guessing a unique Quest.

Every provider HTTP attempt requires a durable budget reservation. Token,
concurrency and configured price limits apply to the applicable scopes, including
legacy opening balances. Unknown usage remains reserved and unknown price stays
unknown; cancellation is not evidence that a remote request stopped. A trusted
operator may set a remote execution bound for eventual concurrency recovery.

This design prioritizes preserved user instructions and explicit failure when
limits are reached. It does not solve unbounded user history, unlimited active
Quests or arbitrary evidence volume. Task relevance, paginated evidence and
longer-term context scale require further work. The M6 synthetic experiments are
not evidence of real-model quality or general task correctness.
