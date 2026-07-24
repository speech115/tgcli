# Contacts and peer resolution

Turn a phone number, `@username`, `t.me` link, or numeric id into a peer
object, list or search your Telegram contacts, and find chats you share with
a user. Use this page before `send`/`read`/`search` whenever you have a raw
reference and need the resolved identity first.

## Resolve a reference

`tg resolve REF` accepts a `+`-prefixed E.164 phone number, `@username`, a
`t.me` link, or a numeric dialog id, and returns one peer object.

```bash
tg --json resolve @alice
tg --json resolve +15551234567
```

Every ref except a phone number goes through the standard chat-reference
parser and `get_entity`; there is nothing phone-specific about resolving a
username, link, or id.

### What phone resolution does and does not do

A `+<digits>` ref calls Telegram's `contacts.resolvePhone` only. It:

- looks up the peer Telegram associates with that phone number today, if any,
  and maps it to a full entity via the response's `users`/`chats` lists;
- returns exit 4 (not found) both when the lookup comes back empty and when
  Telegram reports `PHONE_NOT_OCCUPIED`.

It never:

- calls `contacts.importContacts` — resolving a phone number does not add it
  to your contact list, silently or otherwise;
- guarantees a hit — a number with privacy settings hiding it from lookups,
  or one not registered on Telegram, resolves to nothing.

Phone resolution (including raw `tg api contacts.resolvePhone`) shares a
client-side cooldown of about 3 seconds across all `tg` processes on the
machine; the reservation is atomic across concurrent processes. A call that
arrives too soon exits 5 (`FLOOD_WAIT`) with `retry_after` in the error JSON —
back off and retry rather than looping. See
[src/tgcli/resolve_phone.py](../../src/tgcli/resolve_phone.py) for the
cooldown implementation.

### JSON

```json
{"peer": {"id": 111, "type": "user", "username": "alice",
          "display_name": "Alice Smith", "is_contact": true,
          "is_bot": false}}
```

`type` is `bot` when the entity reports `bot`, `channel` for a broadcast
channel, `group` for a megagroup or basic group, otherwise `user`.
`display_name` is the chat title, or first+last name for a user/bot.

`--plain` emits one row: `id`, `type`, `username`, `display_name`.

## List and search contacts

```bash
tg --json contacts list
tg --json contacts search "ali"
tg --json contacts search "ali" --global
```

| Flag | Effect |
| --- | --- |
| `--global` (on `search`) | query Telegram's global user directory instead of local contacts |

`contacts list` calls `contacts.getContacts` once and maps every returned
user through the same peer shape as `resolve`. `contacts search QUERY`
without `--global` filters that same local list in Python — a
case-insensitive substring match over `display_name` and `username` — and
makes no additional Telegram request. `--global` instead calls
`contacts.search` and is capped at 50 results (`contacts.search`'s own
`limit` argument; there is no flag to raise it).

### JSON

```json
{"contacts": [{"id": 111, "type": "user", "username": "alice",
               "display_name": "Alice Smith", "is_contact": true,
               "is_bot": false}]}
```

Local `search` adds `"scope": "local"`; `--global` adds `"scope": "global"`.
`scope` is JSON-only.

`--plain` for both `list` and `search` emits the same four columns as
`resolve`: `id`, `type`, `username`, `display_name`.

## Mutual chats

`tg mutual-chats REF` lists groups and channels you both belong to.

```bash
tg --json mutual-chats @alice
```

`REF` resolves like `resolve` (phone numbers are not accepted here). It calls
`messages.getCommonChats` with a limit of 100. An empty `chats` list is
still success (`count: 0`); a missing user is exit 4; resolving a group or
channel instead of a user/bot is exit 2 (`BLOCKED`).

### JSON

```json
{"peer": {"id": 111, "type": "user", "username": "alice",
          "display_name": "Alice Smith", "is_contact": true,
          "is_bot": false},
 "chats": [{"id": 200, "type": "group", "username": "shared",
            "display_name": "Shared Group", "is_contact": false,
            "is_bot": false}],
 "count": 1}
```

`--plain` emits one row per chat: `id`, `type`, `username`, `display_name`.

## See also

- [read.md](read.md) — read messages once you have a resolved chat reference
- [dialogs.md](dialogs.md) — `tg info` for metadata on a chat you already hold
- [../CONTRACT.md](../CONTRACT.md) — §5 JSON shapes for `resolve`, `contacts`, `mutual-chats`
