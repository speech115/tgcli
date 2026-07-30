# Searching messages

Find messages by text, either inside one dialog or across every dialog you
can read. Use this instead of `read` plus client-side filtering whenever you
already know roughly what you are looking for.

## Search one dialog

```bash
tg --json search CHAT "query"
```

| Flag | Effect |
| --- | --- |
| `--limit N` | max results returned (default 20) |
| `--from USER` | restrict results to this sender (`@username` or id) |
| `--since ISO` | inclusive lower date/datetime bound |

`search CHAT QUERY` requires both a chat and a query positional. The query
and `--from` are passed straight into Telethon's `iter_messages(search=...,
from_user=...)`, which delegates to Telegram's own server-side message
search rather than re-scanning history client-side. `--since` is applied
client-side after messages come back: results stay newest-first and the
walk stops as soon as it reaches a message older than the bound, so a page
can come back shorter than `--limit`.

### JSON

Same `dialog` and message shape as `read`, plus the submitted `query`:

```json
{"dialog": {"id": -1001234, "name": "Channel"}, "query": "hello",
 "messages": [{"id": 42, "date": "2026-07-06T10:00:00+00:00",
               "from": {"id": 111, "name": "Alice", "username": null},
               "text": "hello", "media": null, "media_info": null,
               "voice_played": null,
               "reply_to": null, "quote_text": null, "permalink": null,
               "edited_at": null,
               "outgoing": false, "forwarded_from": null, "reactions": [],
               "custom_emoji": [],
               "topic_id": null, "grouped_id": null, "is_service": false}]}
```

`--plain` emits one row per message: `id`, `date`, `from_name`, `text`.

## Search across every dialog

```bash
tg --json search --all "query"
```

`--all` takes exactly one query positional (no separate `chat` argument) and
only combines with `--limit`; `--from` and `--since` are chat-scoped and are
rejected together with `--all`. The response has no `dialog` at the top
level — each hit carries its own source dialog instead:

```json
{"query": "hello", "messages": [{"id": 42,
 "dialog": {"id": -1001234, "name": "Channel"}}]}
```

Each message retains the full standard message shape shown above; only the
per-hit `dialog` field is added.

## See also

- [read.md](read.md) — paginate a dialog's full history instead of matching a query
- [batch.md](batch.md) — `search` is one of the ops `tg batch` allows
- [../CONTRACT.md](../CONTRACT.md) — §5 JSON shapes for `search` and `search --all`
