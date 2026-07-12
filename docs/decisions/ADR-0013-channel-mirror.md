# ADR-0013: Crash-safe channel mirror with an explicit foreground watcher

Status: proposed (2026-07-11).
Amended 2026-07-11 after R0 evidence (see DEVLOG): gotd backend removed; copy
method is capability-based.
Amended 2026-07-12 after R1 evidence: Telegram todo is unsupported in
broadcast-channel mirrors; the initial media-fidelity run was inconclusive.

Amends PLAN.md by bringing a constrained channel mirror back into scope. It
narrows ADR-0002's no-daemon rule without allowing self-installing background
services, extends ADR-0005 with a persistent per-mirror authorization, and
inherits ADR-0009's evidence gate for protected content.

## Context

The retired `tools/telegram` mirror copied channel profile data, backfilled
history, and followed new posts. Its reliability failures came from two places:

1. a self-installed fleet of LaunchAgents and hidden runtime state;
2. non-atomic Telegram sends and local ledger updates, which left ambiguous
   rows, duplicates, and manual repair work after interruption.

The replacement must create private owner-only copies of public or closed
broadcast channels, preserve source order, record source edits/deletions without
destroying the backup, and follow new posts with low delay. It must also remain
compatible with tgcli's exclusive SQLiteSession lock and fail-closed write
safety.

The original draft treated ledger membership as sufficient idempotency. It is
not: Telegram can accept a send before the process commits its local mapping.
It also proposed holding the primary account session lock forever, which would
make every other `tg` command for that account fail busy. This decision fixes
both problems before implementation.

## Decision

### 1. Scope

- Source: one broadcast channel, public or closed, plus its linked discussion
  group when present. General groups and 1:1 dialogs remain out of scope.
- Destination: a real private broadcast channel owned by the same Telegram user.
  A linked private megagroup may be created for copied comments.
- Copy method: capability-based, not one fixed transport. Unprotected source
  channels use native server-side copy via
  `messages.forwardMessages(drop_author=True)` with persisted random ids —
  cheap and requires no temp files. Protected (`noforwards`) source channels
  use Telethon download and reupload; R0 proved complete byte access to every
  byte-bearing media kind for both owner and ordinary-subscriber roles (see
  DEVLOG, "R0 protected-content probe evidence"). A native forward attempt
  against a protected source is expected to return
  `CHAT_FORWARDS_RESTRICTED`; the router treats this as an ordinary `blocked`
  capability and falls through to download/reupload rather than crashing.
- Fidelity target: title, about, avatar, chronological content, albums, replies,
  supported media attributes, pins, and threaded comments. Views, reactions,
  original server dates, original senders, paid media, and unsupported service
  actions are not promised. Unsupported items produce an explicit placeholder
  and ledger status; they are never silently dropped.
- Telegram todo is explicitly unsupported for broadcast-channel sources. R1
  observed `MediaInvalidError` for `InputMediaTodo` on both owned source roles;
  the mirror records an unsupported placeholder instead of silently dropping it.

### 2. Session topology

`tg mirror watch --account <account>` uses a dedicated SQLite session file
`sessions/<session>.mirror-watch.session`, not the primary command session.
Both sessions represent the same Telegram user but have independent SQLite
files and independent tgcli locks.

Provisioning is explicit and local-only:

```text
tg mirror watch --account <account> --init-session
```

The initialization takes the primary session lock, copies it with SQLite's
backup API while no Telegram client is connected, opens the watcher copy, and
verifies that both sessions return the same `get_me().id`. Ordinary `watch`
fails with exit 3 if the watcher session is missing or unauthorized; it never
silently falls back to the primary session. M0 must prove that a watcher client
and a normal read command can run concurrently. If this topology fails on the
pinned Telethon/Telegram layer, implementation stops for a new ADR rather than
monopolizing the primary session by accident.

The watcher remains a foreground process. tgcli never forks, installs a
LaunchAgent, creates a listening socket, or manages its own lifecycle. An
operator may supervise the visible command externally, but that configuration
is outside tgcli and is not shipped by this phase.

### 3. Identity and state ownership

A mirror is identified only after source resolution. Its stable identity is a
hash of `(account_user_id, canonical_source_peer_id)`, not a user-supplied
username or link. Aliases may change; canonical peer identity does not.

Each mirror owns one SQLite database:

```text
TGCLI_STATE_DIR/mirrors/<mirror_id>.db
```

Telegram message ids are peer-scoped. Every source key is therefore
`(source_peer_id, source_msg_id)`. The same rule applies to comments, parents,
changelog entries, operations, and destination mappings.

The database owns:

- mirror identity, source/destination peers, account ids, schema version, and
  profile-sync lifecycle;
- copied-message mappings and normalized content hashes;
- durable outbound operations and their Telegram `random_id` values;
- backfill and watcher high-water marks;
- scan coverage and changelog facts.

The existing global `TGCLI_STATE_DIR/audit.jsonl` remains the security audit
required by ADR-0011. It is intentionally outside `mirrors/`; the
`mirrors/`-only rule applies to mirror-specific operational state, not shared
tgcli journals, sessions, or audit files.

### 4. Crash consistency and idempotency

Every outbound Telegram mutation is represented before network dispatch by a
durable operation with a unique logical key and this state machine:

```text
prepared -> dispatched -> confirmed
                   \-> ambiguous
prepared/dispatched/ambiguous -> confirmed | failed
```

- `prepared`: payload hash, target peer, reply target, and stable Telegram
  `random_id` are committed locally; the fail-closed audit attempt is appended.
- `dispatched`: the request is about to be or has been handed to Telethon.
- `confirmed`: Telegram returned destination message id(s), stored in the same
  local transaction as `message_map` and cursor advancement.
- `ambiguous`: cancellation, disconnect, or process recovery cannot prove
  whether Telegram accepted the request.
- `failed`: a definitive non-retryable error; the source item remains visible in
  status and can be retried only by an explicit repair path.

Text, media, and albums use raw `messages.SendMessageRequest`,
`messages.SendMediaRequest`, and `messages.SendMultiMediaRequest` after upload so
the persisted `random_id` is supplied explicitly. Retrying an ambiguous request
reuses the same random id. No high-level helper that generates an inaccessible
random id may be used on the durable send path.

Recovery first replays ambiguous operations with the same random id and then
confirms their returned ids. Cursor/high-water advancement happens only after
confirmation. Crash-injection tests must cover every transition. This is the
minimum protocol required to claim resumability; `message_map` lookup alone is
not sufficient.

Mirror creation is also resumable. The database is created first with lifecycle
`planned`; the destination id is persisted immediately after channel creation;
avatar, about, linked group, link operation, and authorization become separate
idempotent steps. A retry resumes the missing step and never creates a second
destination merely because profile sync failed. Orphan deletion is not
automatic and requires a separately confirmed future command.

### 5. Watch startup and ordering

The watcher registers filtered new/edit/delete handlers before catch-up. It then:

1. reads a source high-water id;
2. catches up `(last_confirmed, high_water]` oldest to newest;
3. buffers handler events during catch-up;
4. drains the buffer in source order through the same durable operation path;
5. continues live, deduplicating by peer-scoped source key and operation key.

`last_confirmed` advances per confirmed message, not only on SIGINT. A clean
SIGINT flushes local transactions and disconnects, but correctness never depends
on receiving SIGINT.

### 6. Append-only change semantics

The copied message is never deleted or edited to reflect a source edit/delete.
An "annotation" means a new changelog message or reply in the destination:

- edit: record old hash/new snapshot and append an `[edited at source ...]`
  changelog message;
- delete: retain the backup and append `[deleted at source ...]`;
- duplicate live/audit observations reuse a unique changelog fact key and do not
  append twice.

This makes append-only behavior literal rather than relying on ambiguous wording
such as "annotate the copy".

### 7. Authoritative audit semantics

A missing item in one iterator is not proof of deletion. `sync --audit` records
a deletion only when all conditions hold:

1. the requested source range completed without FloodWait, cancellation,
   permission loss, or iterator error;
2. the mapped id is inside that proven-complete range;
3. a targeted `messages.getMessages`/channel equivalent confirms that exact id
   is empty or unavailable while neighboring control ids remain readable;
4. the fact is not already in the changelog.

Incomplete scans update no deletion facts and return a non-zero retryable or
access error. Edits are detected from normalized content snapshots, not remote
file ids alone. The normalized hash covers text, entities, media kind and stable
media metadata, grouped membership, reply parent, and poll snapshot.

### 8. Write authorization and audit

`tg mirror create <source> --commit` authorizes continuing writes only after the
destination is created and verified private, owner-only, and owned by the same
Telegram user as the source session. The authorization record binds:

- account user id and configured account alias;
- canonical source and destination peer ids;
- linked destination peer id when present;
- ledger schema version and authorization timestamp.

Every command revalidates destination ownership/private status before its first
mutation. `TGCLI_READONLY`, `TGCLI_NO_SEND`, and `--readonly` are checked before
configuration/session/network work and again at long-running batch boundaries.
Every mutation writes a fail-closed attempt record before dispatch and a
correlated result record after confirmation or definitive failure. Audit records
describe attempts and outcomes; they do not claim that a pre-dispatch attempt
succeeded.

### 9. Protected-content feasibility gate

**RESOLVED (2026-07-11).** M0 ran as a direct diagnostic using the configured
Telethon client, not `tg api` (the raw JSON passthrough excludes
`upload.getFile`, and a raw file request requires an `InputFileLocation`, so
the plan never depended on an unavailable CLI fallback).

The probe covered a small capability matrix from two real protected
(`noforwards`) broadcast channels — one owner, one ordinary subscriber: every
byte-bearing media kind present in the source history, including a ~1 GB
video read as an ordinary subscriber. It recorded message/media types,
expected and received byte counts, SHA-256 hashes, and errors without storing
media in the repo. See DEVLOG, "R0 protected-content probe evidence
(2026-07-11)" for the full run record.

- Result: **Green.** Every discovered byte-bearing capability row returned
  Telethon `pass` for both account roles. Telethon alone provides complete
  byte access; no gotd/TDLib backend PoC was needed. ADR-0009's re-entry gate
  was not opened.
- The stop-for-gotd/TDLib-PoC path described below was the pre-R0 fallback if
  the probe had come back red. It did not trigger and is retained here only
  as historical context, not as an active decision path:
  a reproducible Telethon parse/download failure would have opened ADR-0009's
  re-entry gate, allowed a raw TL diagnostic inside the probe only when a
  valid file location could be obtained, and otherwise required stopping for
  a measured gotd/TDLib PoC decision or an explicitly reduced capability
  matrix in a new ADR revision.

### 10. Transport selection

The router uses a fixed, non-looping order and never silently changes
strategy after a runtime failure:

```text
native copy -> Telethon reconstruction -> unsupported
```

- **Native copy**: `messages.forwardMessages(drop_author=True)` with
  persisted random ids, for unprotected sources. Cheapest path; no temp
  files.
- **Telethon reconstruction**: download and reupload, for protected sources
  or when native copy returns `CHAT_FORWARDS_RESTRICTED`. R0 proved this path
  has complete byte access for both owner and subscriber roles.
- **Unsupported**: an explicit, recorded result — never a silent drop.

There is no gotd tier and no unbounded retry across backends. A capability
that is not provably `pass` never authorizes automatic mirroring.

### 11. R1 controlled-lab evidence

R1 confirmed `CHAT_FORWARDS_RESTRICTED` for native copy from a protected
broadcast channel. The initial run completed native-forward and
download/reupload calls, but did not prove per-kind fidelity: arbitrary bytes
with MP3/MP4/OGG/WebP names collapsed to Telegram `document`, so both verdicts
were red/inconclusive. Completion of a transport call is not fidelity evidence.

Before per-kind R1 results authorize renderer behavior, the lab must use valid
minimal containers and obtain green complete-matrix verdicts, or this ADR must
narrow the supported capability matrix again.

The repaired classifier uses probe schema 2 because Telegram may reorder
`DocumentAttributeVideo` and `DocumentAttributeAnimated`. Version-1 R0 hashes
remain valid byte-access evidence, but subtype labels are not compared across
schema versions.

## Consequences

- Reliability requires more local protocol than the first draft, but it removes
  the exact ambiguous-send class that made the old mirror expensive to operate.
- One Telegram user has two local SQLite session files. This duplicates a small
  amount of state but keeps ordinary tgcli commands available while watch runs.
- Mirror implementation is split into a CLI facade, ledger/state-machine,
  renderer, and watcher service. The module boundary is part of this ADR and
  must appear in MAP.md.
- FEATURES.md continues to describe current code while this ADR is proposed:
  `updates` remains excluded with a pointer here. During implementation it
  becomes wrapped only when the event loop exists; raw upload remains excluded.
- PLAN.md remains historical but its old mirror non-goal gains a dated pointer to
  this ADR and the new scoped plan.
- No production mirror code starts until the watcher-session gate and every
  capability relied on by production have green or explicitly narrowed
  evidence recorded in DEVLOG.
