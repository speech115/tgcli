# Research: tgcli-native archive coverage gap (export/changes/media)

Date: 2026-07-31
Issue: [#103](https://github.com/speech115/tgcli/issues/103) (child of the
wayfinder map, [#100](https://github.com/speech115/tgcli/issues/100); blocks
[#104](https://github.com/speech115/tgcli/issues/104))

## Question

How far do tgcli's existing surfaces (`export`, `changes`, `media`) already
get toward a local, read-only, incrementally-refreshed archive of all private
dialogs — with voice/video-note media and a future SQLite+FTS5 search layer
with transcription — and what exactly is missing?

All findings below are sourced from this repo's code and docs as of commit
`8b5c774` (branch point for `research/tgcli-archive-gap`). File:line
references are to that state.

## 1. `tg export messages` — cursor semantics and per-message JSON shape

Source: `src/tgcli/commands/export.py`, `docs/CONTRACT.md` §7, `docs/guide/export.md`.

- **Cursor model is sidecar-free and lives in the destination file itself.**
  `--resume` (`export.py:91-112`, `_resume_after_id`) reads the **last
  non-empty JSONL line** of `--output`, parses its `id`, and resumes as
  `--append --after-id <that id>`. There is no separate state file — a
  missing/empty/corrupt last line is exit 1, never a guessed restart point.
  `--after-id N` maps straight to Telethon's `min_id` (`export.py:135,
  142-149`); iteration is always oldest→newest (`reverse=True`).
- **Full-export atomicity vs. append mode differ.** Without `--append`/
  `--resume`, export writes to a sibling temp file and does one atomic
  `os.replace` only on full success (`_atomic_text_destination`,
  `export.py:26-46`) — a failed full export leaves the existing destination
  untouched. `--append`/`--resume` open the destination in `"a"` mode
  directly (`export.py:137-151`) and write incrementally, so a crash
  mid-append leaves a partial-but-valid-prefix JSONL file (the next
  `--resume` picks up from whatever the last complete line says).
- **Per-message JSON fields: export reuses the exact `tg read` message shape**
  via the shared `message_to_dict()` in `src/tgcli/commands/read.py:173-198`
  (imported at `export.py:11`), **not** a reduced shape. CONTRACT.md §7 and
  `guide/export.md` both undersell this by documenting only `id, date, from,
  text, media, reply_to` — the actual written object has every field `tg
  read`/`tg search` emit:
  `id, date, from{id,name,username}, text, media, media_info, voice_played,
  reply_to, quote_text, permalink, edited_at, outgoing, forwarded_from,
  reactions, custom_emoji, topic_id, grouped_id, is_service`.
  - **Edits**: `edited_at` is populated (`read.py:174,190`) — export captures
    the *current* edited state of each message at scan time, not edit
    history. There is no separate edit-event record in export output (that
    only exists via `tg changes`' `message_edit` events, see §2).
  - **Reply links**: `reply_to` is present, but export always calls
    `message_to_dict(message)` with **no `entity` argument**
    (`export.py:146, 159` — both omit the second positional), so
    `_reply_to(message, entity=None)` at `read.py:143-151` always takes the
    `entity is None` branch of `_reply_peer_matches_chat`
    (`read.py:125-140`, returns `True` when `entity is None`). **Every reply
    in an export collapses to a bare integer `reply_to` id**, even a
    cross-chat quote-reply that `tg read`/`tg search` would represent as
    `{"id": ..., "peer": ...}`. Same for `permalink` — `_permalink(entity,
    ...)` returns `None` whenever `entity is None` (`read.py:28-30`), so
    **every exported message has `permalink: null`**, unlike `tg read` output
    on the same chat.
  - **Media metadata**: `media` (Telethon class name) and `media_info`
    (`{name, mime, size, duration, width, height}`, `read.py:39-...`) are
    both present — file size/mime/duration survive, but there is no
    `video_note`/round-message flag anywhere in this shape (see §3).
  - **Formatting entities**: `text` is Telethon's rendered `.text` (falls
    back to raw `.message` for the `getChannelDifference`-patched-message
    edge case tgcli hit building `tg changes`, `read.py:160-170`) — this is
    markdown-ish plain text, not a structured entity array. The only
    structured entity data preserved is `custom_emoji` (offsets/lengths for
    premium emoji, `read.py:82-101`); bold/italic/spoiler/link entities are
    only implicitly present as markdown syntax inside `text`, not as
    recoverable structured data.
- **No dedicated `--all-private-dialogs` mode.** `export messages` targets one
  `<chat>` per invocation; a full-account private-dialog backfill is an
  agent-driven loop over `tg dialogs --kind user` results, one `export`
  (or `export --resume`) call per dialog. This matches ADR-0032's explicit
  design stance ("Large exports/downloads are agent-driven loops of capped
  invocations, not unbounded single commands").
- Exports have **no implicit timeout** (`CONTRACT.md:855-857`) — a large
  takeout can run past the normal 60s deadline; only an explicit `--timeout`
  bounds it. `TakeoutInitDelayError` is the one flood-control case, surfaced
  as exit 5 (`FLOOD_WAIT`) with `retry_after`.

## 2. `tg changes` — incremental all-private-dialogs refresh in one run

Source: `src/tgcli/commands/changes.py`, `src/tgcli/changes_cursor.py`,
`docs/decisions/ADR-0063-tg-changes-design.md`, `docs/CONTRACT.md` §12,
`docs/guide/changes.md`.

- **Yes — private dialogs need no enumeration or subscription.** Telegram's
  common `updates.getDifference` tier covers **all** private dialogs and
  basic groups in a single account-wide `pts`/`qts`/`date` state
  (`changes.md:16-18`, ADR-0063 "hybrid coverage"). `tg changes --cursor C`
  therefore returns `message_new`/`message_edit`/`message_delete` events for
  every private dialog in **one foreground call**, with no per-dialog polling
  and no explicit subscribe step — this is the strongest fit found for "all
  private dialogs, one run." Only channels/supergroups need explicit
  `--peer` subscriptions (baselined, no history replay); private dialogs
  "ride the common tier automatically."
- **Rate/FLOOD_WAIT posture is favorable and structurally different from
  export/media.** `changes.py` issues one `getDifference` call (plus one
  `getChannelDifference` per subscribed channel) per invocation
  (`_poll_common`/`_poll_channel`, `changes.py:244-362`) — there is no
  internal retry/backoff loop and no explicit `FloodWaitError` handling in
  the file (`grep` for `FloodWait`/`sleep`/`retry` only turns up the
  `--wait` **polling** sleep, `changes.py:63-69,460-482`, unrelated to flood
  control). A `FloodWaitError` from Telethon propagates through tgcli's
  standard error translation to exit 5 like any other command. Because one
  call drains the whole account's private-dialog delta, an incremental
  refresh loop (`init` once, then `--cursor` on a schedule) issues **O(1)
  Telegram requests per refresh**, not O(dialogs) — a materially better
  FLOOD_WAIT profile than looping `read --after-id` over every dialog, which
  is exactly the problem ADR-0063/FEED-001 was built to replace.
- **Gap handling is coarse for the "all private dialogs" case.** A common-tier
  gap (`differenceTooLong`) rebases the cursor but its `recover.creation`
  hint is generic — `"re-read chats of interest with: tg read CHAT
  --after-id N"` (`changes.py:233-241`) — it does **not** name which private
  dialogs actually changed during the gap window. An archive relying on
  `tg changes` for delta coverage must, on a common-scope gap, fall back to
  re-scanning **every tracked private dialog** (e.g. `export --resume` per
  dialog) rather than a targeted subset, since Telegram's difference API
  gives no cheaper hint here. `message_delete` is a tombstone (ids only, no
  body) and edit/delete history inside a gap window is explicitly lost
  (documented, not a bug).
- **No state files, cursor is caller-owned** (ADR-0002 posture) — an archive
  process must persist the opaque `v1:` cursor string itself between runs;
  tgcli stores nothing.
- `--wait N` long-polls and batches a burst behind one call
  (`changes.py:460-482`), useful for a "run right before syncing" pattern but
  not required for a scheduled one-shot refresh (can call without `--wait`
  and just take whatever is pending).

## 3. Bulk voice-note / video-circle download — coverage and idempotency

Source: `src/tgcli/commands/media.py`, `docs/CONTRACT.md` (media download /
manifest), `docs/guide/media.md`.

- **Bulk download exists and is capped, not unbounded.** `--message-ids`
  and/or `--type`/`--since`/`--limit` filters, hard cap **100 per invocation**
  (`BULK_DOWNLOAD_CAP = 100`, `media.py:365,381-384,408-424`), no `--all`.
  Per-item `NotFound` goes to `failed[]` and the loop continues;
  `FloodWaitError`/policy/auth errors stop the whole batch
  (`media.py:470-482`). A full-account voice/video-note backfill is
  therefore an agent-driven loop of ≤100-item calls advancing `--since` or
  explicit ids, same shape as export.
- **Video circles ("video notes") are not a distinguishable `--type`.**
  `MEDIA_KINDS = ("photo", "video", "audio", "voice", "document")`
  (`media.py:515`) has no `video_note` kind. `_media_kind()`
  (`media.py:518-531`) checks `message.video` before falling through — and
  Telethon's `Message.video` property (`.venv/…/telethon/tl/custom/message.py:600-605`)
  matches **any** `DocumentAttributeVideo`, round-message or not (only
  `Message.video_note`, unused anywhere in tgcli, applies the
  `round_message` filter). So a round video-circle message classifies as
  plain `"video"`, indistinguishable from a normal video upload — `--type
  video` in `media manifest`/`media download` bulk mode returns **both**
  video circles and regular videos with no way to filter one from the other,
  and `media_info` (`read.py`'s `_media_info`) carries no `round_message`
  flag either. Voice notes have no such problem — `voice` is checked before
  `audio` and Telethon's `.voice` property already discriminates on the
  `DocumentAttributeAudio.voice` flag (`media.py:525-527`,
  `message.py:592-598`).
- **Idempotency is refuse-not-skip.** `destination_for()` raises
  `PolicyError` (exit 2, `BLOCKED`) if the final path already exists
  (`media.py:75-81`) — bulk download does **not** silently skip
  already-downloaded media on a re-run; a naive repeated sync over the same
  message-id range fails per-item. Note: in bulk mode this `PolicyError` is
  actually a **hard stop** for the whole batch, since it lands in the broad
  `except (PolicyError, FloodWaitError)` branch (`media.py:472-475`), which
  sets `hard_error` and **breaks the loop** rather than doing a soft
  per-item skip. An archive sync must track what it already has locally
  (e.g., diff against a manifest or its own DB) and only request missing
  message ids — tgcli provides no "skip if present" bulk mode.
  Single-file resume (`~/.local/state/tgcli/downloads/`, keyed by a SHA-256
  of `chat:message_id`, `media.py:134-137`) is about resuming one
  **interrupted** transfer, a different concept from idempotent re-sync.
- Files land: single download default `~/Downloads/<filename>`; bulk default
  `~/Downloads`, or `--output DIR` for either. No archive-shaped destination
  layout (e.g., per-chat subdirectories) exists — `--output` is a flat
  directory per invocation.
- No implicit timeout on manifest/download (large scans/transfers may
  legitimately exceed 60s).

## 4. `clone/statedb.py` — reusable seam or clone-specific?

Source: `src/tgcli/clone/statedb.py`, `docs/decisions/ADR-0060-clone-state-sqlite-proposal.md`.

**Clone-specific, not a reusable archive-store seam** — but its *pattern* is
a solid precedent to imitate rather than code to import:

- Schema is hard-wired to one clone's bookkeeping: a single-row `meta` table
  (18 fixed columns — `source_peer_id`, `discussion_*`, `pin_occupied`, a
  cursor int, etc., `statedb.py:22-42,51-73`) plus four fixed
  `source→dest` int-to-int mapping tables (`id_map`,
  `discussion_id_map`, `topic_map`, `avatar_photo_ids`,
  `statedb.py:44-49,74-89`). There is no message-body table, no text column,
  and no `CREATE VIRTUAL TABLE …USING fts5` anywhere in the codebase
  (confirmed by repo-wide grep — zero FTS5 hits in `src/`). It maps a
  source message id to a destination message id for a copy operation; it
  cannot hold archived message content as-is.
- ADR-0060 explicitly scoped this as "clone state only... this is not a
  storage rewrite" and rejected "a general storage layer / ORM" as YAGNI —
  the design intent was never a generic store.
- **What is reusable as a pattern**: WAL + `synchronous=NORMAL` pragmas,
  `PRAGMA user_version` schema versioning with a hard version-mismatch
  fail-closed check, `PRAGMA integrity_check` surfaced through `clone
  status`-style reporting, one-time automatic JSON→SQLite import with the
  original kept as an `.imported` backup (never auto-deleted), sidecar
  (`-wal`/`-shm`) permission hardening (`_restrict_sidecars`,
  `statedb.py:275-286`), and an explicit rollback/export command
  (`persist`/`load_dict`/`archive_paths`). A new archive DB would be a
  **new module** following this same shape (own schema, own pragmas, own
  versioned migration path), not an extension of `clone/statedb.py`.
- `tg store stats` already tracks `clones/` SQLite files distinctly from
  other state (`.db`/`.db-wal`/`.db-shm` counted separately,
  CONTRACT.md §5.05) — an archive DB would need the same treatment added to
  `store stats`/`store cleanup` if it lived under `TGCLI_STATE_DIR`.

## 5. Concrete gap list for a SQLite+FTS5 archive with transcription

Everything below has **no existing tgcli command or internal seam**; all are
net-new work regardless of the sidecar-vs-native decision in #104:

1. **A message-body archive schema + FTS5 index.** No `messages` table, no
   virtual FTS5 table, anywhere in tgcli. `export`'s JSONL and `changes`'
   event JSON are both plausible *inputs* to build one, but nothing ingests
   them into a queryable local store today.
2. **A driver loop that turns `export`/`changes` into "archive all private
   dialogs."** Needs: enumerate private dialogs (`tg dialogs --kind user`),
   run `export messages --resume` per dialog for first backfill, persist a
   per-dialog "last exported id" (or rely on each dialog's own JSONL tail),
   then switch to `tg changes --cursor` polling for delta — plus the
   fallback full-rescan path for a common-scope gap (§2).
3. **Reply/permalink fidelity fix or a documented archive-side workaround.**
   Export's `entity=None` call collapses cross-chat quote-replies to a bare
   id and always nulls `permalink` (§1) — an archive that wants accurate
   reply graphs or clickable permalinks needs either an export-side code fix
   (pass `entity`) or its own re-resolution pass.
4. **A distinct video-circle (`video_note`) classification.** Neither
   `media manifest`'s `type` field nor `media_info` exposes
   `round_message`; today video circles are invisible as a category inside
   `--type video` results (§3). Needed before "voice/video-note media" can
   be selectively archived, filtered, or routed to transcription
   differently from ordinary video.
5. **Idempotent bulk media sync.** `media download` bulk mode hard-stops on
   an already-existing destination file (§3) instead of skipping; an
   archive-sync driver needs its own "have I already got message N's media"
   check before calling `media download`, or tgcli needs a new skip-existing
   mode.
6. **Edit-history capture, not just current-state.** `export`'s
   `edited_at` only reflects the message's state at scan time; `tg changes`'
   `message_edit` events are the only source of actual edit deltas, and only
   for events observed after a cursor baseline — there is no way to recover
   **historical** edit versions for messages that were edited before the
   archive's first backfill.
7. **Deletion tracking.** `message_delete` tombstones (ids only) exist only
   in the `tg changes` stream; `export` has no concept of "this id used to
   exist and is now gone" — an archive must diff against previously seen ids
   itself if it wants deletion awareness outside the `changes` cursor's live
   window, and gap windows silently lose delete visibility (§2).
8. **Transcription hook and its own storage.** No transcription integration
   exists in tgcli; per the wayfinder map (#100) this is local
   FluidAudio/Parakeet via the `transcribe` skill, external to tgcli. The
   archive needs its own place to store transcripts keyed to a
   voice/video-note message id, and a backfill queue (old media) plus a
   per-sync hook (new media) — both net-new.
9. **Archive-shaped media storage layout.** `media download`'s `--output`
   is a flat per-invocation directory; nothing in tgcli lays out a
   per-chat/per-date media tree suitable for a long-lived archive.
10. **`tg store` accounting for a new archive DB**, mirroring how `clones/`
    SQLite files are already broken out (§4), so `store stats`/`store
    cleanup` stay honest about a new class of local state.
11. **Formatting-entity fidelity for search/rendering**, if the archive wants
    more than plain-text FTS: only `custom_emoji` entities survive
    structurally today (§1); bold/italic/spoiler/link entities are only
    implicit in the rendered `text` markdown.
12. **Refresh scheduling itself** (launchd/cron invoking a one-shot sync) is
    explicitly out of tgcli's scope per the wayfinder map's no-daemon
    constraint — orchestration lives outside tgcli regardless of which
    gaps above get closed inside it.

## Bottom line

`tg export messages` (backfill, resumable, full `tg read`-shape rows minus
accurate reply-peer/permalink) and `tg changes` (one-call, whole-account,
private-dialog-covering incremental delta with a favorable FLOOD_WAIT
profile) together cover the **read/backfill/incremental-delta** axis
surprisingly well — better than a naive per-dialog polling loop would. `tg
media download` covers **bulk fetch** with real caps and resumability, but
not idempotent re-sync and not video-circle discrimination. None of the
three, nor `clone/statedb.py`, provide anything resembling a searchable
local store, an FTS5 index, transcription, edit-history retention, or
deletion tracking beyond the live `changes` cursor window — all of that is
net-new, regardless of whether #104 picks a telecrawl sidecar or a native
tgcli SQLite+FTS5 store.
