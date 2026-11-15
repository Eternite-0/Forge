# V22 live usage audit

Only explicitly named finished V22 live runs. No Store construction, Provider calls, credential access, database mutation, product source edits, or historical evidence changes.

All databases were opened with mode=ro, query_only and an explicit BEGIN. No Provider request or database mutation was performed.

- 20261115T104536Z-L1-79aa8a: strict acceptance failed; Gateway known tokens 37,977, unknown reserve 0, original cap 180,000; 10 attempts, final states {'settled': 10}.
  Worker runs 1; independent model review claims 0; actual revision requests 0; accounting checks pass; read snapshot changes 0.
  Worker run_9bd71910657d: 11846 = 0 opening + 11846 Worker + 0 review tokens; 3 steps; state blocked.
  Held allocation audit pass; 0 serial starts, 0 one-time reserve markers; sent/unknown reservation retention=True.

- 20261115T104536Z-L2-776888: strict acceptance failed; Gateway known tokens 21,759, unknown reserve 0, original cap 360,000; 5 attempts, final states {'settled': 5}.
  Worker runs 0; independent model review claims 0; actual revision requests 0; accounting checks pass; read snapshot changes 0.
  Held allocation audit pass; 0 serial starts, 0 one-time reserve markers; sent/unknown reservation retention=True.
  Planner inbox: 1 processing attempts; planning_failed:DomainError:Invalid_graph_proposal; dead=True.

- 20261115T104536Z-L3-774dfc: strict acceptance failed; Gateway known tokens 23,858, unknown reserve 0, original cap 630,000; 4 attempts, final states {'settled': 4}.
  Worker runs 0; independent model review claims 0; actual revision requests 0; accounting checks pass; read snapshot changes 0.
  Held allocation audit pass; 0 serial starts, 0 one-time reserve markers; sent/unknown reservation retention=True.
  Planner inbox: 1 processing attempts; planning_failed:DomainError:Invalid_graph_proposal; dead=True.

Final Gateway settlement and exact local accounting do not establish task success or strict acceptance.
Planner invalid proposals are not retained as raw model responses. An Invalid_graph_proposal diagnostic alone cannot establish a specific schema, capacity, or model-quality root cause.
No absence of a Gateway attempt is treated as permission to refund a reservation. Refunded records are checked only against their existing durable terminal state.
Review PASS is accepted only by rebuilding the complete packet and running the existing live provenance/hash binding checks.
Unknown usage and unconfigured monetary costs are never converted into zero. Rejected HTTP attempts without usage still retain their full reservations.
Shared serial reserves are reconstructed once per Quest/accounting scope from every started Worker and the final durable markers. Stored start snapshots are checked against prior Gateway calls and opening balances; settled_at timestamps are unavailable, so historical settlement timing cannot be proved from the final ledger alone.
