# Backup schema fixture correction (v15)

The existing `test_v7_snapshot_is_migrated_in_staging_before_publish` constructs
a synthetic schema-7 database by removing later tables from its test-only backup.
One line adds `worker_model_reservations` to that existing removal tuple. The
fixture had retained the new schema-12 table before rerunning forward migrations,
which then correctly rejected an attempt to create an already-existing table.

No Worker is started or executed before this fixture's backup. Removing the new
table from that synthetic older snapshot is necessary to represent schema 7.
The original manifest/hash maintenance, restoration and assertions remain intact.
Removing the single added line reproduces the exact prior file hash; every
original assertion's AST is unchanged. The [JSON audit](worker-model-usage-migration-v15-backup.json)
records both hashes and the exact line.

The focused run passed **52 tests / one existing deprecation warning in 5.47s**:
the previous 51 migration and Worker cutover checks plus this backup restoration
case. Ruff lint, format and whitespace checks passed. New evidence is retained in
[the test log](worker-model-usage-migration-v15-backup-tests.log) and
[JUnit report](worker-model-usage-migration-v15-backup-tests.xml).

All earlier v14 audit and test reports remain unchanged. This result does not
claim a new full-suite or live acceptance outcome; no Provider was called and no
historical or user database was modified.
