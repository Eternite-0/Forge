# Exact semantic source identity correction — v16

The Router integration exposed a real source-identity bug: an explicit
`source_refs=["quest:..."]` read also tokenized that reference for the legacy
lexical candidate union. The generic `quest` term could select another Quest that
sorted earlier. A one-item limit then substituted that unrelated source for the
requested identity. This affected both semantic candidate construction and
post-call identity validation.

`MemoryService.retrieve_context` now accepts `exact_source_refs=True`. This mode
reads only the supplied source identities and suppresses lexical/Quest candidate
expansion. All existing source hash, revocation, expiry, type and filter checks
remain active. Missing or invalid references return no item. The default remains
false for existing lexical retrieval callers. Semantic candidate construction and
post-await source rereads use exact mode; Router commit validation also uses it.

Deterministic tests explicitly select each of two fixed Quest IDs on opposite
sides of the lexical sort order, verify each candidate key equals its actual
source identity, and require the selected original Quest to be returned. Revoked
and missing IDs cannot fall back to matching query/Quest candidates. Legacy
default expansion is checked separately.

The eight-file regression passed **174 tests in 29.54 seconds**. Ruff and formatting
checks passed on the owned files. Results are in
`semantic-exact-source-v16-regression.xml` and its log. The first integration record
retains the pre-Router-adaptation failure; it was not overwritten. The Router
owner separately runs the final cross-module integration suite after enabling
exact commit validation.
