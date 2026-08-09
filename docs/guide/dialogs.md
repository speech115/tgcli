# Dialogs and dialog metadata

Enumerate your open conversations, then look up metadata or a message count
for one of them. Start here when an agent needs to discover what chats exist
before reading or searching any of them.

## List dialogs

`tg dialogs` walks your dialog list, newest-activity first.

```bash
tg --json dialogs --limit 50
```

| Flag | Effect |
| --- | --- |
| `--limit N` | max dialogs returned (default 50; `--limit 0` returns an empty list) |
| `--unread-only` | keep only dialogs with unread messages or unread mentions |
| `--kind {user,group,channel}` | keep only this dialog kind |

Megagroup supergroups are classified as `group` even though Telethon also
marks them as channels internally; broadcast channels remain `channel`.

### JSON

```json
{"dialogs": [{"id": -1001234, "name": "Channel", "kind": "channel",
              "username": "chan", "unread": 3,
              "mentions": 0,
              "last_message_at": "2026-07-06T11:59:00+00:00"}]}
```

### `--plain` columns

`id`, `kind`, `username`, `name`, `unread`, `mentions` — in that order, one
row per dialog.

## Inspect a dialog

`tg info CHAT` returns the same identity fields `dialogs` does, for a single
chat reference; add `--full` for capability and moderation metadata.

```bash
tg --json info CHAT
tg --json info CHAT --full
```

| Flag | Effect |
| --- | --- |
| `--full` | add `role`, `can`, `slowmode_seconds`, `participants_count`, `about` |

### JSON

```json
{"id": 3817664407, "name": "Channel", "kind": "channel", "username": "chan"}
```

`--full` adds:

- `role` — `"creator"`, `"admin"`, `"member"`, or `null` for a user dialog.
- `can` — best-effort `send_messages`, `send_media`, `pin_messages`,
  `delete_messages`, `edit_messages` booleans, or `null` when Telegram does
  not expose enough rights data. This is a preflight aid, not authorization
  truth: Telegram remains the authority, and a creator reports every listed
  capability as `true`.
- `slowmode_seconds`, `participants_count`, `about` — from full channel
  metadata for channels and megagroups; `null` for user dialogs and basic
  groups.

### `--plain` columns

`info` and `info --full` share the same four columns: `id`, `kind`,
`username`, `name`. The `--full` additions are JSON-only.

## Count messages

`tg count CHAT` returns Telegram's total message count for a dialog without
fetching any message bodies.

```bash
tg --json count CHAT
```

### JSON

```json
{"dialog": {"id": 3817664407, "name": "Channel"}, "count": 73}
```

`--plain` emits one row with a single `count` value.

## See also

- [read.md](read.md) — paginate through the messages `count` reports
- [search.md](search.md) — find specific messages instead of walking all of them
- [contacts.md](contacts.md) — resolve a chat reference before inspecting it
- [../CONTRACT.md](../CONTRACT.md) — full JSON/TSV stability rules
