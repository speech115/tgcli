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
| `--timeout <sec>` | overall invocation deadline (default 60; no default deadline for exports) |
| `-v/--verbose` | extra diagnostics on stderr |

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

## 5. Core JSON Shapes (phase 1–2)

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

### TSV Shapes

`dialogs` retains its phase-1 columns. `read` and `search` output one row per
message as `id`, `date`, `from_name`, `text`; `latest` and `message` use the
same single-row shape. `info` outputs `id`, `kind`, `username`, `name`.
`count` outputs one `count` value.

`tg send --preview --json` (phase 4):
```json
{"preview_id": "p_9f3a", "to": {"id": 111, "name": "Alice"},
 "text": "hello", "expires_at": "2026-07-06T12:05:00+00:00"}
```
Commit replays the stored preview verbatim: `tg send --commit p_9f3a`.
The agent cannot alter text between preview and commit (carried over from
the old stack's confirmed-send design — its one genuinely good write-safety idea).

## 6. Raw API Passthrough (`tg api`, phase 2+; ADR-0010)

```
tg api <Namespace.method> --params '<json>' [--write] [--confirm <method>]
```

- `--params` is required and must be a JSON object. In phase 2, only the
  reviewed explicit allowlist in ADR-0010 may run through the configured
  session (35 methods as of 2026-07-10; e.g. `users.getFullUser`,
  `messages.getHistory`, `channels.getParticipants`).
- Every other method is blocked before config loading or session acquisition
  with exit 2. In phase 2, `--write` is also blocked before session acquisition
  with exit 2 and the message `tg api --write is unavailable until phase 4`.
- Phase 4 will add the `--readonly` / `TGCLI_READONLY` / `TGCLI_NO_SEND`
  checks, typed destructive `--confirm <Namespace.method>`, and the permanent
  account-lifecycle denylist from ADR-0008 before enabling writes.
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
  used for field values.
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
