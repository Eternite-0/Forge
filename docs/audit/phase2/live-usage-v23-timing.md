# V23 original strict-run timing

Immutable timing and failure interpretation of three original finished strict V23 drivers. SQLite URI mode=ro, query_only, explicit BEGIN. No Store construction, Provider call, credentials, source edits or original record changes.

- 20261115T124831Z-L1-e59339: 21.647 seconds, 17,772 tokens, unknown reserve 0; strict failed, 0 Workers/Core admissions. Four calls: retrieval, Router and two Planner attempts.
- 20261115T124831Z-L2-f05578: 26.283 seconds, 20,196 tokens, unknown reserve 0; strict failed, 0 Workers/Core admissions. Four calls: retrieval, Router and two Planner attempts.
- 20261115T124831Z-L3-c7b618: 31.023 seconds, 23,763 tokens, unknown reserve 0; strict failed, 0 Workers/Core admissions. Four calls: retrieval, Router and two Planner attempts.

The observed stage intervals are durable event/driver timestamp differences and include intervening orchestration work; they are not exact per-call request timing.
Gateway observations do not persist call_id. They remain separate latency observations; no ordinal join is treated as exact attribution to an attempt. Gateway attempts lack settled_at, so exact historical settlement timing is unavailable.
Gateway success measures the local adapter response. It proves neither upstream model identity nor valid planning or task completion.
All three runs failed planning before any Worker, prefetch, independent content review or restart. Vacuous zero-source accounting is not live prefetch validation; goals 9/11 and Phase 2 remain unaccepted.
