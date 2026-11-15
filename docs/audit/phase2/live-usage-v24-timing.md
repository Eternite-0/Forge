# V24 original strict-run timing

Immutable original driver and read-only event/latency interpretation. SQLite URI mode=ro, query_only, explicit BEGIN. No Store, Provider calls, credentials, source edits or original record changes.

- 20261115T130806Z-L1-ddf8db: 25.609 seconds, 22,670 known tokens, unknown reserve 0; strict failed; 0 Workers, 0 Core source admissions; calls by purpose {'memory_semantic_retrieval': 1, 'router': 1, 'router_reflect': 1, 'planner': 2}.
- 20261115T130806Z-L2-18ee03: 26.62 seconds, 24,523 known tokens, unknown reserve 0; strict failed; 0 Workers, 0 Core source admissions; calls by purpose {'memory_semantic_retrieval': 1, 'router': 1, 'planner': 2}.
- 20261115T130806Z-L3-f90e7f: 46.875 seconds, 39,851 known tokens, unknown reserve 0; strict failed; 0 Workers, 0 Core source admissions; calls by purpose {'memory_semantic_retrieval': 1, 'router': 2, 'planner': 3}.

Durable stage timestamp intervals include intervening orchestration work; they are not exact request timings. Gateway observations lack call_id and are not joined ordinally to attempts. Attempts lack settled_at, so exact historical settlement timing is unavailable.
Every actual Planner attempt remains counted even when a new Router notice invokes planning; no attempt is removed or reclassified as a free retry.
Local Gateway success does not establish upstream identity, correct planning, task success or Phase 2 acceptance. A run with no Workers provides no live Core prefetch, review, repair or restart proof.
All failures and unknown usage remain original observations; monetary cost remains unknown when prices are unconfigured.
