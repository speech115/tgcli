# Inbox management

`mark-read`, `mark-unread`, and the `dialog` subcommands change a dialog's
state — read/unread, pinned, archived, muted — without touching any message
content. Unlike `send`/`edit`/`delete`/`forward`, these are direct
mutations: there is no `--preview`/`--commit` step. They are still audited
and still blocked by the same safety gates as any other mutation.

## Mark a dialog read or unread

```bash
tg --json mark-read CHAT
```

```bash
tg --json mark-unread CHAT
```

`mark-read` and `mark-unread` are idempotent: calling either again is
harmless.

## Pin or unpin a dialog

```bash
tg --json dialog pin CHAT
```

```bash
tg --json dialog unpin CHAT
```

## Archive or unarchive a dialog

```bash
tg --json dialog archive CHAT
```

```bash
tg --json dialog unarchive CHAT
```

Archive moves the dialog into Telegram folder id `1`; unarchive restores
folder id `0`.

## Mute or unmute a dialog

Mute requires exactly one of `--until` or `--forever`; omitting both, or
passing both together, is blocked (exit 2).

```bash
tg --json dialog mute CHAT --until 2026-08-01T00:00:00
```

```bash
tg --json dialog mute CHAT --forever
```

```bash
tg --json dialog unmute CHAT
```

| Flag | Effect |
| --- | --- |
| `--until ISO` | unmute automatically at this ISO 8601 timestamp (naive values are treated as UTC) |
| `--forever` | mute indefinitely; must be given explicitly, omitting both flags is not treated as forever |

## Gating

No preview step means these gates are checked immediately, before any
Telegram work:

| Gate | Effect on inbox commands |
| --- | --- |
| `--readonly` | blocks every command on this page, exit 2 |
| `TGCLI_READONLY=1` | same as `--readonly` |
| `TGCLI_NO_SEND=1` | also blocks every command on this page — these all reach Telegram, unlike local-only `store cleanup` |

See [safety](safety.md) for the full gate model.

Each successful call appends a fail-closed audit record before the
Telegram acknowledgement, with the submitted chat reference: `mark-read`,
`mark-unread`, `dialog-pin`, `dialog-unpin`, `dialog-archive`,
`dialog-unarchive`, `dialog-mute`, `dialog-unmute`.

## JSON

```json
{"dialog": {"id": 3817664407}, "marked_read": true}
```

```json
{"dialog": {"id": 3817664407}, "marked_unread": true}
```

```json
{"dialog": {"id": 3817664407}, "pinned": true}
```

```json
{"dialog": {"id": 3817664407}, "archived": true}
```

```json
{"dialog": {"id": 3817664407}, "muted": true, "until": null}
```

`until` is the parsed timestamp for a `--until` mute, and `null` for a
`--forever` mute and for `unmute`.

`--plain` rows: `mark-read`/`mark-unread` emit `dialog_id`, `read`/`unread`;
`dialog pin`/`unpin` emit `dialog_id`, `pinned`/`unpinned`; `dialog
archive`/`unarchive`/`mute`/`unmute` emit `dialog_id` and one of
`archived`/`unarchived`, `muted-forever`/`muted-until:<ISO>`, or `unmuted`.

## See also

- [safety](safety.md) — the preview/gate/audit model and why these commands
  skip the preview step
- [../CONTRACT.md](../CONTRACT.md) — frozen JSON and TSV shapes for every
  command on this page
- [../decisions/ADR-0029-discovery-inbox-scope.md](../decisions/ADR-0029-discovery-inbox-scope.md)
