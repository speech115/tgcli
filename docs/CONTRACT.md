# CLI Automation Contract

Version: 0.1 (pre-implementation draft; frozen at first release).
Any change here lands in the same commit as the code change (AGENTS.md).

## 1. Invocation

```
tg [global-flags] <command> [subcommand] [args] [options]
```

Global flags (available on every command):

| Flag | Meaning |
|------|---------|
| `--account <alias>` | account alias from config; default: config `default_account` |
| `--json` | machine output: one JSON document to stdout |
| `--plain` | stable TSV to stdout (no colors, no alignment) |
| `--readonly` | hard-block any mutating call in this invocation |
| `--timeout <sec>` | overall invocation deadline (default 60; no default deadline for media and exports) |
| `-v/--verbose` | Python and Telethon debug diagnostics on stderr for this invocation |

Env equivalents: `TGCLI_ACCOUNT`, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1`.
Flag beats env, env beats config.

## 2. Streams

- **stdout** — contract data only. With `--json`: exactly one JSON document.
  With `--plain`: TSV rows. Default (human) mode: readable tables/text.
- **stderr** — everything else: progress, hints, warnings, error messages.
  With `--json`, the final error is also mirrored to stderr as a single-line
  JSON object: `{"error": {"code": "FLOOD_WAIT", "message": "...", "retry_after": 42}}`.

## 3. Stability Rules

- JSON: adding fields is allowed anytime; renaming/removing/retyping fields
  is a breaking change → requires ADR + major version bump.
- TSV: column order is frozen per command; new columns append at the end.
- Datetimes: ISO 8601 UTC (`2026-07-06T12:00:00+00:00`). IDs: as integers.

## 4. Exit Codes

| Code | Meaning | Typical cause |
|------|---------|---------------|
| 0 | success | |
| 1 | runtime error | network, unexpected exception |
| 2 | blocked by safety policy | `--readonly` + mutating command, `TGCLI_NO_SEND` |
| 3 | config/auth error | missing account, dead session, bad api_id |
| 4 | not found | unknown dialog, message id, media |
| 5 | rate limited | FloodWait longer than threshold; `retry_after` in error JSON |

## 5. Core JSON Shapes (phase 1–3)

`tg dialogs --json`:
```json
{"dialogs": [{"id": -1001234, "name": "Channel", "kind": "channel",
              "username": "chan", "unread": 3,
              "last_message_at": "2026-07-06T11:59:00+00:00"}]}
```

`tg read <chat> --json`:
```json
{"dialog": {"id": -1001234, "name": "Channel"},
 "messages": [{"id": 42, "date": "2026-07-06T10:00:00+00:00",
               "from": {"id": 111, "name": "Alice"},
               "text": "hello", "media": null, "reply_to": null}]}
```

`tg search <chat> <query> --json` uses the same `dialog` and message shapes as
`read`, adding the submitted query:
```json
{"dialog": {"id": -1001234, "name": "Channel"}, "query": "hello",
 "messages": [{"id": 42, "date": "2026-07-06T10:00:00+00:00",
               "from": {"id": 111, "name": "Alice"}, "text": "hello",
               "media": null, "reply_to": null}]}
```

`tg latest <chat> --json` and `tg message <chat> <message_id> --json` return
one message in that same shape:
```json
{"dialog": {"id": -1001234, "name": "Channel"},
 "message": {"id": 42, "date": "2026-07-06T10:00:00+00:00",
             "from": {"id": 111, "name": "Alice"}, "text": "hello",
             "media": null, "reply_to": null}}
```

`tg info <chat> --json`:
```json
{"id": -1001234, "name": "Channel", "kind": "channel", "username": "chan"}
```

`tg count <chat> --json`:
```json
{"dialog": {"id": -1001234, "name": "Channel"}, "count": 73}
```

`tg media download <t.me/link|chat> [message_id] --json`:
```json
{"source": "@channel:42", "path": "/Users/me/Downloads/clip.mp4",
 "bytes": 104857600, "resumed": false, "parallel": 1}
```

The command accepts public `t.me/<username>/<message_id>` and private
`t.me/c/<channel_id>/<message_id>` links, or a chat reference plus message
ID. Without `--output`, the final file is written to `~/Downloads`; an
existing final path is refused and never overwritten. Progress is emitted only
to stderr. Single-stream transfer resumes a matching interrupted partial file
from `~/.local/state/tgcli/downloads/`; `--parallel N` is opt-in, requires a
positive `N`, and starts a fresh offset-based transfer.

### TSV Shapes

`dialogs` retains its phase-1 columns. `read` and `search` output one row per
message as `id`, `date`, `from_name`, `text`; `latest` and `message` use the
same single-row shape. `info` outputs `id`, `kind`, `username`, `name`.
`count` outputs one `count` value.
`media download` outputs `path`, `bytes`, `resumed`, `parallel`.

`tg send CHAT TEXT --preview --json`:
```json
{"preview_id": "p_9f3a", "to": {"id": 111, "name": "Alice"},
 "text": "hello", "expires_at": "2026-07-06T12:05:00+00:00"}
```
Previews expire after five minutes and are single-use: `tg send --commit p_9f3a`
replays only the stored target and text, then consumes the preview even if the
network call fails. Commit JSON is `{"preview_id": "p_9f3a", "message_id": 42}`.
Every authorised send commit appends one JSON object to
`~/.local/state/tgcli/audit.jsonl` (or `TGCLI_STATE_DIR/audit.jsonl`) before
network dispatch. If the audit record cannot be written, the mutation is
blocked with exit 2; tgcli never performs an unaudited authorised write.
Preview creation itself does not send or audit a mutation.

## 6. Raw API Passthrough (`tg api`, phase 2+; ADR-0010)

```
tg api <Namespace.method> --params '<json>' [--write] [--confirm <method>]
```

- `--params` is required and must be a JSON object. In phase 2, only the
  reviewed explicit allowlist in ADR-0010 may run through the configured
  session (35 methods as of 2026-07-10; e.g. `users.getFullUser`,
  `messages.getHistory`, `channels.getParticipants`).
- Without `--write`, every method outside the ADR-0010 read allowlist is
  blocked before config loading or session acquisition with exit 2.
- With `--write`, the same `--readonly`, `TGCLI_READONLY=1`, and
  `TGCLI_NO_SEND=1` gates run before config/session/network work. Destructive
  `delete*`, `reset*`, `leave*`, `block*`, `edit*Admin*`, and `edit*Banned*`
  methods require an exact `--confirm <Namespace.method>`; the permanent
  denylist `account.deleteAccount`, `auth.logOut`, `auth.resetAuthorizations`,
  and `account.resetAuthorization` is always exit 2. Authorised raw writes
  append one JSONL audit object before dispatch.
- `--json` output: `{"method": "users.getFullUser", "result": {…}}` where
  `result` is the TL object as a dict.
- **Stability exemption:** `result` mirrors the Telegram TL layer of the
  pinned Telethon version and may change when that pin is upgraded; the §3
  stability rules do not apply inside `result`. Everything outside `result`
  follows §3 as usual.

## 7. Export (phase 5)

```
tg export messages <chat> --output <path> [--limit <n>]
tg export subscribers <channel> --output <path> [--limit <n>]
```

- `--output` is required. It is the only destination for the export records;
  the command writes a sibling temporary file and replaces the destination only
  after the complete export succeeds. An existing destination is unchanged on
  a failed export.
- `messages` iterates through a Telethon takeout session from oldest to newest.
  The destination is UTF-8 JSONL: one `read`-shape message object per line,
  with `id`, `date`, `from`, `text`, `media`, and `reply_to` fields.
- `subscribers` writes UTF-8 CSV with the frozen header
  `id,username,first_name,last_name,phone,is_bot`; standard CSV quoting is
  used for field values. Username and name cells beginning with `=`, `+`, `-`,
  or `@` are prefixed with a single quote so spreadsheet programs do not
  interpret them as formulas.
- Success on `--json` is one completion document:
  `{"export":{"kind":"messages|subscribers","format":"jsonl|csv",
  "path":"<path>","count":42,"dialog":{"id":-1001234,"name":"Channel"}}}`.
  `--plain` emits one TSV row in the frozen order `kind,format,path,count`.
- A `TakeoutInitDelayError` exits 5 as `FLOOD_WAIT`, includes
  `retry_after`, and tells the user to retry after that many seconds.
- Exports have no implicit overall timeout because a 10k-message takeout may
  legitimately exceed the normal 60-second command deadline. An explicit
  `--timeout` still applies.
- An existing integer takeout identifier is reused. A malformed local
  identifier (for example a legacy `b''` value) is cleared before a new
  takeout is initialized, preventing a Telethon serialization traceback.

## 8. Untrusted Content

Message texts, dialog names, and file names are untrusted input. In `--json`
mode they are passed through as data (JSON escaping is sufficient). In human
mode control characters are stripped. tgcli never interpolates message
content into shell commands or file paths without sanitizing.

## 9. Invocation Journal and Diagnostics

Every successfully parsed command appends one JSON object to
`~/.local/state/tgcli/invocations.jsonl` (or `TGCLI_STATE_DIR/invocations.jsonl`):
`timestamp`, `command`, resolved `account` when applicable, `exit_code`,
structured `error` code when applicable, and `duration_ms`. The journal never
contains message/search text, chat references, raw API parameters, or command
output. A journal-write failure emits a warning to stderr but does not change
the command result.

`-v` / `--verbose` enables Python and Telethon debug logs on stderr for the
current process. Stdout remains contract data in all output modes.

## 10. Accounts (phase 6)

```
tg accounts import [ALIAS ...] [--source-root PATH] [--force]
```

This is a local-only command: it never opens a Telegram connection. With no
aliases it tries `main`, `recklessou`, and `teamsyncsage`, and reports a
missing old-stack source as a warning rather than failing. An explicitly named
missing source exits 4. The command copies old-stack SQLite sessions with an
online backup into `TGCLI_STATE_DIR/sessions`; an existing destination is left
untouched unless `--force` is supplied. A busy destination lock or missing or
unparseable credentials for a newly configured account exits 3.

`--json` emits:

```json
{"imported": [{"alias": "pl", "session": "/home/me/.local/state/tgcli/sessions/pl.session",
               "status": "imported|skipped_existing|source_missing",
               "config": "added|unchanged"}]}
```

`--plain` emits frozen TSV columns: `alias`, `status`, `config`.

## 11. Lean Channel Mirror (post-v1, ADR-0014)

```text
tg mirror init SOURCE [--commit [--retry-create --confirm MIRROR_ID]]
tg mirror sync SOURCE
```

This post-v1 mirror accepts one broadcast channel. `init` without
`--commit` resolves the configured account and source, creates or reopens only
local planned state, and performs no Telegram mutation. Its JSON shape is:

```json
{"mirror":{"id":"<sha256>",
           "source":{"id":123,"title":"Source channel"},
           "destination":null,
           "status":"planned","commit_required":true}}
```

`init --commit` creates or resumes one private creator-owned broadcast
destination, restores its visible title to the current source title, and
persists authorization. Immediately before a create dispatch, tgcli records
the exact temporary marker, a UTC attempt time, and creation state
`reconcile_required`. Creation state is one of `planned`,
`reconcile_required`, `blocked`, or `authorized`; retention class is
`provisional` until authorization atomically changes it to
`user_owned_retained` together with the destination id and authorized flag.

Recovery scans every exact-marker dialog before any new create. Exactly one
private creator-owned broadcast resumes. One wrong-shape exact-marker dialog
or more than one exact-marker dialog records `blocked` and exits 2 without
create, edit, or delete. Zero matches after a dispatched create never permits
an automatic second create: the only retry form is
`tg mirror init SOURCE --commit --retry-create --confirm MIRROR_ID`, with the
exact id from that mirror. Either retry flag without `--commit`, a missing
partner flag, or a non-exact id exits 2. An already authorized mirror resolves
only its stored destination id and never scans the creation marker or creates
again. Retry-create flags are inapplicable after authorization: missing,
unpaired, non-exact, and otherwise valid retry pairs all exit 2 instead of
being silently accepted. Retry-flag pairing is validated before session
acquisition; exact mirror identity is validated after source/account
resolution and before the authorized short-circuit.

Authorized JSON uses `destination:{"id":999,"title":"Source channel"}`,
status `authorized`, and `commit_required:false`. Plain init columns are
`status`, `mirror_id`, `source_peer_id`, `destination_peer_id`.

`sync` requires that authorization and copies supported content oldest first
through Telethon exactly `1.44.0`, pinned in project metadata and the
lockfile. The explicit content allowlist is text/no media,
`MessageMediaWebPage`, `MessageMediaPhoto`, and `MessageMediaDocument`,
including generic files and Telegram's video, audio, voice, and sticker
document variants. Unprotected batches without replies are copied by
`messages.forwardMessages` with the original source message ids,
`drop_author=True`, and `drop_media_captions` unset, so Telegram carries the
original native media and caption without any download or reupload.
Protected batches — a `noforwards` source channel or message — are
reconstructed instead: media is downloaded to a temporary directory that is
removed after the batch, then re-sent through `messages.sendMessage`,
`messages.sendMedia`, or `messages.uploadMedia` + `messages.sendMultiMedia`
with the same journaled random ids, preserving text, entities, captions,
album grouping, mapped replies, and document `mime_type`/attributes; photos
are re-encoded by Telegram. Unprotected reply batches use the same
reconstruction path because Telegram's native forward request does not retain
the mapped destination reply in a broadcast channel. A failed download exits
2 with the batch left pending. Sync JSON adds:

```json
{"sync":{"copied":42,"skipped_service":1,"last_confirmed_message_id":73}}
```

Plain sync columns are `copied`, `last_confirmed_message_id`, `mirror_id`,
`source_peer_id`, `destination_peer_id`, `skipped_service`. The new service
counter is appended so the five frozen legacy columns do not move.
The mirror envelope returned by `sync` reports the title read from the resolved
destination rather than assuming it still matches the source. `copied` counts
confirmed source messages, including every item in a confirmed album, rather
than Telegram RPCs or batches. Service messages (any `Message.action`, such as
the channel-creation notice at message id 1) are never copied: the new-history
scan skips them and reports the per-run count as `skipped_service`. They do not
advance the confirmed cursor, so a trailing service message is recounted on the
next run.

Each mirror has one SQLite database at
`TGCLI_STATE_DIR/mirrors/<mirror_id>.db`. A singleton message is a one-item
batch; every complete contiguous run whose `grouped_id is not None` is one
album batch. Before audit or dispatch, tgcli atomically persists the complete
ordered batch. Every item owns a distinct stable signed 64-bit Telegram
`random_id`, and retry reuses the same ordered source ids and random ids.

One batch attempt appends exactly one fail-closed `mirror-sync-forward` audit
record containing the ordered source ids and random ids, then issues exactly
one `ForwardMessagesRequest`. Confirmation must contain exactly one matching
`UpdateMessageID` for every requested random id and distinct positive
destination ids. Missing, duplicate, extra, or invalid confirmations leave the
entire batch pending and do not advance the cursor. A valid complete mapping,
all source/destination mappings, and the confirmed cursor commit in one SQLite
transaction; no album item advances alone.

Pending batches recover before new history. tgcli requests the exact pending
source-id set, rejects missing, duplicate, or unexpected returned ids, restores
request order from persisted `batch_index`, revalidates content and reply
policy, and replays the original random ids. Sync is streaming and does not
pre-scan unbounded history: a later non-contiguous reuse of an already completed
`grouped_id` blocks the reused segment before its prepare, audit, or network
work, while earlier independently confirmed batches remain committed.

A plain intra-channel reply is copied only when its
`MessageReplyHeader.reply_to_msg_id` has an already confirmed destination
mapping. The child is then forwarded with `InputReplyToMessage` targeting that
destination parent; supported `quote_text`, `quote_entities`, and
`quote_offset` are preserved. An album may carry the header on its leading
item only, or repeat identical reply and quote metadata on later items.
Unconfirmed parents, a header first appearing after the leading album item,
conflicting album reply metadata, cross-peer, forum, scheduled, ephemeral,
todo, poll-option, reply-from, and reply-media shapes exit 2 before the child
batch is prepared, audited, or dispatched. Source replies are never flattened.

A Telegram `FloodWait` during mirror create, title edit, or message copy writes
an account-scoped UTC `retry_not_before` to
`TGCLI_STATE_DIR/mirrors/cooldowns/<sha256-account-id>.json`. The file is mode
`0600`, is replaced atomically, and the replacement plus parent directory are
fsynced. Cooldown writers serialize on a private account-scoped lock, so
concurrent compare-and-max updates never shorten a later deadline. While the
deadline is active, `init --commit` and `sync` fail
locally before mirror-store creation, marker scan, audit, or Telegram mutation;
the existing exit-5 `FLOOD_WAIT` shape reports the ceiling of remaining seconds
as `retry_after`. tgcli does not sleep or retry internally. `init` preview
remains read-only and available, and expiry of the cooldown does not relax the
explicit confirmed-retry rule after an ambiguous create.

Mirror mutation sessions disable Telethon's hidden RPC replay and short-wait
sleep behavior with exactly `request_retries=0` and
`flood_sleep_threshold=0`. This mode is used only by `init --commit` and
`sync`; init preview and unrelated commands keep the normal read-oriented
Telethon defaults. After source/account resolution, both mutation commands
hold one non-blocking lock keyed by the resolved Telegram user id across the
cooldown check, reconciliation, audit, Telegram dispatch, and durable
confirmation. The private mode-`0600` lock is
`TGCLI_STATE_DIR/mirrors/locks/<sha256-account-id>.lock`, so different local
session aliases for the same Telegram user cannot mutate concurrently; a
contender exits 3 before audit or mutation dispatch.

`--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block `init --commit`
and `sync` before config/session/network work. Every create, title edit, and
copy-batch attempt appends the shared fail-closed audit before dispatch
(`mirror-sync-forward` for native batches, `mirror-sync-reupload` for
reconstructed ones). Paid media, stories, polls, or any other media wrapper
outside the explicit allowlist exits 2 before that batch is prepared, audited,
or dispatched; service messages are skipped and counted instead of blocking.
Mixed supported/unsupported albums also fail as a whole; already confirmed
earlier batches remain committed. Linked
discussion comments, foreground watch, forum topics, groups, reactions,
views, and attribution emulation remain explicit later slices, not silently
claimed by this contract.

An expected local mirror-store migration or invariant failure exits 2 with
`local mirror state is invalid; manual repair is required`. Detection during
sync occurs before destination/input-peer preparation, audit, or Telegram
mutation where the invalid state is already observable. This translation is
limited to `MirrorStore` operations; unrelated Telegram/client `ValueError`
exceptions are not mislabeled as local state failures.

`sync` has no implicit overall timeout because a serial backfill may
legitimately run for longer than 60 seconds. An explicit `--timeout` still
applies; interruption leaves an unconfirmed batch with its persisted source
order and random ids for the next resumable run. `init` retains the normal
60-second default timeout. Cancellation, timeout, review expiry, SIGINT,
SIGTERM, and process failure never delete an authorized
`user_owned_retained` destination; there is no automatic
mirror-destination deletion path.
