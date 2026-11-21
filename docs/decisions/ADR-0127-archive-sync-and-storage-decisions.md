# ADR-0127: Archive sync and storage decisions

Status: Accepted

## Context

These decisions about the local archive (`tgcli.archive`) and `tg export` used to live as code comments beside the
code they explain. ADR-0132 removes comments from code, so the non-obvious
ones move here, grouped by module and by the function, class or name they
sit on. Each entry is the reason for a choice that the code alone does not
show; the code is the authority for what it does.

## Decision

### `src/tgcli/archive/messages.py`

- `find_message_peers`: Marked channel/supergroup ids are <= -10**12 (-100XXXXXXXXXX).

### `src/tgcli/archive/schema.py`

- `ensure_account_dir`: root itself may be a custom path outside state_dir; still force 0700.

### `src/tgcli/archive/sync.py`

- `apply_events`: Difference may deliver an edit as message_new with a new body; upsert_message already appended a revision for the prior payload.
- `_catch_up_peer`: With a stored `newest_id`, read oldest first: a page cut short by the limit or a flood ends at a message whose predecessors are all stored, so `newest_id` never skips past a hole. A peer with no history yet keeps its newest page; backfill owns older messages.
- `_catch_up_peer`: `more` is backfill's: older history remains. A catch-up reads forward and never learns that.
- `_catch_up_peer`: The account can no longer read this channel: keep what was stored and drop the catch-up instead of failing every later sync on it.
- `fetch_media`: Another process is downloading this very item: it owns the checkpoint, and nothing was learned about the file. Leave the row queued without spending an attempt.
- `fetch_media`: A lost connection, a dead session: nothing was learned about this item, so no attempt is recorded.
- `fetch_media`: Telegram failing, not the file: stop the pass, keep the row's attempts, and let the next run take it again.
- `sync_archive`: Catch-ups an earlier run left unfinished go first, then this pass's activity and recovery; whatever does not finish is persisted for the next run, so `remaining: true` stays true until every chat is caught up.
- `sync_archive`: Checkpoint before the media tail: its queue lives in the database, so a media rate limit (exit 5) must not make the next run re-apply this difference. A gap stays recorded until no catch-up is left.

### `src/tgcli/archive/sync_state.py`

- `write_account_sync`: Add to, never replace, the gaps a recovery has not closed yet; a repeat of a stored gap moves it to the end instead.
- `write_account_sync`: Column names come from the fixed keys above, never from input.

### `src/tgcli/commands/export.py`

- `export_messages`: A takeout session lifts the history limits, which is what it is for: its pages need no gap.

## Consequences

A change that breaks one of these reasons updates or removes its entry in the
same PR. A new decision of this kind is added here, not as a code comment.
