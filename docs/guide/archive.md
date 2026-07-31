# Archive: local selected-dialog store

`tg archive` keeps a per-account, read-only Telegram archive on disk
([ADR-0068](../decisions/ADR-0068-local-archive-store.md)). Phase 1 ships
the store, scope, and selected-dialog backfill. Search, delta sync,
transcription, and hourly refresh come later.

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

`list` and `status` are offline: they load config + the local DB and do
**not** open a Telegram session.

## Backfill

```bash
tg --json archive backfill @alice --limit 100
tg --json archive backfill @alice @channel --limit 200
```

At least one `CHAT` is required — there is no empty→all sentinel. Default
limit is 100 messages per dialog; hard caps are 1000 messages/dialog and
20 dialogs per invocation. Groups/channels need `add` first; private
dialogs do not. Each run is checkpointed and resumable: a later call
continues older history from the stored oldest id.

Stored message bodies reuse the universal `tg read` JSON shape
(`message_to_dict`). Edits append revisions; deletions (later sync) become
tombstones.

## Status and hygiene

```bash
tg --json archive status
tg --json store stats
```

`status` reports message/revision/tombstone counts, per-dialog freshness,
transcript queue depth (empty until Phase 4), and last errors. `store
stats` inventories archive bytes under the state root; `store cleanup`
never deletes anything under the archive root.

## See also

- [CONTRACT.md §13](../CONTRACT.md) — flags, caps, JSON shapes, exit codes
- [store.md](store.md) — local state inventory and cleanup boundary
- [ADR-0068](../decisions/ADR-0068-local-archive-store.md)
