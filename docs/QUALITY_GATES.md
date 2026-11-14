# Quality Gates and Known Limitations

## Current engineering evidence
- M6 local engineering verification passed: 372 tests, one deprecation warning, Ruff from both working directories, dependency checks and historical-evidence hashes. One bounded real-provider continuation succeeded after five retained diagnostic failures; see the [M6 report](PHASE1_MILESTONE6_REPORT.md).
- M7 desktop lifecycle, settings, backup and UI integration are in progress. Early isolated native startup is not a packaged Alpha or a general correctness result. On the current intermediate source, the desktop's 12 Node tests and TypeScript check passed; npm audit against the official registry reported zero vulnerabilities. A production build emitted its output, but the command has not yet exited and therefore is not counted as a passed build. Final frozen-source checks, installation and the requested Alpha scenario remain pending.
- A Windows Rust dependency audit reported no vulnerabilities but did report two upstream advisories for `proc-macro-error` and `glib`; the native owner reports those packages are outside the Windows target graph. This does not establish safety on other platforms. See the final M7 evidence for the audited graph and exact scope.

## Verified at Milestone 3
- Backend suite: 140 passed, 1 warning; Ruff, pip check and the locked Python dependency audit passed on Windows/Python 3.13.
- Model Provider and Router protocols were exercised with explicitly labelled Mock responses and MockTransport, including local HTTP startup/restart tests. Real provider calls remain unverified.
- See [Milestone 3 report](PHASE1_MILESTONE3_REPORT.md) and [verification evidence](audit/phase1/milestone3-verification.json) for exact commands, results and limitations.

## Still open
- The final packaged Windows application, end-to-end desktop Core lifecycle and M7 Alpha scenario.
- General model quality, unrestricted task correctness, arbitrary-scale continuity and production durability. M5/M6 bounded live and local tests do not prove these claims.
- Final M7 credential vault, backup/restore and native integration acceptance; test credentials must remain dummy and temporary.

## Engineering cautions
- Router decisions are validated and ambiguous targets require clarification. Recall is bounded and signals omitted candidates; the compaction task snapshot is limited to 100 active Quests.
- SQLite has versioned migrations, transaction boundaries and regression tests; this does not establish production durability under every failure mode.
- Team scoring is a simple capability-fit baseline, not the final research algorithm.
- Never describe proposed Worker records as running Workers.
