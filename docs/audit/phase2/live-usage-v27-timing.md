# V27 original strict-run timing

Immutable original driver and read-only event/latency interpretation. SQLite URI mode=ro, query_only, explicit BEGIN. No Store, Provider calls, credentials, source edits or original record changes.

- 20261115T141947Z-L1-777a30: 40.381 seconds, 29,134 known tokens, unknown reserve 0; strict failed; 0 Workers, 0 Core source admissions; calls by purpose {'memory_semantic_retrieval': 1, 'router': 1, 'planner': 2, 'team_composer': 2}.
- 20261115T141947Z-L2-d4cf0c: 28.08 seconds, 20,838 known tokens, unknown reserve 0; strict failed; 0 Workers, 0 Core source admissions; calls by purpose {'memory_semantic_retrieval': 1, 'router': 1, 'planner': 2}.
- 20261115T141947Z-L3-cb0c7b: 39.825 seconds, 24,728 known tokens, unknown reserve 0; strict failed; 0 Workers, 0 Core source admissions; calls by purpose {'memory_semantic_retrieval': 1, 'router': 1, 'planner': 2}.

Durable stage timestamp intervals include intervening orchestration work; they are not exact request timings. Gateway observations lack call_id and are not joined ordinally to attempts. Attempts lack settled_at, so exact historical settlement timing is unavailable.
Every actual Planner attempt remains counted even when a new Router notice invokes planning; no attempt is removed or reclassified as a free retry.
Local Gateway success does not establish upstream identity, correct planning, task success or Phase 2 acceptance. A run with no Workers provides no live Core prefetch, review, repair or restart proof.
All failures and unknown usage remain original observations; monetary cost remains unknown when prices are unconfigured.
