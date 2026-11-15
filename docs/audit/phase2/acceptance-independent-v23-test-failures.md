# V23 original complete-suite failures

1710 passed, 5 failed, 0 errors, 0 skipped, 1 warning in 412.81 seconds. The complete-suite gate failed.

Five original failures remain failures. Two old-schema reconstruction fixtures retained the V23 Core table while lowering schema_meta, so the fail-closed migration correctly rejected duplicate CREATE TABLE; repair the disposable old-schema fixtures rather than silently accepting an ambiguous schema. The remaining failures concern expired Planner attempt fixtures and background automatic planning. Static/preservation/usage success does not pass this suite.

All original XML, logs and archived source bytes remain preserved.
