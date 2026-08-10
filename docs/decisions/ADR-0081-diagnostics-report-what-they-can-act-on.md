# ADR-0081: Local diagnostics report what an operator can act on

Date: 2026-08-09
Status: accepted
Form: full (CONTRACT semantics + a state schema version)
Closes: #172, #173, #175

## Context

Three reports from one live session (2026-08-09) share a cause: the two
read-only surfaces that describe local state — `tg doctor` and
`tg clone status` — printed fields the operator could neither act on nor
interpret.

`doctor` reported `preview_perms_ok: false` and therefore `ok: false` on every
account, permanently. Previews have been written `0600` since 1.1.2 (commit
`7f0e304`), so nothing on the write path was wrong; roughly thirty files
created *before* that fix were still `0644`, and the only documented remedy
was `tg store cleanup --confirm` — a command whose job is deleting spent
records, which happens to chmod the survivors on its way out. A health command
that is red forever, and whose remedy is a reaping command, is not a health
signal.

`clone status` listed ten entries whose every field was `null` except
`clone_id`: `.json` state documents from mid-July written at schema
`version: 1`, which the v2 loader refuses. They outnumbered the healthy clones
on the reporting machine. Nothing distinguishes them from a genuinely corrupt
v2 slot in the output, and nothing can be recovered from them.

`clone status` also identified a clone's destination only as
`destination_id: 4373611234`. Destinations are private channels created by
`clone init`; they have no username, and the operator cannot tell which clone
writes where without resolving the id through a second command.

## Decision

1. **`doctor` repairs the preview modes it checks.** Before the checks read
   them, every preview file whose mode is not `0600` is chmod'd back
   (`session.restrict_file`, fail-open). `checks.preview_perms_repaired`
   counts what was tightened this run, and a one-line count goes to stderr.
   The repair is a local-state mutation, so it asks
   `safety.enforce_local_mutation_allowed` rather than testing a flag —
   `--readonly` and `TGCLI_READONLY=1` are one gate, and a hand-rolled
   `if readonly` honoured only the first (review finding). When it is
   blocked the check reports `false` exactly as before; `TGCLI_NO_SEND=1` does not apply because no Telegram traffic is
   involved. The remedy hint no longer names `store cleanup`.

2. **Unimportable state slots are counted, not listed.** A `.json` document
   with no `.db` beside it that `state.load` refuses is reported as the
   top-level `pending_import` count. It stays out of `clones` unless
   `--all` is given, and `status` prints a stderr pointer to `--all` when it
   hid any. The count is reported under a `SOURCE` filter too: the slots are
   on disk either way, and their identity can never match a filter. Slots
   that fail for any *other* reason — a truncated `.db`, a `.db`/`.json`
   collision — stay visible by default, because those are actionable.

3. **The destination gets a name in state.** `CloneState` gains
   `destination_title` and `destination_username`; `clone init --commit` and
   `clone sync` record them as they resolve the peer. `clone status` emits a
   `destination: {id, title, username}` object in place of the scalar
   `destination_id`, and the plain column prints the title, falling back to
   the id. `status` stays strictly offline — it prints what state recorded,
   never a fresh resolve, and both names are `null` on a clone that neither
   command has touched since this ADR.

4. **The clone state schema migrates forward in place.** `SCHEMA_VERSION`
   goes to 2 and `statedb.connect` applies a forward-only `_MIGRATIONS`
   table (`ALTER TABLE meta ADD COLUMN …`) inside one transaction. A file
   *newer* than the running build is still refused, unchanged: reading it
   would silently drop whatever it added.

## Rejected alternatives

- **Leaving `doctor` read-only and pointing at `store cleanup`.** That is
  today's behavior and is what the report is about. A diagnostic that can
  fix a permission bit it already had to `stat` should fix it; the
  `--readonly` escape keeps the strict reading available.
- **Deleting or auto-importing the legacy v1 slots.** They are operator-owned
  files holding destinations of real Telegram channels. tgcli renames state,
  it does not delete it, and a v1 importer would be a compatibility path for
  documents no supported version writes.
- **Resolving destination titles in `status`.** It would break the guarantee
  that `status` opens no session — the one command that must work while an
  account is cooling — and cost an RPC per clone.
- **Deriving the title from `attribution.destination_title(source_title)`.**
  Right until the operator renames the destination, then confidently wrong.
- **Recreating the database instead of migrating it.** A clone state holds an
  id map that cost thousands of governed Telegram requests to build.

## Contract impact

`docs/CONTRACT.md` §5 (`doctor`) gains `preview_perms_repaired`, the repair
paragraph, and the `--readonly` interaction. §11 (`clone status`) replaces
`destination_id` with the `destination` object, adds `pending_import` and
`--all`, and renames the fourth plain column to `destination`. Existing clone
databases migrate on first open; `schema_version` in `status` reads `2`.
