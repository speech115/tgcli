# Archive: local selected-dialog store

`tg archive` keeps a per-account, read-only Telegram archive on disk
([ADR-0068](../decisions/ADR-0068-local-archive-store.md)). The shipped surface includes
the store, scope, selected and private backfill, filtered/ranked offline
search, delta sync via the `tg changes` cursor, bounded voice/video-note
acquisition, foreground local Parakeet transcription, and offline timeline /
history views. Recurring foreground work is scheduled through typed
[`tg jobs`](jobs.md) lanes.

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

`list`, `status`, `search`, `read`, `history`, and `transcribe` are offline: they load config +
the local DB and do **not** open a Telegram session.

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
`--private` skips only dialogs whose backfill has recorded an end checkpoint
(`last_backfill_at` is set and `more: false`), so a delta-only peer is still
backfilled.
Groups/channels need `add` first; private dialogs do not. Each run is
checkpointed and resumable. Long `FLOOD_WAIT` exits 5 after checkpointing.
Media downloads are bounded separately from message acquisition; the message
checkpoint never advances by dropping a fetched tail.

Stored message bodies reuse the universal `tg read` JSON shape
(`message_to_dict`). Edits append revisions; deletions via sync become
tombstones. Backfill/sync persist peer identity on `sync_state` so offline
`search --chat @username` works for private peers.

## Sync and rebaseline

```bash
tg --json archive sync
tg --json archive sync --max-events 500 --max-dialogs 20 --max-media 50
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

## Media and transcription

Backfill and sync queue `voice` and `video_note` messages for media download
into the account-local `media/` directory. Backfill uses a fixed budget of 50
media items per run; sync exposes `--max-media`, which defaults to 50 and has
a hard cap of 500. Existing files are reused, and the transcript queue
records `media_path` only after a successful publish. Media acquisition has
three attempts independent of transcription attempts; failures are marked
`media_status: "retryable"` and then terminal `media_status: "no_media"`.
A successful later publish resets the media counter and marks it `done`.
A `FLOOD_WAIT` leaves the item retryable, exits 5, and arms the shared
account cooldown.

After media is available, drain the local Parakeet queue in a separate
foreground invocation:

```bash
tg --json archive transcribe
tg --json archive transcribe --limit 20 --max-attempts 3
tg --plain archive transcribe --limit 5
```

The command runs the local `transcribe` executable (FluidAudio/Parakeet),
newest media first. Successful rows store transcript text, engine, and model
version in SQLite and are searchable through the existing FTS index. A
retryable failure stays queued until the attempt cap; terminal or exhausted
rows become `no_transcript` with an error for status/reporting. Transcripts
survive message edits and FTS rebuilds.

## Search (offline)

```bash
tg --json archive search "query"
tg --json archive search "хакатон*" --chat @alice --from @alice --limit 20
tg --json archive search "needle" --transcripts-only --since 2026-01-01
tg --json archive search "query" --kind voice --sort date --page 2 --limit 50
tg --plain archive search "елка"
```

Exact FTS5 `MATCH` by default (no auto-prefix). Include `*` (or other FTS
operators) for a raw MATCH escape hatch. Default limit is 20 and the hard cap
is 50 rows per page. `--page` is 1-based and returns `has_more` / `next_page`.
Relevance uses FTS5 BM25 with message date as the recency tiebreak; use
`--sort date` for newest-first ordering. `--chat` resolves from `scope` or
private `sync_state` identity. `--from` accepts a sender id, `@username`, or
stored sender name. `--since` / `--until` are inclusive; `--kind` accepts
`text`, `photo`, `video`, `video_note`, `audio`, `voice`, and `document`.
`--transcripts-only` searches successful transcript text only. Each hit keeps
the stored transcript/status, a marked snippet, the stored HTTPS `permalink`
when available, and a `tg_link` for handing the id to live `tg read` tooling.
The link uses Telegram's user, public-message, or private-message form based
on the archived peer; basic groups without a public username use the internal
`openmessage?chat_id=...` fallback.
Results cover archived peers only; JSON `scope.stale` is true when any dialog
still has `more: true` on Telegram. Allowed under `--readonly`.

## Offline timeline and history

```bash
tg --json archive read @alice --around-id 42 --limit 20
tg --json archive read @alice --around-date 2026-01-10 --since 2026-01-01
tg --plain archive read @alice --until 2026-01-31
tg --json archive history @alice 42
```

`archive read` never opens Telegram. Without a center it returns the newest
bounded local window in chronological order. `--around-id` centers on a stored
message id; `--around-date` centers on a date/datetime. `--since` and `--until`
further bound the window. The returned messages keep the universal `tg read`
shape and add `peer_id`, transcript fields, and `tg_link`.

`archive history` exposes the current local body, append-only revisions, and a
tombstone when sync observed a deletion. A tombstoned message reports
`status: "deleted"`; historical bodies remain available. Unknown chats and
message ids with no local record return exit 4. Both commands are read-only
and safe under `--readonly`.

## Status and hygiene

```bash
tg --json archive status
tg --json store stats
```

`status` reports message/revision/tombstone counts, per-dialog freshness,
gap/cursor state, reconcile sample, transcript queue depth/status/errors, last
errors, and media retry state.
`store stats` inventories archive bytes under
the state root; `store cleanup` never deletes anything under the archive
root.

## See also

- [CONTRACT.md §13](../CONTRACT.md) — flags, caps, JSON shapes, exit codes
- [store.md](store.md) — local state inventory and cleanup boundary
- [changes.md](changes.md) — delta cursor reused by `archive sync`
- [jobs.md](jobs.md) — recurring archive sync and local transcription
- [ADR-0068](../decisions/ADR-0068-local-archive-store.md)
- [ADR-0087](../decisions/ADR-0087-foreground-persisted-jobs.md)
