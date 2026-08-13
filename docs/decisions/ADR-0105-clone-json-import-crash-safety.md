# ADR-0105: Clone JSON→SQLite import is crash-safe

Date: 2026-08-13
Status: accepted
Ticket: T06 (`docs/thermos-audit-2026-08-13/tickets/T06-clone-json-import-crash.md`)

## Context

`clone/statedb.py::finish_json_import` wrote the legacy `<clone_id>.json`
into `<clone_id>.db` (ADR-0060) and only then renamed the JSON to
`.imported`. A crash between those two steps (power loss, OOM kill — not a
Python exception, so no `except` clause runs) left both `.db` and `.json`
on disk. `clone/state.py::load()` treats that shape as a genuine conflict
and fail-closes with a `PolicyError` demanding "manual resolution", even
though the DB it just wrote is complete and healthy — there is nothing to
resolve, only a rename that did not get to run.

## Decision

Import becomes two atomically-ordered renames around the SQLite write
instead of one rename after it:

1. `begin_json_import` renames `<clone_id>.json` to `<clone_id>.json.importing`
   *before* touching SQLite. This rename is the durable signal "an import is
   underway"; nothing depends on it happening quickly, and it is what makes
   the ambiguous both-plain-files shape only ever describe a real conflict
   going forward.
2. `finish_json_import` writes the full state to `<clone_id>.db`
   (`persist(..., full=True)`, unchanged) and renames `.json.importing` to
   `.json.imported`.

`state.load()` checks for a `.json.importing` marker before its existing
both-files check. When present, it re-reads and re-validates the data from
the marker file and re-runs step 2 unconditionally. Both operations are
idempotent — persisting the same full snapshot twice and renaming an
already-`.imported` file are no-ops in effect — so this resumes correctly
regardless of which sub-step of the previous attempt actually crashed:
before the SQLite write started, mid-write, or after the write committed
but before the final rename ran. A `.json.importing` marker found alongside
a plain `.json` (which the two renames above should make unreachable in
practice) still fail-closes, preserving the original "manual resolution
required" guarantee for a genuine conflict.

No behavior changes for the steady states: a lone `.db`, a lone `.json`
(first import), or genuinely both `.db` and `.json` present.

## Rejected alternatives

- **Auto-heal by comparing existing `.db` contents to the `.json` on a
  both-files hit:** works for the "crash after persist, before rename"
  window, but not for "crash mid-`persist()`" (schema created, no `meta`
  row yet), which needs its own case anyway. The marker-file approach
  handles every sub-step with one idempotent resume path instead of two.
- **fsync + rename-only durability tricks:** the failure here is ordering
  across two separate renames, not durability of either one; reordering
  the renames removes the ambiguous window instead of hardening it.

## Contract impact

`docs/CONTRACT.md` §11: the both-files-is-exit-2 sentence gains a note that
a `.json.importing` marker is a private in-progress artifact, resumed
automatically by the next `load()`, not a second ambiguous shape.
