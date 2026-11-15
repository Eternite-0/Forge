# First archive v12 intermediate test failures

The first run of the new first-archive regression reached the real governed
read/archive/readback/delivery path, then failed in its final audit SQL because
the test used the nonexistent `source_hash` column. A corrected test was run
before the instruction to preserve that first JUnit file reached its author;
the first XML was overwritten. Its failure remains in the session's tool trace,
but no standalone first-run JUnit is available. It must not be described as a
retained byte-identical test artifact.

`first-archive-v12-tests.xml` retains the next intermediate run: 11 passed and
one failed registry-window test fixture. `first-archive-v12-fixed-tests.xml`
records the corrected 12 passed cases. Original historical tests were not
changed. The final v12 full suite is recorded separately in
`frozen-tests-v12.xml` and `frozen-tests-v12.log`.
