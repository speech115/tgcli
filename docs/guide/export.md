# Export messages and subscribers

`export` writes a whole chat's history or a channel's subscriber list to a
file. Both subcommands require `--output`; nothing is exported to stdout.

## Export messages

```bash
tg --json export messages CHAT --output messages.jsonl
```

| Flag | Effect |
| --- | --- |
| `--output PATH` | required; destination JSONL file |
| `--limit N` | cap on rows written this run |
| `--after-id ID` | export only messages with id greater than `ID` (Telethon `min_id`) |
| `--append` | append to an existing file; requires `--after-id` or `--resume` |
| `--resume` | resume from the last JSONL line's message id in `--output` |

Without `--append`/`--resume`, `export messages` iterates a Telethon takeout
session oldest to newest, writes a sibling temporary file, and replaces
`--output` only after the full export succeeds — a failed full export
leaves an existing destination untouched. Each line is a UTF-8 JSON object
in the `read`-message shape: `id`, `date`, `from`, `text`, `media`,
`reply_to`.

### `--resume`

`--resume` reads the **last non-empty line** of the file at `--output`,
parses it as JSON, and takes its `id` field as the resume point. It then
behaves exactly as `--append --after-id <that id>`: new rows with
`id > <that id>` are appended to the same file. There is no sidecar state
file — the JSONL file itself is the only record of where the last run
stopped. A missing, empty, or corrupt last line fails the command (exit 1)
rather than guessing a restart point.

```bash
tg --json export messages CHAT --output messages.jsonl --resume
```

### Completion JSON

```json
{"export":{"kind":"messages","format":"jsonl","path":"messages.jsonl","count":42,
 "dialog":{"id":-1001234,"name":"Channel"},"after_id":100,"appended":true}}
```

`after_id` and `appended` only appear when `--after-id`, `--append`, or
`--resume` was used. `count` is always this run's written rows only, not
the file's total. `--plain` emits one row: `kind`, `format`, `path`,
`count`.

## Export subscribers

```bash
tg --json export subscribers CHAT --output subscribers.csv
```

| Flag | Effect |
| --- | --- |
| `--output PATH` | required; destination CSV file |
| `--limit N` | cap on rows (see broadcast caveat below) |

Writes UTF-8 CSV with the frozen column order:

```
id,username,first_name,last_name,phone,is_bot
```

Standard CSV quoting applies; a username or name cell starting with `=`,
`+`, `-`, or `@` is prefixed with a single quote so spreadsheet programs
never read it as a formula.

Both **broadcast channels** and **megagroups** (supergroups) support
subscriber export; the crawl strategy differs:

- **Broadcast channel, `--limit` omitted**: tgcli unions saturating prefix
  searches over `channels.getParticipants` to walk past Telegram's hard
  200-row cap for a single query, recovering the full subscriber list.
- **Broadcast channel, `--limit` > 200**: exits 2 (`BLOCKED`) — it must not
  run a full-channel crawl and then slice an arbitrary post-dedupe result.
  Omit `--limit` for a complete broadcast export.
- **Broadcast channel, `--limit` ≤ 200**, or any **megagroup**: a single
  `iter_participants` pass.

An emoji/CJK-only display name with no searchable character may leave a
member unreachable by the prefix crawl.

## Long exports and rate limits

Exports have **no implicit overall timeout**: a takeout of thousands of
messages may legitimately run past the normal 60-second command deadline.
An explicit `--timeout SEC` still applies if given. A `TakeoutInitDelayError`
exits **5** (rate limited) with `retry_after` in the JSON error, telling you
how many seconds to wait before retrying.

## See also

- [media.md](media.md) — per-message media inventory and download
- [../CONTRACT.md](../CONTRACT.md) — §7 canonical JSON shapes and exit codes
- [../decisions/ADR-0031-broadcast-subscriber-export.md](../decisions/ADR-0031-broadcast-subscriber-export.md) — why broadcast export uses prefix search
