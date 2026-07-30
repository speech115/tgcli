# Reading messages

The core reading surface: paginate a dialog's history, grab its latest
message, read one message with surrounding context, or walk a reply chain.
Everything here is read-only and safe to run repeatedly.

## Read recent messages

```bash
tg --json read CHAT --limit 20
```

| Flag | Effect |
| --- | --- |
| `--limit N` | max messages returned (default 20) |
| `--before-id ID` | only messages older than this id |
| `--after-id ID` | only messages newer than this id |
| `--since ISO` | inclusive lower date/datetime bound |
| `--until ISO` | upper date/datetime bound |
| `--topic ID` | restrict to one forum topic id |

Results are newest-first within a page. `--since` stops the walk early once
a message older than the bound is reached, so a page can come back shorter
than `--limit` even when older messages exist beyond the boundary.

### JSON

```json
{"dialog": {"id": -1001234, "name": "Channel"},
 "messages": [{"id": 42, "date": "2026-07-06T10:00:00+00:00",
               "from": {"id": 111, "name": "Alice", "username": null},
               "text": "hello", "media": null, "media_info": null,
               "voice_played": null,
               "reply_to": null, "quote_text": null, "permalink": null,
               "edited_at": null,
               "outgoing": false, "forwarded_from": null, "reactions": [],
               "custom_emoji": [],
               "topic_id": null, "grouped_id": null, "is_service": false}],
 "page": {"oldest_id": 42, "newest_id": 42}}
```

`page.oldest_id` / `page.newest_id` are the lowest/highest message ids in
*this* page, or `null` for an empty page. `--plain` emits one row per
message: `id`, `date`, `from_name`, `text`.

## Latest message

`tg --json latest CHAT` returns the single most recent message in the same
shape as `read`, under a `message` key instead of `messages`. No flags
beyond the globals.

## Read one message, with context

```bash
tg --json message CHAT ID --context 3
```

| Flag | Effect |
| --- | --- |
| `--context N` | include existing messages in the inclusive id window `ID-N`..`ID+N` (default 0) |

`--context` adds a `context` array of neighboring messages ordered by
ascending id; the target message is excluded from it (it is still the
`message` value). Missing ids inside the window are silently skipped.

## Reply chains

```bash
tg --json thread CHAT ID
tg --json thread CHAT ID --replies --depth 10
```

| Flag | Effect |
| --- | --- |
| `--replies` | also fetch comment/forum replies when a cheap thread API exists |
| `--depth N` | max ancestor steps to walk upward (default 20, hard cap 100) |
| `--limit N` | max replies returned when `--replies` is set (default 50) |

`thread` always returns `root`, `ancestors` (oldest→newest, walking
`reply_to` upward, excluding the root), and `replies` — empty unless
`--replies` is set **and** the root exposes a cheap reply thread, in which
case `note` explains why. `--plain` emits the same message TSV as `read`,
one row per root, then ancestors, then replies.

## Recipe: walk history

Page backward through a whole dialog, oldest page last:

```bash
tg --json read CHAT --limit 100
```

Save the returned `page.oldest_id`. While it is not `null`, request the next
older page:

```bash
tg --json read CHAT --before-id OLDEST_ID --limit 100
```

Stop when `messages` comes back empty. Each page stays newest-first; reverse
the collected pages only if a downstream consumer needs oldest-first order.

`page.oldest_id` is the cursor here, not a timestamp: message ids are
monotonic and stable per dialog, so `--before-id` always means "strictly
older than this exact message," with none of the clock-skew or
same-second ambiguity a date-based cursor would have.

## Recipe: what's new since the last check

Discover which dialogs have pending activity:

```bash
tg --json dialogs --unread-only
```

For each dialog, look up the last message id your caller already processed
— call it `LAST_ID`, held fixed for the whole run. Fetch the newest end of
the unread window:

```bash
tg --json read CHAT --after-id LAST_ID --limit 100
```

If that page's `page.oldest_id` is still newer than `LAST_ID`, there is more
unread history below it; keep paginating older pages within the same
boundary:

```bash
tg --json read CHAT --after-id LAST_ID --before-id OLDEST_ID --limit 100
```

where `OLDEST_ID` is the previous page's `page.oldest_id`. Stop once
`messages` comes back empty. Only after collecting every page, process the
messages oldest-first; once processing succeeds, advance your checkpoint to
the maximum message id you collected.

Do not use `dialogs[].unread` as that checkpoint: it is Telegram's own UI
counter, it resets whenever the chat is read in any Telegram client outside
your control, and it names no specific message. A message id you persisted
yourself is a durable, monotonic boundary; the unread counter is not.

## See also

- [search.md](search.md) — find specific messages instead of paging through all of them
- [dialogs.md](dialogs.md) — discover dialogs and their unread state first
- [batch.md](batch.md) — run several reads (including `read`, `thread`, `message`) in one session
- [../../SKILL.md](../../SKILL.md) — the same two recipes, condensed for agent routing
- [../CONTRACT.md](../CONTRACT.md) — §5 JSON shapes and stability rules
