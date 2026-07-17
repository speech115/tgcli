# Design: supersede a stale clone with `clone init --replace`

Date: 2026-07-17
Status: approved (brainstorming)
Builds on: ADR-0017 (clone), ADR-0023 (comments; `VERSION = 2`, no migration),
ADR-0024 (participant roster sidecar).
Amends: ADR-0023, ADR-0024, CONTRACT §11.

## Problem

`clone_id = sha256(f"{account_user_id}:{source_peer_id}")` is deterministic, so
one source peer maps to exactly one state-file slot,
`TGCLI_STATE_DIR/clones/<clone_id>.json`, forever. `commit_init`
(`src/tgcli/commands/clone.py`) opens that slot with `state.load(clone_id)`, and
`state.load` (`src/tgcli/clone/state.py`) is fail-closed: a file whose `version`
!= `VERSION` (2) raises `PolicyError` before any other work.

Consequence: once a pre-round-3 **v1** state file exists for a source, `clone
init --commit` can never re-create that clone — the version check fires first and
there is no write path that supersedes the slot. ADR-0023 and CONTRACT §11 both
document the comments-upgrade path as "a fresh `init` against a new destination",
but that is physically impossible here:

- A **v1** file makes `state.load` raise, so commit aborts. (Confirmed live
  2026-07-17: the @sral_v_nastav gate had to `mv <clone_id>.json{,.v1bak}` by
  hand before init would proceed.)
- A readable **v2 posts-only** file (`comments: "none"`/`"unavailable"`) is
  loaded and its recorded `destination_peer_id` is *reused*, so a re-run adopts
  the same anchor-less destination instead of a new pair — still no comments.

This is distinct from the shipped `clone status` fix (72e8f6f), which only
tolerates unreadable files while **listing**; the **write** path is unchanged.

Fail-closed loading is correct and must stay: a v1 destination must never be
silently reused as if it were a v2 clone. What is missing is an *explicit* way to
retire the old clone and start clean.

## Decision

Add an opt-in `clone init --replace` flag that supersedes the stale clone by
archiving its on-disk artifacts (never deleting) and starting a fresh v2 clone
against a brand-new destination pair. Without the flag, behaviour is unchanged
and fail-closed.

Rejected alternative — a documented manual `mv`/`rm` eviction step — is what the
gate already did by hand: undiscoverable, error-prone, untestable, and not a
product path. `--replace` performs the same archival under the preview→commit +
mutation gate, and is what the manual step becomes "under the hood".

### Command surface

```text
tg clone init SOURCE --replace
tg clone init SOURCE --replace --commit PREVIEW_ID
```

`--replace` is a boolean flag on the `init` subparser. Intent is captured at
**preview** time and carried in the single-use preview payload, so the preview
response announces the supersede and `--commit` executes it from the payload —
`--replace` need not (and is ignored if) re-passed at commit, matching how
`source_kind` already flows through the payload.

### Preview (`preview_init`, stays read-only)

Adds a `supersede` object, computed without triggering fail-close:

```json
"supersede": {"existing": true, "readable": false, "replace": true}
```

- `existing`: `state.path_for(clone_id).exists()`.
- `readable`: `state.load` under `try/except PolicyError` → `true`/`false`, or
  `null` when `existing` is `false`. Preview never aborts on a v1 file (unlike
  commit).
- `replace`: echoes the flag; stored in the payload.

No Telegram mutation and no state write happen in preview.

### Commit (`commit_init`)

When the payload's `replace` is truthy, before `state.load`:

1. `state.supersede(clone_id)` — a pure filesystem helper in `state.py`.
   Atomically `os.replace`-renames, if present:
   - `<clone_id>.json` → `<clone_id>.json.superseded-<UTC-compact>`
   - `<clone_id>-participants.jsonl` → `<clone_id>-participants.jsonl.superseded-<UTC-compact>`
     (the ADR-0024 roster sidecar)
   Both share one timestamp. Returns the list of archived paths (`[]` if the slot
   was empty — `--replace` on a fresh source is a harmless no-op). **Rename, not
   delete**: the old state and roster stay recoverable, and the old destination
   channel/group in Telegram is never touched.
2. Append audit `clone-init-replace` (`clone_id`, archived basenames) before the
   fresh state is written.
3. `state.load(clone_id)` now returns `None` → `CloneState.new(...)`.
4. **Nonce marker.** The fresh state's `creation_marker` is set to
   `f"tgcli-clone-{clone_id[:12]}-{secrets.token_hex(3)}"` (persisted). The base
   marker is deterministic from `clone_id`; an init interrupted by FLOOD_WAIT
   before the retitle step (ADR-0023 documents this as the *normal* outcome of
   two-peer creation) leaves the old destination still bearing that deterministic
   marker, which the marker-scan would re-adopt — re-entering the posts-only
   trap. The nonce makes `--replace` deterministically *create* a new pair
   instead of maybe-adopting the old one. Retries read the persisted nonce marker
   back, so crash recovery within a `--replace` init adopts the *new* pair, not a
   third.

When `replace` is falsy and `state.load` raises `PolicyError` on the version
check, catch it and re-raise a message that names the escape hatch:

```
clone state <clone_id>.json has unsupported version 1; re-run clone init --replace to supersede it
```

This preserves fail-closed behaviour (a v1 slot still refuses to start without
the flag; a v2 destination is never silently reused as v2) while turning the
dead end into an actionable one.

### CLI plumbing

- `p_clone_init.add_argument("--replace", action="store_true")`.
- `preview_init(tg, source, *, replace=args.replace)`.
- The preview-consumption block already verifies `payload["kind"] ==
  "clone-init"` and `payload["source"] == args.source`; the payload gains
  `replace`, read by `commit_init`.
- Mutation gating is unchanged: commit already runs under
  `enforce_mutation_allowed` + `mutation_safe`. Archival is part of the committed
  mutation.

## Testing (TDD)

Unit (`tests/test_clone_state.py`):
- `state.supersede` on a missing slot returns `[]`, writes nothing.
- On a present `<id>.json` (+ optional sidecar), renames both to
  `.superseded-*`, leaves the active slot empty, is idempotent-safe.

CLI/behaviour (`tests/test_cli_clone_init.py`):
- v1/unreadable slot, commit **without** `--replace` → exit 2, message contains
  `--replace`.
- v1 slot, commit **with** `--replace` → old file archived, fresh v2 clone
  created (destination created), `status: ready`, `comments` recomputed.
- Readable v2 posts-only slot with a recorded `destination_peer_id`, commit with
  `--replace` → old `destination_peer_id` is **not** reused; a new destination is
  created (nonce marker ⇒ no re-adopt).
- `--replace` on a source with no existing slot → behaves as a plain fresh init.
- preview response carries `supersede` with correct `existing`/`readable`/
  `replace`.
- roster sidecar present → archived alongside the state file.
- audit log gains a `clone-init-replace` record.

## Scope / non-goals

- No deletion of any state file or any Telegram peer; supersede is archival only.
- No auto-cleanup of `.superseded-*` files (left for the user; they are the
  recovery copy).
- No `--replace` for `sync` — sync already refuses an uninitialised/mismatched
  clone and is not a create path.
- No migration of v1 → v2 content (ADR-0023's no-migration stance stands;
  `--replace` starts clean, it does not port old data).

## Line-budget note

ADR-0023 records `commands/clone.py` at 449/460 and `state.py` at 189/190. The
archive logic lives in `state.py` as the pure `supersede` helper; `commit_init`
gains only the branch + clearer-error wrapping; `preview_init` gains the
`supersede` probe. Keep both files inside budget — golf if needed rather than
raising the budget.
