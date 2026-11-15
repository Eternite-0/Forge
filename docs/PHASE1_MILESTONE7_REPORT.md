# Phase 1 Milestone 7 — Desktop integration

Status: **in progress; not an Alpha release or final acceptance**. The user has
deferred desktop acceptance while this round focuses on [backend hardening](PHASE1_MILESTONE7_BACKEND_HARDENING.md).
Native Core
supervision, the desktop UI, backup/restore, settings and the bounded Alpha
harness are being integrated. Earlier isolated startup and browser layout checks
are preliminary; they do not prove the installer, recovery or real-provider
end-to-end scenario.

The backend hardening round passed its frozen-source local checks: 488 tests,
one warning, and the recorded static, dependency and historical-preservation
checks. This does not establish the complete six-role Mock chain, paid Alpha,
desktop acceptance or NSIS installer acceptance.

The desktop keeps one persistent conversation. The UI presents persisted
messages and a separately paginated, sanitized projection of Core events,
receipts and context archives; it does not display private model reasoning or
invent progress. Pending local sends retain their request ID for safe retry.
The native credential interface is intended for set/delete/existence checks,
not retrieval of plaintext. The implementation and these boundaries remain
subject to final cross-component verification.

The current desktop test runner requires Node 22.18 or later; intermediate
checks were run on Node 24.19.0. A Windows desktop UI workflow is included, but
no GitHub Actions run is claimed. The complete backend and frontend suites,
dependency audits, Windows native build and installer, isolated recovery tests,
real-provider bounded Alpha scenario and historical evidence check must be
recorded against the final frozen source before the milestone can be accepted.

## Intermediate desktop checks

On the current intermediate source, `npm test` passed 12 tests and
`npx tsc --noEmit` exited successfully. `npm audit` against the official npm
registry reported zero vulnerabilities. `npm run build` emitted production
assets successfully, but the process remained running after Vite reported
completion; it is not yet counted as a passing build. Logs are retained under
`.local/phase1/milestone7/ui/` with `-current.log` names. These checks are not
final frozen-source evidence.

The browser smoke test uses explicitly labelled Mock HTTP responses; it covers
the single composer, retry identity, IME, Markdown restrictions and viewport
behavior. Real Core fixture UI checks and the bounded live Alpha scenario are
separate and remain subject to their own evidence and results.
