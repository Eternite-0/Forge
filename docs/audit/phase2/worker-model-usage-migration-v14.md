# Worker model reservation migration (v14)

Schema 12 adds `worker_model_reservations` for new local model admissions only.
Migration 12 creates one table, its pending lookup index and four immutability
triggers. It does not insert, update or infer historical reservation records.

The table binds a call to its original Worker run, owner generation, lease,
Provider/model, execution mode, context, local token/output limits, optional cost
reservation, original prices and cost scopes. Checks enforce valid reservation
and terminal-usage bounds, including explicit integer requirements where SQLite
`CHECK` would otherwise accept `NULL`. Original binding/reservation fields and
completed settlements cannot be rewritten; reservation deletion is prohibited.
`INSERT OR REPLACE` also cannot overwrite an existing call when SQLite's default
recursive-trigger setting would bypass an implicit DELETE trigger. JSON meaning
and Gateway proof are checked by the separate runtime helper.

Only four version-maintenance edits were authorized in the existing
`test_migrations.py`: the schema assertion becomes 12; the fresh-database rollback
parameter list adds 12; and two final-version assertions become 12. Existing test
IDs, earlier-version assertions, legacy SQL fixtures and test meaning remain intact.
Exact changes and source hashes are recorded in the
[JSON audit](worker-model-usage-migration-v14.json).

Two existing v7/v8 Worker migration fixtures also require dropping the new
schema-12 table from their test-only backup before declaring that backup to be
an older schema. One table name was added to the v7 drop tuple and one adjacent
`DROP TABLE` was added to the v8 setup. Neither test runs the Worker before
constructing that backup, so the new table has no model-call reservations there.
Their original budget, evidence, migration and team assertions are unchanged.

The final focused run passed **51 tests in 5.11 seconds**:
`test_migrations.py`, `test_worker_model_usage_migration_phase2.py` and both
existing v7/v8 Worker cutover cases.
Ruff lint, Ruff format checks and whitespace checks passed. The final report is
[worker-model-usage-migration-v14-cutover-tests.xml](worker-model-usage-migration-v14-cutover-tests.xml).

The new tests preserve every preexisting table's complete UTF-8 serialized values
and SQL definitions across a v11 cutover, including settled/unknown/reserved
Gateway attempts, the original Worker counters and raw historical message JSON.
They also check inert reopening, migration rollback, admission transaction
rollback, foreign/duplicate call rejection, numeric/NULL limits, immutable records
and bounded cost settlement. Three additional replacement tests cover reserved,
settled and refunded calls with `recursive_triggers=OFF`.

Two earlier failures in the new fixtures are retained in separate reports. The
first used a positional plan INSERT predating a tenth column (28 passed, 2 failed,
16 fixture errors); explicit columns fixed it. The second assumed `rowid` on FTS
virtual tables (44 passed, 2 failed); complete-row stable JSON sorting fixed it.
Neither earlier report was overwritten.

This is migration validation. Runtime known-usage settlement, callback/recovery
races, response fencing and live acceptance remain separate checks. No Provider
was called and no historical live database was changed by this work.
