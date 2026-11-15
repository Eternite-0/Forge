# V16 historical acceptance boundary

V16 remains failed and Phase 2 incomplete. Its frozen backend suite retained **1,304 passed, five failed and one warning / 328.69 seconds**. The five failing cases are listed in the JSON; later V17 fixes do not alter this outcome.

All three strict live levels failed before plan approval, each after five Planner processing attempts and ten Planner HTTP calls. The final durable reason is `Invalid_graph_proposal`; detailed rejected drafts were not retained. No Worker, independent Review PASS, revision, restart or archive/readback occurred.

| Level | Actual Gateway tokens | Original cap | Final attempts |
| --- | ---: | ---: | --- |
| L1 | 66,428 | 180,000 | 12 settled, zero unknown |
| L2 | 78,872 | 360,000 | 12 settled, zero unknown |
| L3 | 90,794 | 630,000 | 12 settled, zero unknown |

The source manifest contains 188 files with canonical hash `e069c337d3d4cb5554181ef79a0f3803700b6e5d7f6c5a117dcb35d17c3f9cad`. This is a historical identity; the working source has advanced to V17. The complete original test and runtime evidence remains preserved.

See `acceptance-independent-v16-boundary.json` for original artifact hashes and limits, and `live-usage-v16-audit.json` for the mode=ro/query_only/BEGIN database checks. No database, Provider or credential access was used to generate this boundary file.
