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

## 11. Channel Clone (ADR-0017)

`tg clone` is the canonical channel-copy surface. v1 accepts broadcast
channels, not megagroups, forums, or comment threads.

```text
tg clone status [SOURCE]
tg clone init SOURCE
tg clone init SOURCE --commit PREVIEW_ID
tg clone sync SOURCE [--limit N]
```

`status` is local and read-only: it never loads config or opens a Telegram
session. Without `SOURCE` it lists every JSON state file; with `SOURCE` it
filters by exact numeric source id or case-insensitive title substring. JSON:

```json
{"clones":[{"clone_id":"hex","source":{"id":123,"title":"Source"},"destination_id":999,"cursor":42,"copied":40,"cooldown_until":null,"created_at":"2026-07-15T12:00:00+00:00","last_synced_at":null}]}
```

Plain status columns are `source_peer_id`, `source_title`,
`destination_peer_id`, `cursor`, `copied`, `last_synced_at`.

`init SOURCE` is a read-only network preview. It resolves the source, verifies
that it is a broadcast channel, reads the approximate message count and
protected-content flag, and stores a five-minute single-use preview through the
shared safety mechanism. It does not create clone state or mutate Telegram.
JSON:

```json
{"preview_id":"p_...","expires_at":"...","clone":{"id":"hex","source":{"id":123,"title":"Source"},"destination":null,"status":"planned","commit_required":true},"approximate_message_count":321,"protected":false}
```

`init SOURCE --commit PREVIEW_ID` requires a matching unexpired clone-init
preview. `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block before
preview consumption, config, session, audit, or Telegram work. The commit uses
a mutation-safe session, verifies that the resolved account/source ids still
match the preview, then creates or recovers one private creator-owned broadcast
destination. JSON:

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source"},"destination":{"id":999,"title":"Source"},"status":"ready","commit_required":false}}
```

Plain init columns are `status`, `clone_id`, `source_peer_id`,
`destination_peer_id`.

Before creation, state with `destination_peer_id:null` and title marker
`tgcli-clone-<clone-id-prefix>` is atomically saved under
`TGCLI_STATE_DIR/clones/<clone_id>.json`. Recovery adopts exactly one matching
private creator-owned broadcast channel, creates when none exists, and exits 2
without mutation on multiple or wrong-shape matches. Once a destination id is
recorded, repeated init resolves and reuses it without scanning or creating.
Destinations are user-owned and never deleted automatically.

After creation or recovery, init applies the source title, copies a non-empty
channel description, and copies a non-empty static channel avatar before
returning `status: ready`. Empty source fields cause no destination mutation.
Avatar bytes use a temporary directory that is removed on success or failure.
Animated or video avatar motion is not preserved (ADR-0020).

Every create, title-edit, description-edit, and avatar-edit attempt appends a
fail-closed shared audit record before dispatch. A profile-copy failure exits 2
while retaining the recorded destination for a new-preview retry; it never
creates a second channel. Telegram FloodWait during profile reads, downloads,
uploads, or edits persists `retry_not_before` in clone state; later commit
attempts exit 5 locally while that deadline is active. Init keeps the global
60-second default timeout.

`sync SOURCE` requires initialized state and a private creator-owned broadcast
destination. It verifies the destination tail before reading new source
history. Rows after the largest persisted mapping (or the fresh-channel id-1
baseline) are accepted only when every visible tail row is a Telegram service
action, such as channel creation or the init title change. Any ordinary tail
message exits 2 with its count in `unexpected` and requires manual repair; no
source scan, audit, or copy occurs (ADR-0018).

Sync iterates source history with `reverse=True` and
`min_id=cursor`, so confirmed destination messages follow source order. The
allowlist is unprotected non-reply text/no-media, `MessageMediaWebPage`,
`MessageMediaPhoto`, and `MessageMediaDocument`; captions remain attached by
native `messages.forwardMessages(drop_author=True)` copying. The destination
does not expose source-forward attribution.

Contiguous messages whose `grouped_id is not None` are one album batch, including
the valid edge case `grouped_id=0`. The complete ordered album is sent by one
request with one distinct random id per item. Every requested random id must
have exactly one unique positive `UpdateMessageID` before all mappings and the
batch cursor are atomically saved. Missing, duplicate, extra, or invalid
confirmation exits 2 without partially advancing state; the next run's tail
verification detects a batch Telegram accepted but tgcli could not confirm.

Service messages advance the cursor and increment `skipped_service` without
audit or Telegram mutation. Polls become human-readable static result cards with
a Russian heading, question, options, Unicode progress bars, counts, rounded
percentages, and total voters; no timestamps or implementation labels are
shown. Story references become two-line `Stories недоступна` placeholders whose
resolved author name/title is a clickable `t.me` link when possible; Story IDs
are not shown. Both use the audited `clone-sync-snapshot` path, receive
source-to-destination mappings, and count as copied. Truly unsupported kinds
such as dice advance the cursor and appear in `skipped_unsupported`; nothing is
skipped silently (ADR-0019).

An unprotected batch without a reply uses native forwarding. A batch uses
download/reupload reconstruction when the source channel or any message has
`noforwards`, or when the batch carries a reply. This preserves the mapped
destination reply relationship that Telegram drops from native forwarding.
Ordinary same-source-channel replies preserve the mapped direct parent and,
when present, the mapped nested top root. If either mapping is unavailable,
the message content still copies in source order without a reply relation.
Cross-peer, forum, scheduled, ephemeral, todo, poll-option, reply-from,
reply-media, malformed quote, and inconsistent album reply shapes exit 2
before audit or mutation. Supported quote text, entities, and offset are
retained.

Reupload sends text and webpage messages with `sendMessage`, photos/documents
with `sendMedia`, and albums with per-item `uploadMedia` followed by one
ordered `sendMultiMedia`. Captions and entities are retained; documents retain
MIME type and Telegram attributes. Downloaded files live only in a temporary
directory and are removed on success or failure. A download failure leaves the
batch cursor and mapping unchanged and occurs before the fail-closed
`clone-sync-reupload` audit/write boundary. Upload/send FloodWait persists the
clone cooldown. Both `UpdateMessageID` batches and the single-message
`UpdateShortSentMessage` envelope require exact positive confirmation before
state advances.

`--limit N` must be positive and copies at most N message batches. If another
source row remains, JSON reports `"more":true`; the next run resumes at the
saved cursor. Sync has no implicit overall timeout, uses a mutation-safe
session, and is blocked by all readonly gates before config/session work.
FloodWait persists the clone cooldown and exits 5 without advancing the current
message. JSON:

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source"},"destination":{"id":999,"title":"Source"}},"sync":{"copied":2,"skipped_service":1,"skipped_unsupported":[{"id":4,"kind":"MessageMediaDice"}],"cursor":5,"more":false}}
```

Plain sync columns are `copied`, `skipped_service`,
`skipped_unsupported_count`, `cursor`, `clone_id`, `source_peer_id`,
`destination_peer_id`, `more`.
