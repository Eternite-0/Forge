# V16 live usage audit

Only explicitly named finished V16 live runs. No Store construction, Provider calls, credential access, database mutation, product source edits, or historical evidence changes.

All databases were opened with mode=ro, query_only and an explicit BEGIN. No Provider request or database mutation was performed.

- 20261115T082953Z-L1-16b57e: strict acceptance failed; Gateway known tokens 66,428, unknown reserve 0, original cap 180,000; 12 attempts, final states {'settled': 12}.
  Worker runs 0; independent model review claims 0; actual revision requests 0; accounting checks pass; read snapshot changes 0.
  Planner inbox: 5 processing attempts; planning_failed:DomainError:Invalid_graph_proposal; dead=True.

- 20261115T082953Z-L2-a68109: strict acceptance failed; Gateway known tokens 78,872, unknown reserve 0, original cap 360,000; 12 attempts, final states {'settled': 12}.
  Worker runs 0; independent model review claims 0; actual revision requests 0; accounting checks pass; read snapshot changes 0.
  Planner inbox: 5 processing attempts; planning_failed:DomainError:Invalid_graph_proposal; dead=True.

- 20261115T083313Z-L3-673598: strict acceptance failed; Gateway known tokens 90,794, unknown reserve 0, original cap 630,000; 12 attempts, final states {'settled': 12}.
  Worker runs 0; independent model review claims 0; actual revision requests 0; accounting checks pass; read snapshot changes 0.
  Planner inbox: 5 processing attempts; planning_failed:DomainError:Invalid_graph_proposal; dead=True.

Final Gateway settlement and exact local accounting do not establish task success or strict acceptance.
Planner invalid proposals are not retained as raw model responses. An Invalid_graph_proposal diagnostic alone cannot establish a specific schema, capacity, or model-quality root cause.
No absence of a Gateway attempt is treated as permission to refund a reservation. Refunded records are checked only against their existing durable terminal state.
Review PASS is accepted only by rebuilding the complete packet and running the existing live provenance/hash binding checks.
Unknown usage and unconfigured monetary costs are never converted into zero.
