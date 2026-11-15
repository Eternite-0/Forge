# Restart known-usage accounting diagnosis (v14)

Status: **defect confirmed; correction pending**. This is a bounded diagnosis of
the v12 L2 `draft-report` Worker run `run_7932f0527e66`. No original live record or
database has been modified, and no corrected live result is claimed.

The final local counter is 52,939 tokens. Its five settled Worker Gateway attempts
charge 37,510 tokens. The difference is exactly **15,429 tokens**:

`18,234 (first local reservation) - 2,805 (first actual usage) = 15,429`.

The four calls after resume charge 34,705 tokens. Consequently
`18,234 + 34,705 = 52,939`, while `2,805 + 34,705 = 37,510`.
The first Gateway reservation is separately 10,294 input + 8,192 output = 18,486;
that reservation is not the local 18,234 reservation and cannot replace it in a
refund calculation.

The original evidence is
`.local/phase2/runs/20261115T063556Z-L2-a3ad74/run.json`, SHA-256
`64699a9c4915e5e0097e11da9e22b36b9a439285d5de0a1c8b558f7abd58e70e`.
The accompanying [JSON diagnosis](restart-known-usage-v14-diagnosis.json) records
the original source snapshot, exact five attempt/call IDs, bindings and counters.

| UTC time | Durable fact |
| --- | --- |
| 06:37:03.258059 | First attempt `d9ddc5ed7bb9410ab57a7b6f57824652`, call `model_call_fa2eb9e94492`, admitted. Its durable final status is settled with 2,695 input and 110 output tokens. |
| 06:37:04.390312 | Event 28 `evt_4741959b2c21`: `quest.plan_pause`. |
| 06:37:04.390606 | Event 29 `evt_ecbb9d28b2a7`: `worker.command.pause`. |
| 06:37:06.803479 | Event 30 `evt_3e3c7f71d370`: second `core.started`. |
| 06:37:09.425094 | Event 31 `evt_f9e928cc8cef`: `quest.plan_resume`. |
| 06:37:09.425464 | Event 32 `evt_381ee1fe8432`: `worker.command.resume`. |

Both original driver snapshots, `restart.before` and `restart.after_start`, retain
`token_spent=18234`, `steps=1`, `tool_spent=0` for the affected run. Both already
record the first attempt as settled at 2,805 actual tokens and zero unsettled model
attempts. This excludes an unresolved Gateway call as the explanation for this
particular excess. The schema does not record the precise settlement timestamp;
the snapshots establish settlement before restart without inventing that time.

The old `_settle_model_usage` checks the current Worker lease before replacing a
local reservation with usage. Pause increments the generation and clears the
lease token. That correctly fences the old response from executing, but also
prevents its already-known accounting delta from applying. Schema 11 has no durable
local reservation keyed by model call, so restart cannot safely recover the delta.

The planned v14 change stores each new local reservation in the same transaction
as its counter admission. A bound, settled Gateway proof may settle that record
once, even when response ownership is stale. All response persistence, tool
execution, execution-state changes and PASS still require their existing fences.
Sent steps remain charged. Unknown or ambiguous attempts retain their reservation.
Calls without new durable reservation records receive no inferred historical
refund.

Inspection opened the original SQLite database using URI `mode=ro` and explicit
`BEGIN`; the connection reported `total_changes=0`. No Provider was called and no
request, response or credential body is included. The earlier
`capacity-design-v13` evidence remains unchanged: its cause was unresolved at the
time that document was written.
