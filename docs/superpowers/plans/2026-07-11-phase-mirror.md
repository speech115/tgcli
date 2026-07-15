# Phase Mirror Implementation Plan (M0-M4)

> **SUPERSEDED (2026-07-15) by [ADR-0017](../../decisions/ADR-0017-clone-supersedes-mirror.md) and the [clone design spec](../specs/2026-07-15-clone-design.md).** This plan documents the old `tg mirror` feature, which is being replaced by `tg clone`. It is kept as history — do NOT execute it. Mirror code is frozen in the tree only as a transplant donor for clone.


> **Execution:** use `superpowers:subagent-driven-development` or
> `superpowers:executing-plans` task by task. Do not skip the two M0 hard gates.

**Governing decision:** [ADR-0013](../../decisions/ADR-0013-channel-mirror.md).

**Goal:** build a crash-safe, append-only copy of a Telegram broadcast channel
in a private owner-only destination, with chronological backfill, supported
media fidelity, threaded comments, authoritative change audit, and a visible
foreground watcher that does not block normal tgcli commands.

## Architecture

Ownership is deliberately split so network effects and durable state can be
tested separately:

```text
cli.py
  -> commands/mirror.py       command facade and contract-shaped results
      -> mirror/service.py    create/backfill/sync orchestration
      -> mirror/watch.py      foreground event loop and catch-up barrier
      -> mirror/outbox.py     durable sends and recovery
      -> mirror/ledger.py     schema, transactions, migrations, queries
      -> mirror/render.py     pure source-to-destination representation
```

`session.py` continues to own TelegramClient lifecycle and file locks.
`safety.py` continues to own kill switches and the global audit journal.
Command and mirror modules never print; progress goes through a `note()` callback
to stderr, and final contract data goes through `output.emit()`.

## Frozen decisions

- Foreground only: no fork, daemon, LaunchAgent installer, socket, or service
  manager.
- The watcher uses `sessions/<session>.mirror-watch.session`; normal commands
  retain the primary session and lock.
- Mirror identity is derived from `(account_user_id, source_peer_id)` after
  source resolution.
- All message keys are `(peer_id, message_id)`, never message id alone.
- Every outbound write uses a durable operation and persisted Telegram
  `random_id` before network dispatch.
- Copied messages are never edited or deleted. Source edits/deletions append new
  changelog messages or replies.
- Audit records deletion only after a complete scan and targeted confirmation.
- M0's protected-content gate is closed by R0 evidence (2026-07-11, see
  DEVLOG): Telethon download/reupload has complete byte access on real
  protected channels for both owner and subscriber roles. No gotd backend is
  needed. Copy method is capability-based: native server-side copy for
  unprotected sources, Telethon download/reupload for protected sources.
- Shared tgcli audit/session/invocation files remain in their existing state
  paths. Mirror-specific state alone is constrained to `mirrors/`.

## Required checks for every implementation commit

1. Start with a focused failing test and quote the failing command in DEVLOG.
2. Implement the minimum behavior needed for that test.
3. Run the focused test, then `.venv/bin/pytest -q`.
4. Update CONTRACT/MAP/FEATURES in the same commit whenever their truth changes.
5. Use `safe-commit` with an explicit file list. Never stage unrelated changes.

---

## M0 - Feasibility gates

M0 performs no Telegram mutations and creates no destination channel. Both gates
must be green before M1 begins. Evidence goes in DEVLOG with account aliases and
peer ids redacted where appropriate.

### Task 0.1: Prove dedicated watcher-session concurrency

**Files:**

- Create: `scripts/mirror_session_probe.py`
- Create: `tests/test_mirror_session_probe.py`
- Modify: `docs/DEVLOG.md`

- [ ] Write a failing test for a local SQLite-backup helper: it copies a closed
  primary Telethon session to a temporary watcher path without modifying the
  source and refuses to overwrite an existing destination.
- [ ] Implement the probe locally, using SQLite backup rather than filesystem
  copy.
- [ ] With `TGCLI_LIVE_SMOKE=1`, open the watcher copy and the primary session at
  the same time, verify both `get_me().id` values match, and execute a read-only
  dialog fetch through each client.
- [ ] Hold the watcher client open while running installed `tg --json dialogs
  --account <account> --limit 1`; it must exit 0 rather than busy.
- [ ] Record exact exit codes and same-user/concurrent-read verdict in DEVLOG.
- [ ] If any concurrency or authorization check fails, stop for a new session
  topology ADR. Do not make watch use the primary long-held lock.

Verification:

```text
.venv/bin/pytest tests/test_mirror_session_probe.py -q
.venv/bin/pytest -q
TGCLI_LIVE_SMOKE=1 .venv/bin/python scripts/mirror_session_probe.py ...
```

Commit only after GREEN:

```text
safe-commit "Prove mirror watcher session concurrency" scripts/mirror_session_probe.py tests/test_mirror_session_probe.py docs/DEVLOG.md
```

### Task 0.2: Prove protected-content capability matrix

**DONE via R0 evidence (2026-07-11, see DEVLOG).**

**Files:**

- Create: `scripts/mirror_content_probe.py`
- Create: `tests/test_mirror_content_probe.py`
- Modify: `docs/DEVLOG.md`

The probe receives explicit message ids for:

- a non-empty text post;
- a photo;
- a document or video;
- every member of one album;
- a media item large enough to read multiple chunks.

It records source message/media class, text length, declared size, received byte
count, SHA-256 of sampled/full bytes as configured, grouped ids, and controlled
errors. It writes diagnostics to stderr and a small JSON verdict to stdout. It
does not persist downloaded media after the probe.

`scripts/mirror_probe.py` (commit 7de7c90) ran live against two real
protected broadcast channels — one owner, one ordinary subscriber. Every
byte-bearing capability row returned Telethon `pass` for both roles; zero
`fail`/`inconclusive`. Verdict: GREEN, gotd path not needed. Full record in
DEVLOG, "R0 protected-content probe evidence (2026-07-11)".

- [x] Test verdict aggregation: every required row must be green; one unsupported
  or zero-byte row makes the overall verdict red.
- [x] Test cleanup on success, exception, and SIGINT.
- [x] Run against a real `noforwards` channel through the pinned Telethon client.
- [x] Record the capability matrix in DEVLOG.
- [x] On RED, stop. A raw TL experiment is allowed inside the diagnostic only if
  the parsed message supplies a valid `InputFileLocation`. Do not route the test
  through `tg api upload.getFile`, which the public raw contract excludes.
  (Not triggered — verdict was GREEN.)
- [x] If raw Telethon cannot recover the content, invoke ADR-0009's measured
  backend PoC decision or narrow scope in a revised ADR with user approval.
  (Not triggered — Telethon alone had complete byte access; no gotd path was
  needed.)

Commit only after GREEN or a documented stop decision:

```text
safe-commit "Probe protected mirror content" scripts/mirror_content_probe.py tests/test_mirror_content_probe.py docs/DEVLOG.md
```

---

## M1 - Durable identity, ledger, session initialization, and destination creation

### Task 1.1: Ledger schema and peer-scoped contracts

**Files:**

- Create: `src/tgcli/mirror/__init__.py`
- Create: `src/tgcli/mirror/ledger.py`
- Create: `tests/mirror/test_ledger.py`
- Modify: `docs/MAP.md`

Schema version 1:

```sql
mirror(
  mirror_id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL,
  account_alias TEXT NOT NULL,
  account_user_id INTEGER NOT NULL,
  source_peer_id INTEGER NOT NULL,
  source_access_hash INTEGER,
  source_ref TEXT NOT NULL,
  dest_peer_id INTEGER,
  linked_source_peer_id INTEGER,
  linked_dest_peer_id INTEGER,
  lifecycle TEXT NOT NULL,
  authorization_version INTEGER,
  authorized_at TEXT,
  last_confirmed_id INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  UNIQUE(account_user_id, source_peer_id)
);

message_map(
  source_peer_id INTEGER NOT NULL,
  source_msg_id INTEGER NOT NULL,
  dest_peer_id INTEGER NOT NULL,
  dest_msg_id INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  kind TEXT NOT NULL,
  parent_source_peer_id INTEGER,
  parent_source_msg_id INTEGER,
  copied_at TEXT NOT NULL,
  PRIMARY KEY(source_peer_id, source_msg_id)
);

operation(
  operation_id TEXT PRIMARY KEY,
  logical_key TEXT NOT NULL UNIQUE,
  source_peer_id INTEGER,
  source_msg_id INTEGER,
  target_peer_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  random_id INTEGER NOT NULL,
  state TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  dest_ids_json TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

changelog(
  source_peer_id INTEGER NOT NULL,
  source_msg_id INTEGER NOT NULL,
  event TEXT NOT NULL,
  old_hash TEXT,
  new_hash TEXT,
  snapshot_json TEXT,
  detected_at TEXT NOT NULL,
  detected_by TEXT NOT NULL,
  annotation_operation_id TEXT,
  UNIQUE(source_peer_id, source_msg_id, event, new_hash)
);

scan(
  scan_id TEXT PRIMARY KEY,
  source_peer_id INTEGER NOT NULL,
  from_id INTEGER NOT NULL,
  to_id INTEGER NOT NULL,
  state TEXT NOT NULL,
  checked_count INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  error TEXT
);
```

- [ ] Test stable identity after resolving aliases to the same canonical peer.
- [ ] Test that two accounts mirroring the same peer do not collide.
- [ ] Test that channel message `42` and linked-group message `42` coexist.
- [ ] Test schema creation, foreign invariants, transaction rollback, WAL mode,
  busy timeout, and rejection of unknown schema versions.
- [ ] Add mirror package ownership to MAP immediately.

### Task 1.2: Durable outbox state machine

**Files:**

- Create: `src/tgcli/mirror/outbox.py`
- Create: `tests/mirror/test_outbox.py`
- Modify: `src/tgcli/safety.py`, `tests/test_safety.py`

Interfaces:

- `prepare_operation(...) -> Operation`
- `dispatch_operation(tg, ledger, operation) -> Operation`
- `recover_operations(tg, ledger) -> RecoveryResult`

- [ ] Test legal transitions and fail closed on illegal ones.
- [ ] Test that operation and stable signed 64-bit `random_id` are committed
  before the fake network dispatcher is called.
- [ ] Use raw `SendMessageRequest`, `SendMediaRequest`, and
  `SendMultiMediaRequest`; do not use a helper that hides random ids.
- [ ] Inject crashes before dispatch, after dispatch/before response, after
  response/before mapping, and after mapping/before cursor advancement.
- [ ] Prove recovery reuses the same random id and produces one destination
  mapping.
- [ ] Extend audit with a correlation id and phases `attempt`, `confirmed`, and
  `failed`; preserve ADR-0011's pre-dispatch fail-closed behavior.

No live write is needed yet: use a deterministic fake dispatcher that models
Telegram random-id deduplication.

### Task 1.3: Explicit watcher-session initialization

**Files:**

- Modify: `src/tgcli/session.py`, `tests/test_session.py`
- Create: `src/tgcli/commands/mirror.py`, `tests/test_cli_mirror.py`
- Modify: `src/tgcli/cli.py`, `docs/CONTRACT.md`, `docs/MAP.md`

- [ ] Add a role-aware session path and lock for `mirror-watch` without changing
  existing primary-session behavior.
- [ ] Add `tg mirror watch --account <account> --init-session` as an explicit
  local-only action. It must refuse overwrite and require both session locks.
- [ ] Verify same Telegram user on first live connection; remove the watcher copy
  and fail if verification is definitive and mismatched.
- [ ] Test ordinary `watch` fails exit 3 when the watcher session is absent.
- [ ] Document JSON/plain result and errors in CONTRACT.

### Task 1.4: Resumable destination creation and authorization

**Files:**

- Create: `src/tgcli/mirror/service.py`
- Create: `tests/mirror/test_service_create.py`
- Modify: `src/tgcli/commands/mirror.py`, `src/tgcli/cli.py`
- Modify: `tests/test_cli_mirror.py`, `docs/CONTRACT.md`

Lifecycle:

```text
planned -> channel_created -> profile_synced -> discussion_created
        -> discussion_linked -> authorized
```

- [ ] Resolve source and account user id before deriving mirror id.
- [ ] Verify source is a broadcast channel; reject general groups/DMs.
- [ ] `create` without `--commit` returns a non-mutating plan including canonical
  source id, intended private destination, profile/comment capabilities, and
  authorization scope.
- [ ] `create --commit` creates the ledger first, audits each mutation, persists
  destination id immediately, and resumes missing lifecycle steps on retry.
- [ ] Inject failure after every lifecycle step and prove a retry creates no
  second channel or linked group.
- [ ] Before authorization, fetch destination state and prove creator ownership,
  private/no username, and no exported invite link created by tgcli.
- [ ] Bind authorization to account user id, source/destination ids, linked peer,
  and schema version.
- [ ] Add `tg mirror list` and `status`; expose lifecycle and blocked operations.

Live acceptance uses a designated test source/destination and records created
peer ids in DEVLOG without publishing them.

---

## M2 - Rendering and resumable backfill

### Task 2.1: Pure renderer and explicit capability policy

**Files:**

- Create: `src/tgcli/mirror/render.py`
- Create: `tests/mirror/test_render.py`
- Modify: `docs/CONTRACT.md`

Define representations before network code:

- date prefix and escaping;
- UTF-16 entity offsets after prefix insertion;
- text/caption limits and deterministic overflow messages;
- albums with one caption and constituent mappings;
- replies when parent exists and explicit fallback when it does not;
- photo/document/video/voice note/video note/sticker/web preview/poll snapshot;
- unsupported, paid, expired, or service content placeholders.

- [ ] Table-test every supported media kind and Telegram length boundary.
- [ ] Test formatting entities, spoilers, custom emoji fallback, and links.
- [ ] Test album order and one-to-many destination ids.
- [ ] Test no source item disappears silently: every input yields content or an
  explicit unsupported record.
- [ ] Hash normalized semantic content, not transient remote file ids alone.

### Task 2.2: Text-only backfill through the durable outbox

**Files:**

- Modify: `src/tgcli/mirror/service.py`, `src/tgcli/mirror/outbox.py`
- Create: `tests/mirror/test_backfill_text.py`

- [ ] Iterate oldest to newest through takeout and peer-scoped keys.
- [ ] Prepare and confirm one operation before advancing `last_confirmed_id`.
- [ ] Re-run after every injected crash boundary and prove one destination
  message per source message.
- [ ] Handle short FloodWait by sleeping and long FloodWait as exit 5 with
  `retry_after`; do not mark an ambiguous operation failed.
- [ ] Check kill switches at start and between batches.

### Task 2.3: Media, albums, replies, pins, and comments

**Files:**

- Modify: `src/tgcli/mirror/service.py`, `src/tgcli/mirror/outbox.py`
- Create: `tests/mirror/test_backfill_media.py`
- Modify: `src/tgcli/commands/mirror.py`, `src/tgcli/cli.py`
- Modify: `tests/test_cli_mirror.py`, `docs/CONTRACT.md`

- [ ] Download into a per-operation temporary directory under
  `TGCLI_STATE_DIR/mirrors/<mirror_id>/tmp/` and clean only confirmed or safely
  retryable artifacts.
- [ ] Upload media, then dispatch raw durable requests with persisted random ids.
- [ ] Albums use constituent logical keys plus one group operation and map every
  source constituent.
- [ ] Replies resolve peer-scoped parent mappings. Missing parents use the frozen
  renderer fallback, never an unrelated numeric id.
- [ ] Comments target the linked destination and use the copied channel post as
  reply root; author is rendered as data because tgcli cannot impersonate them.
- [ ] Pins are applied only after their destination mappings exist and are their
  own audited durable operations.
- [ ] Add `tg mirror backfill <mirror>` with progress on stderr and one final
  JSON/plain result on stdout.

Live acceptance interrupts a media backfill at a controlled boundary, reruns it,
and verifies destination ids contain no duplicate logical operations.

---

## M3 - Race-free foreground watch

### Task 3.1: Buffered catch-up barrier

**Files:**

- Create: `src/tgcli/mirror/watch.py`
- Create: `tests/mirror/test_watch.py`

- [ ] Register source-filtered new/edit/delete handlers before reading the
  high-water mark.
- [ ] Buffer events while catching up `(last_confirmed, high_water]`.
- [ ] Drain buffered events in source order and deduplicate through logical
  operation/source keys.
- [ ] Test an event arriving before high-water read, during catch-up, after
  catch-up, and twice.
- [ ] Advance high-water only per confirmed durable operation.
- [ ] Test SIGINT, cancellation, disconnect, and reconnect. Correctness must not
  depend on SIGINT being delivered.

### Task 3.2: Watch CLI and multi-mirror routing

**Files:**

- Modify: `src/tgcli/commands/mirror.py`, `src/tgcli/cli.py`
- Modify: `tests/test_cli_mirror.py`, `docs/CONTRACT.md`

- [ ] `tg mirror watch --account <account>` opens only the dedicated watcher
  session and services all authorized mirrors owned by that Telegram user.
- [ ] Validate destination authorization/private ownership before the first
  mutation and after reconnect.
- [ ] One mirror failure is isolated and reported without stopping healthy
  mirrors; authorization loss stops only the affected mirror.
- [ ] While watch is live, run a normal installed read command using the primary
  session and prove both succeed.
- [ ] Live smoke: insert a source post during the catch-up window and prove it
  appears exactly once within the declared latency budget.

---

## M4 - Authoritative edit/delete audit and closeout

### Task 4.1: Complete-range scan and targeted confirmation

**Files:**

- Modify: `src/tgcli/mirror/service.py`, `src/tgcli/mirror/ledger.py`
- Create: `tests/mirror/test_audit.py`

- [ ] A scan starts `running` and becomes `complete` only after the entire
  requested range finishes without access, FloodWait, cancellation, or iterator
  errors.
- [ ] An incomplete scan produces no deletion facts.
- [ ] A mapped id missing from a complete range receives a targeted exact-id
  fetch; deletion is recorded only when that fetch confirms absence and readable
  neighboring controls rule out broad access failure.
- [ ] Content hash changes create one edit fact and one durable append-only
  annotation operation.
- [ ] Repeat live/audit observations are idempotent via the changelog unique key.
- [ ] Windowed scans report their proven range and never classify ids outside it.

### Task 4.2: Sync/diff CLI and documentation closeout

**Files:**

- Modify: `src/tgcli/commands/mirror.py`, `src/tgcli/cli.py`
- Modify: `tests/test_cli_mirror.py`
- Modify: `docs/CONTRACT.md`, `docs/MAP.md`, `docs/FEATURES.md`,
  `docs/PLAN.md`, `docs/DEVLOG.md`

- [ ] `tg mirror sync <mirror>` performs forward-fill only.
- [ ] `tg mirror sync <mirror> --audit` adds authoritative scan semantics.
- [ ] `tg mirror diff <mirror>` emits frozen changelog rows.
- [ ] `status` exposes lifecycle, last confirmed source id, prepared/dispatched/
  ambiguous/failed operation counts, last complete audit range, and degradation
  flags without leaking message text.
- [ ] CONTRACT freezes command forms, result shapes, stderr behavior, exit codes,
  and recovery guidance.
- [ ] MAP marks actual modules done; FEATURES marks `updates` wrapped and keeps raw
  `upload` excluded while documenting wrapped uploads; PLAN retains its historical
  banner and points to ADR-0013.
- [ ] Run full tests, coverage gate, `tg --help`, read-only live status/diff, and
  the crash-recovery acceptance suite.

---

## Acceptance gates

The phase is complete only when all are true:

- M0 proves concurrent primary/watcher sessions for the same Telegram user and
  the protected-content capability matrix.
- A watcher running for an account does not make a normal `tg` read command busy.
- Crash injection at every outbound transition and creation lifecycle boundary
  produces no duplicate destination channel, group, message, album, annotation,
  or pin operation.
- Channel and linked-group messages with the same numeric id coexist correctly.
- A post arriving during watcher startup appears exactly once.
- An interrupted or access-failed audit produces zero false deletions.
- A real source deletion while watch is down is confirmed by complete scan plus
  targeted fetch, then represented by one append-only changelog message.
- Destination private ownership and authorization binding are revalidated before
  writes and after reconnect.
- `pytest -q`, coverage gate, CLI help, and gated live smokes are green with real
  output quoted in DEVLOG.
- No self-installed daemon/background process exists; no mirror-specific state
  exists outside its ledger/temp directory; shared audit/session/invocation state
  remains in the canonical tgcli paths.
