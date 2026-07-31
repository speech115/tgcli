# Archive: local selected-dialog store

`tg archive` keeps a per-account, read-only Telegram archive on disk
([ADR-0068](../decisions/ADR-0068-local-archive-store.md)). Phase 1–3 ship
the store, scope, selected and private backfill, thin offline search, and
delta sync via the `tg changes` cursor. Full Phase 5 filters, transcription,
and hourly refresh come later.

Default location: `~/.local/state/tgcli/archive/<alias>/archive.db`
(directories `0700`). Override with:

```toml
[archive]
root = "/path/to/archive-root"
```

## Initialize

```bash
tg --json archive init
```

Binds the live Telegram account id to the store. Re-running against the
same account is a no-op (`created: false`). A different live account id
against an existing store is exit 2 — stores are never merged.

## Scope

Private 1:1 dialogs are always in scope (standing category). Groups and
channels must be opted in:

```bash
tg --json archive add @channel
tg --json archive remove @channel
tg --json archive list
```

`list`, `status`, and `search` are offline: they load config + the local
DB and do **not** open a Telegram session.

## Backfill

```bash
tg --json archive backfill @alice --limit 100
tg --json archive backfill @alice @channel --limit 200
tg --json archive backfill --private --max-dialogs 20 --limit 100
```

Pass explicit `CHAT`s **or** `--private` (enumerate standing private 1:1
dialogs under `--max-dialogs`) — never both, and never an empty→all
sentinel. Default limit is 100 messages per dialog; hard caps are 1000
messages/dialog, 20 explicit chats, and 100 private dialogs per run.
`--private` skips dialogs already checkpointed with `more: false`.
Groups/channels need `add` first; private dialogs do not. Each run is
checkpointed and resumable. Long `FLOOD_WAIT` exits 5 after checkpointing.

Stored message bodies reuse the universal `tg read` JSON shape
(`message_to_dict`). Edits append revisions; deletions via sync become
tombstones. Backfill/sync persist peer identity on `sync_state` so offline
`search --chat @username` works for private peers.

## Sync and rebaseline

```bash
tg --json archive sync
tg --json archive sync --max-events 500 --max-dialogs 20
tg --json archive rebaseline
```

`sync` holds an account-level `tg changes` cursor, applies **every**
new/edit/delete event from the poll (no apply-side truncation — the cursor
advances only after a full apply), then runs scoped channel catch-up.
`--max-events` budgets catch-up message fetches; `--max-dialogs` caps how
many channels get catch-up in one run. `differenceTooLong`-class gaps are
recorded loudly in `status`. Private deletes without a peer may tombstone
every user/basic-group dialog that shares that numeric message id
(channel `-100…` peers are excluded). `rebaseline` is the explicit recovery
that re-inits the cursor and clears a stored gap — never silent. A rotating
local-vs-Telegram count sample is attached as `reconcile`.

## Search (thin / offline)

```bash
tg --json archive search "query"
tg --json archive search "хакатон*" --chat @alice --limit 20
tg --plain archive search "елка"
```

Exact FTS5 `MATCH` by default (no auto-prefix). Include `*` (or other FTS
operators) for a raw MATCH escape hatch. Default limit 20, hard cap 50.
Optional `--chat` resolves from `scope` or private `sync_state` identity.
Results cover archived peers only; JSON `scope.stale` is true when any
dialog still has `more: true` on Telegram. Allowed under `--readonly`.

## Status and hygiene

```bash
tg --json archive status
tg --json store stats
```

`status` reports message/revision/tombstone counts, per-dialog freshness,
gap/cursor state, reconcile sample, transcript queue depth (empty until
Phase 4), and last errors. `store stats` inventories archive bytes under
the state root; `store cleanup` never deletes anything under the archive
root.

## See also

- [CONTRACT.md §13](../CONTRACT.md) — flags, caps, JSON shapes, exit codes
- [store.md](store.md) — local state inventory and cleanup boundary
- [changes.md](changes.md) — delta cursor reused by `archive sync`
- [ADR-0068](../decisions/ADR-0068-local-archive-store.md)
