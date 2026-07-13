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
tg mirror init SOURCE [--commit]
tg mirror sync SOURCE
```

This first vertical slice accepts one broadcast channel. `init` without
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
persists authorization. A unique temporary creation marker makes an ambiguous
accepted create recoverable without blindly creating a duplicate. One exact
match is resumed; multiple matches exit 2 for operator review. Authorized JSON
uses `destination:{"id":999,"title":"Source channel"}`, status `authorized`,
and `commit_required:false`. Plain init columns are `status`, `mirror_id`,
`source_peer_id`, `destination_peer_id`.

`sync` requires that authorization and currently copies only unprotected text
posts, oldest first, using native server-side copy with the original author
hidden. Its JSON adds:

```json
{"sync":{"copied":42,"last_confirmed_message_id":73}}
```

Plain sync columns are `copied`, `last_confirmed_message_id`, `mirror_id`,
`source_peer_id`, `destination_peer_id`.
The mirror envelope returned by `sync` reports the title read from the resolved
destination rather than assuming it still matches the source.

Each mirror has one SQLite database at
`TGCLI_STATE_DIR/mirrors/<mirror_id>.db`. Before every copy dispatch, tgcli
persists a stable signed 64-bit Telegram `random_id`; retry reuses that value.
The source/destination mapping and confirmed cursor advance atomically only
after the matching Telegram confirmation. Re-running `sync` therefore creates
no duplicate for an already confirmed source message.

`--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block `init --commit`
and `sync` before config/session/network work. Every create, title edit, and
copy attempt appends the shared fail-closed audit before dispatch. A protected
channel, a message with `noforwards`, media, or a service action exits 2 before
that unsupported item is prepared or copied; already confirmed earlier text
posts remain committed. Media, albums, replies, linked comments, foreground
watch, protected-content reupload, and forum topics are explicit later slices,
not silently claimed by this contract.

`sync` has no implicit overall timeout because a serial backfill may
legitimately run for longer than 60 seconds. An explicit `--timeout` still
applies; interruption leaves an unconfirmed operation with its persisted
random id for the next resumable run. `init` retains the normal 60-second
default timeout.
