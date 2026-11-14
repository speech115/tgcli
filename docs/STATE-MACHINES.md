# State machines

tgcli is stateless per process, but almost everything it keeps on disk is a
state machine: a preview moves from `.json` to `.used`, a clone slot moves from
"planned" to "linked". This file names
each machine, its states, the invariants that must hold in every state, and
every transition the code performs, with the retry and concurrency story for
each.

It is a map, not the law. [CONTRACT.md](CONTRACT.md) says what users may rely
on, and the tests say what holds. When a fix changes a machine, update its
section here in the same PR. The last section lists every reachable invalid
state or transition found so far, ranked by impact, with its status.

Notation: `A → B` is a transition; *retry* means the same command run again
after a crash, a kill, a timeout, or an error exit; *concurrency* means two
`tg` processes, or two coroutines in one process, touching the same state.
Code references are `module:function`.

## Concurrency guards

Every machine below leans on one of these. A machine with no guard has to be
safe under interleaving on its own.

| Guard | Scope | Held by | On contention |
|---|---|---|---|
| `session.session_file_lock` (`<session>.lock`, flock) | one session file | every network command for its whole run | waits up to 120 s, then exit 3 |
| `safety._lock_pending` (flock on `<preview>.pending`) | one preview | a `--commit` from `begin_commit` to `finish_commit` or the end of its invocation (`cli.main`, so `tg-serve` frees it too) | exit 2, "commit already in progress" |
| `clone.state.slot_lock` (`clones/<id>.lock`) | one clone slot, all roles | `clone init --commit`, `clone sync` | exit 2, "clone … is busy" |
| `commands.archive._one_run_per_account` (`sync.lock`, `transcribe.lock`) | one archive account | `archive sync`, `rebaseline`, `transcribe` | exit 3 |
| SQLite WAL + `busy_timeout` 5 s | one database | every archive and clone write | writers serialize |
| `media.download_lock` (`downloads/<key>.lock`) | one media source | `media download`, archive media tails | exit 3; the archive leaves the row queued |

Two processes on the same account normally serialize on the session lock.
They run in parallel when they use different `--session-role` sessions
(ADR-0062), which is exactly how scheduled jobs are meant to run, so every
per-account machine must still hold up under real concurrency.

## 1. Invocation

One `tg` run: `cli.main`.

States: parsed → preflighted → executed → emitted → journaled.

| From | Event | To | Notes |
|---|---|---|---|
| start | argparse fails | journaled, exit 1 | `--json` still gets one error document |
| parsed | `preflight.prepare` raises (readonly gate, usage, `begin_commit`) | journaled, exit per error | no config or network yet |
| preflighted | handler returns | executed | network commands hold the session lock |
| executed | emit succeeds | journaled, exit 0 | |
| any | `TgcliError` | journaled, its exit code | |
| any | `--timeout` passes | journaled, `TIMEOUT` | |
| any | SIGTERM/SIGHUP | journaled `TERMINATED`, then dies by the signal | |

Invariant: exactly one journal row per parsed command (CONTRACT §9), and
stdout carries at most one contract document.

## 2. Preview → commit handshake

`safety.create_preview`, `begin_commit`, `finish_commit`;
`preview_commit.HANDSHAKES` drives it for `send`, `edit`, `delete`,
`forward`, `draft set`, `draft clear`, and `clone init`.

States of `previews/<id>.*`:

| State | File | Meaning |
|---|---|---|
| absent | none | never created, or reaped by `store cleanup` |
| fresh | `<id>.json`, `now < expires_at` | committable |
| expired | `<id>.json`, `now ≥ expires_at` | refused, reapable |
| pending | `<id>.pending` | a commit began; may be re-committed |
| used | `<id>.used` | the commit finished and its result audit persisted |

| From | Event | To | Code |
|---|---|---|---|
| absent | `--preview` | fresh | `create_preview` (atomic write, 5 min TTL) |
| fresh | `--commit`, kind matches, TTL ok | pending, flock held | `begin_commit` |
| fresh | TTL passes | expired | (time) |
| fresh/expired | kind mismatch | unchanged, exit 2 | `_validate_preview_kind` |
| pending | `--commit` again, no live holder, TTL ok | pending, flock held | `begin_commit` |
| pending | `--commit` while another process holds the flock | unchanged, exit 2 | `_lock_pending` |
| pending | `--commit` after TTL | expired (`.pending` → `.json`) | `_expire_pending` |
| pending | mutation and result audit succeed | used | `preview_commit.audit_after`, then `preview_commit.finish` → `finish_commit` |
| pending | mutation or audit fails | pending (flock released when the invocation ends) | `release_commit_locks` |
| used | `--commit` | unchanged, exit 2 | file not found |

Invariants:

- **H1.** At most one process runs a commit of a given preview at a time
  (flock plus the inode check in `_lock_pending`).
- **H2.** A preview reaches `used` only after its result audit is written;
  the before-audit is written before the mutation, and an unwritable audit
  blocks it.
- **H3.** Re-committing a `pending` preview is safe: every step a commit
  takes is idempotent, or keyed to the preview so it happens once.
  `send` and `forward` reuse the preview's `random_id` (Telegram answers
  `RANDOM_ID_DUPLICATE`, reported as `already_sent`); `edit` treats
  `MESSAGE_NOT_MODIFIED` as success; `delete` and drafts are naturally
  idempotent; `clone init` re-adopts its own destination by marker, and its
  `--replace` supersedes only the state the preview saw (F1).
- **H4.** An expired preview never stays `pending`.

Retry outside the TTL is not covered: a new preview carries a new
`random_id`, so a send that was delivered but never confirmed can be sent
again. CONTRACT §3 documents that window.

## 3. Clone slot lifecycle (`clone init`)

`commands.clone._commit_slot` under `state.slot_lock`. The slot is
`clones/<clone_id>.db` (`clone.statedb`, SQLite/WAL).

States, in the order a commit moves through them; each is saved before the
Telegram step that depends on it, so a crash resumes from the last one:

| State | Saved fields | Next step |
|---|---|---|
| empty | no `.db` | create state |
| marked | `creation_marker` (secret suffix) | create or adopt destination |
| destination | `destination_peer_id` | forum toggle, title, profile |
| titled | `destination_title`/`username` | copy avatar, about |
| comments decided | `comments` ∈ {`disabled`, `none`, `unavailable`, `enabled`} | link discussion (§4) |
| ready | `comments` final, `discussion_linked` when enabled | `clone sync` |

| From | Event | To |
|---|---|---|
| any | `init --replace` commit, slot holds the state the preview saw | slot archived as `*.superseded-<UTC>`, then empty |
| any | `init --replace` commit retried, slot holds the state an earlier attempt of *this* preview made | unchanged (resumes it) |
| empty | commit | marked → … → ready |
| marked | crash, then commit | marker search adopts the half-created destination |
| destination | crash, then commit | recorded destination re-resolved and re-validated |
| unreadable | commit without `--replace` | refused, exit 2, hint `--replace` |

Invariants:

- **S1.** One writer per slot across roles (`slot_lock`).
- **S2.** Every saved state loads: `state.load` rejects
  combinations such as `comments == "enabled"` on a non-broadcast source,
  phase-2 progress on a clone that is not copying comments, or a pin that is
  both set and occupied. `state.save` does not validate, so each writer must
  keep these by construction.
- **S3.** A destination is created at most once per clone. Before creation
  the marker is saved, so a crash after `CreateChannel` adopts the channel
  instead of creating another; `--replace` starts a fresh marker on purpose.
- **S4.** `--replace` supersedes the state that its preview showed, once.
  The preview records that state's identity (`created_at` and
  `creation_marker`, or "none", or "unreadable"); a retried commit that finds
  a different state in the slot resumes it instead of superseding again.

## 4. Clone comments mode

Fields `comments` and `discussion_linked`, with `discussion_source_peer_id`,
`discussion_destination_peer_id`, `discussion_cursor`,
`discussion_id_map`. Writers: `init_peers.init_discussion`,
`commands.clone._commit_slot`, `comments.sync_phase.degrade`.

| State | Meaning |
|---|---|
| `disabled` | the owner chose `--no-comments`; sticky |
| `none`, unlinked | source has no linked group |
| `none`, linked | the source unlinked its group after we linked ours: paused, progress kept |
| `enabled`, unlinked | group created or adopted, `SetDiscussionGroup` not done yet ("half-initialized") |
| `enabled`, linked | copying comments |
| `unavailable` | the source group refused us; progress cleared, never relinks |

| From | Event | To |
|---|---|---|
| new | `init --no-comments` | `disabled` |
| new/`none` | source has no linked group | `none` |
| `enabled`+linked | source unlinked, re-init | `none`+linked (paused) |
| `none`+linked | source relinks the same group, re-init | `enabled`+linked |
| new/`none` | readable linked group, no posts copied yet | `enabled` unlinked → `enabled` linked |
| `none`/`unavailable` | linked group appears, posts already copied | unchanged (no anchor backfill, ADR-0023) |
| `enabled` | group unreadable at init or mid-sync | `unavailable`, progress cleared |
| `enabled`/paused | `init --no-comments` | refused, exit 2 |

Invariants: phase-2 progress exists only when `enabled`, or paused with the
same source group; `discussion_linked` implies a destination group id;
`clone sync` refuses `enabled` without `discussion_linked`.

## 5. Clone sync legs

`clone.sync._sync_slot` with `legs.PostsLeg` and `legs.DiscussionLeg`.
Each leg is a cursor (last source id handled) plus an id map
(source → destination).

| Event | Effect | Saved |
|---|---|---|
| service message | cursor advances, `skipped_service` | yes |
| unsupported message | cursor advances, `skipped_unsupported` | yes |
| batch sent and every `random_id` confirmed | map rows added, cursor = last id | one transaction |
| batch confirmation incomplete | nothing; exit 2 | no |
| comment whose parent post is beyond the posts cursor | leg stops (`deferred`), cursor stays | no |
| post whose parent comment is beyond the discussion cursor | posts window stops, cursor stays; that post alone goes out flat once a comments window stalls (F7) | no |
| `--limit` / `--max-runtime` | stop between batches | yes |
| FloodWait / 5xx | run stops; next run resumes from the saved cursor | — |

Invariants:

- **L1.** Cursor and map rows for a batch land in one SQLite transaction
  after Telegram confirmed every message; the cursor never passes an
  unmapped copied message.
- **L2.** A batch that Telegram accepted but tgcli did not record (crash
  between send and save) is detected before the next copy: `verify_tail`
  refuses any ordinary destination message above the highest mapped id
  (exit 2, manual repair). This is the ADR-0017 "at most one duplicated batch"
  bound, made loud.
- **L3.** The reupload cache (`clones/<id>-media`) is cleared only after the
  batch's mapping is saved, so a crash resumes the download. A finished
  download is `src-<id>` plus a `src-<id>.done` marker. An empty marker names
  `src-<id>` itself (a streamed download). A marker holding a file name names
  the file Telethon chose for a one-chunk download (`src-<id>.jpg`). The next
  run reuses that file while its byte size matches the message's media, and
  otherwise deletes it before downloading again: Telethon never overwrites, so
  a file left behind would send the new download to `src-<id> (1).jpg`.
  `CacheMachine` (`tests/test_clone_media_cache_machine.py`) checks reuse,
  replacement and dropped downloads.

## 6. Clone pin (broadcast only)

`clone.pin`: `unmapped` → `set` (we pinned `pinned_dest_id`) or `occupied`
(the owner already pinned something). Both end states are terminal and
mutually exclusive. A crash between the pin RPC and the save is recovered by
reading the destination's pin: exactly our id adopts `set` without a second
RPC.

## 7. Governor cooldown — removed

ADR-0125 removed the request governor and its `governor.db` ledger. tgcli
keeps no flood state between invocations: within one process Telethon
remembers a FloodWait per request type until it passes, and a command that
exits 5 hands `retry_after` to its caller. The number stays so the later
sections keep theirs.

## 8. Archive account cursor

`archive.sync_state` table `account_sync`: `changes_cursor` (common pts/qts/
date/seq plus subscribed channel pts), `gap_json` (list), `pending_catchups`,
`private_enum`, `reconcile`.

| From | Event | To |
|---|---|---|
| uninitialized | first `sync` | cursor = `getState`, gaps cleared |
| cursor C | `sync`: difference applied in full, catch-ups run | cursor C′, every gap of the pass appended, unfinished catch-ups saved, one IMMEDIATE transaction |
| cursor C | `sync` dies before the checkpoint | cursor C (the next run re-applies; applies are idempotent upserts) |
| any | `differenceTooLong` / `channelDifferenceTooLong` / channel lost | jump to current state, gap recorded |
| any | `rebaseline` | cursor = now, every gap cleared |
| any | `remove CHAT` | channel dropped from cursor and scope atomically |

Invariants: the cursor advances only after every event of the pass is
applied; every gap stays until `rebaseline`; one `sync`/`rebaseline` per
account at a time.

## 9. Archive per-peer `sync_state`

Columns `oldest_id`, `newest_id`, `more`, `last_backfill_at`,
`last_sync_at`, `last_error`. Writers: `backfill_one`, `_catch_up_peer`,
`apply_events`, `_enrich_private_identity`.

`more` means "older history may remain on Telegram": backfill sets it from
whether the page was full. A peer is backfill-complete when
`last_backfill_at` is set and `more` is false; `--private` skips those.

| From | Event | To |
|---|---|---|
| none | difference event for a private peer | `oldest = newest = id`, `more` true |
| any | backfill page | range widens downward, `more` = page full |
| any | backfill flood | checkpoint, `more` true, exit 5 |
| any | catch-up page (forward from `newest_id`) | `newest` widens, `more` unchanged |
| any | catch-up flood | checkpoint, `more` unchanged, exit 5 |

Invariants: the id range only widens; a forward catch-up never leaves a hole
below `newest_id`; only backfill decides `more`.

## 10. Archive media and transcript queue

Table `transcripts`, two coupled machines per voice/video-note row.

Media: `pending` → `done` | `retryable` (attempt < 3) → … → `no_media`
(terminal). A `done` row whose file vanished re-enters the queue.

Transcript: `pending` → `done` | `retryable` → … → `no_transcript`
(terminal); `no_media` forces an untranscribed row to `no_transcript`.

Invariant: a row that has spent the attempt cap is terminal. The cap is the
run's `--max-attempts` (3 by default, at most 5); each run first moves rows
whose attempts already reach it to `no_transcript`, so a row a higher-cap run
left `retryable` does not stay `retryable` with no way out (F4).

## 11. Media download checkpoint

`commands.media.download_media`: `downloads/<sha(source)>.json` (offset,
destination, media identity) beside `<key>.part`.

| From | Event | To |
|---|---|---|
| none | start | state at 0, part truncated |
| state at k | chunks written | state at k′ every 8 chunks and on exit |
| state at k | rerun, same media and output | resume from k (part truncated to k) |
| state at k | media replaced at the source | restart from 0 |
| state | other `--output` | refused, exit 2 |
| state at size | publish | part renamed onto the destination, state removed |
| parallel | any interruption | `resumable: false`, restart from 0 |

Invariant: one writer per source checkpoint, held by `download_lock` from
resume to publish. The serial path appends to the part file, so without it
two runs resuming one source interleave their chunks, record an offset past
the media size, and every later run stops at that offset for good (F3). A
second run exits 3; an archive media tail skips the item without spending an
attempt.

## 12. Login attempt

`login_state` (`logins/<id>.json`, `.session`, `.phone`) and
`commands.login`.

| From | Event | To |
|---|---|---|
| none | `accounts login --phone` | code (staged session, phone sidecar, `phone_code_hash`) |
| code | `--continue --code`, 2FA needed | password |
| code/password | correct secret | authorized: staged session promoted, attempt discarded |
| code | `PHONE_CODE_EXPIRED` | discarded |
| any | 30 min pass | expired (read refuses; `store cleanup` reaps) |
| any | alias removed meanwhile | discarded, new session deleted |

Invariants: only `promote` writes `sessions/<alias>.session`, under that
session's lock; the attempt json never holds the phone; a read never deletes.

## Findings

Reachable invalid states and transitions, ranked by impact. Each was proven
by a test that fails on the code before its fix.

### F1. Retried `clone init --replace` creates a second destination — fixed

Machine §2/§3, retry. The commit supersedes the slot on every attempt. When
a commit fails after creating the destination (a 5xx on the title edit, a
flood, a kill, an unwritable result audit), the preview stays `pending`, and
re-running the same `--commit` archives the half-built new state, mints a
fresh marker, and creates another channel. The first one is orphaned in the
account. Fix: the preview records which state it would supersede, and the
commit supersedes only that state (S4).
Test: `tests/test_cli_clone_init.py::test_clone_init_replace_commit_retry_resumes_its_own_destination`.

### F2. Governor cooldowns outlive their one-day cap — fixed, then removed

Machine §7. The two readers of a cooldown row clamped it differently, so a
deadline past a day kept refusing after the other reader said it had ended.
The machine is gone (ADR-0125).

### F3. Two runs resuming one media download wedge it for good — fixed

Machine §11, concurrency. Nothing owns a source's checkpoint. Two resumes
(`media download`, or an `archive backfill` media tail beside an `archive
sync` on a job role) append into one `.part`, record an offset past the
media size, and every later run exits 2 at that offset. For the archive this
burns the three media attempts and marks the voice note `no_media` and
`no_transcript` permanently. Fix: `download_lock` makes the checkpoint
single-writer.
Tests: `tests/test_commands_media.py::test_two_runs_resuming_one_source_never_wedge_it`,
`tests/test_cli_archive_phase3.py::test_sync_media_rate_limit_still_advances_the_changes_cursor[busy]`.

### F4. Archive status flags drift — fixed

Machine §9/§10. A catch-up flood writes `more: true` onto a channel whose
backfill had finished, so `status` reports history left that is not there.
A transcript that failed three or four times under `--max-attempts 5`
stays `retryable` forever under the default cap of 3: no run takes it and it
never becomes `no_transcript`, against CONTRACT's "exhausted failures become
`no_transcript`". Fix: a catch-up flood keeps `more`, and `transcribe` first
makes rows exhausted under its cap terminal.
Tests: `tests/test_archive_sync_state_writes.py::test_a_catchup_flood_keeps_a_finished_backfill_finished`,
`tests/test_archive_phase4.py::test_transcribe_row_exhausted_under_a_lower_cap_becomes_terminal`.

### F5. A short flood can shrink a live governor cooldown — fixed, then removed

Machine §7, concurrency. Arming overwrote the stored deadline, so a 5 s
FloodWait one process drew on chat B replaced a 1 h penalty another process
recorded on chat A. The machine is gone (ADR-0125).

### F6. FLOOD_PREMIUM_WAIT counts as a refusal — fixed

Machines §5, §9, §10, and every per-item failure record. Telegram answers a
non-Premium account that downloads or uploads too fast with
`FLOOD_PREMIUM_WAIT_X`, a 420 like `FLOOD_WAIT_X` but another Telethon class.
Every handler caught only
`FloodWaitError`: the run exited 1 instead of 5, an archive media tail spent
one of the item's three attempts on it (three such floods make a voice note
`no_media` and `no_transcript` for good), and clone code that maps a refusal
to a lasting outcome treated it as one (a thumb dropped for good, a reference
send reuploaded, comments degraded to `unavailable`, a roster reason that was
not `flood_wait`). Found by `CloneSyncFaultMachine`. Fix: `tg_errors.FLOOD_ERRORS`
names both classes, the transient and run-aborting families include it, and
`scripts/check-architecture.py` refuses a handler that names one flood class.
Tests: every `TRANSIENT_ERRORS` case in `tests/conftest.py` (`premium-flood`),
`tests/test_cli_archive_phase3.py::test_sync_media_rate_limit_still_advances_the_changes_cursor[premium-flood]`.

### F7. A resumed sync flattens a post's reply to a comment — fixed

Machine §5, retry. The legs interleave in windows (ADR-0051): posts, then the
comments those posts unlock. A post that replies to a comment kept its reply
only if the comments leg had already copied that comment. Every run starts
with a posts window, so when a run stopped after a posts window but before
the comments leg caught up (a flood, a 5xx, a kill, `--limit`,
`--max-runtime`), the next run copied the following posts first, and a post
replying to one of those comments went out flat. An uninterrupted run kept
the reply. Found by `CloneSyncFaultMachine`. The owner chose to fix it
(2026-11-07). Now the posts leg mirrors the comments leg: a post whose parent
comment lies beyond `discussion_cursor` ends the posts window unsent. It goes
out flat only when the comments window after it does not move the cursor.
Test: `tests/test_clone_reply_placement.py::test_a_resumed_sync_keeps_a_posts_reply_to_a_comment_not_copied_yet`.

### Checked and holding

- A clone sync cut short anywhere, then resumed (`tests/test_clone_sync_faults.py`,
  `CloneSyncFaultMachine`): a FloodWait of either kind, a 5xx, a timeout, a
  dropped connection, or a killed process before any operation of a run; a
  send Telegram ran whose answer was lost, never came, or came without its
  `random_id` confirmations. After every run the slot loads, each mapping is
  the copy Telegram made, no source message has two copies, and no cursor
  passes an unmapped message (L1); an unrecorded copy makes every later run
  refuse before copying (L2) until the owner repairs the tail; and once the
  faults stop one run finishes the clone, every copy identical in body and
  reply target to a run that never failed. 3,000 generated
  worlds of up to ten runs each held.

- Concurrent `--commit` of one preview (flock + inode check).
- Crash between a clone batch send and its save (tail verification, L2).
- Crash between creating a clone destination and saving its id (marker).
- Crash between the pin RPC and its save (exact-id adoption).
- Re-sent `send`/`forward` commit (`random_id`), re-sent `edit`.
- Two `archive sync`/`rebaseline`/`transcribe` on one account (account lock).
- Archive cursor regress after a killed sync (re-applied idempotently).

### Suspected, not proven

- Two `accounts login --continue` of one attempt can leave an empty staged
  `.session` without its json, which `store cleanup` never lists.
