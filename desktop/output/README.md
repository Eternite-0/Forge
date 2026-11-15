# Desktop validation evidence

This directory retains the UI reports, browser screenshots, scale matrix and
source comparison notes referenced by `../REFACTOR-REPORT.md`. Historical runs
and failures remain evidence of their own checkpoints; the report's first
section identifies the current implementation and latest checks.

- `reference-sidebar-unit.txt` and `reference-sidebar-build.txt`: latest unit and
  production build output for the reference sidebar changes.
- `playwright/new-reference-*.json`: latest affected layout, preview, tree and
  smoke checks; `playwright/source-port-*.json` retains the preceding checkpoint.
- `reference-native-layout/`: static source locators, extracted component rules,
  screenshot color comparisons and validation boundaries.
- `scale-matrix/` and other browser artifacts: earlier visual and layout checks,
  with the original scope described in the corresponding report section.

Tests use labelled fixtures or imported File API data. Browser checks do not
establish live provider success or native Windows DPI parity. Screenshots are
captures of rendered pages, not generated artwork.

Copied runtimes, native test workspaces, reference source copies, process/lock
files and draft CSS fragments are ignored and remain local. They are not needed
to build the application. See the desktop README for supported startup steps.
