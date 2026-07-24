# Quickstart

A first session with `tg`, one command at a time. Assumes you've already
completed [install.md](install.md): `tg` is on your PATH and
`~/.config/tgcli/config.toml` has a working account.

## 1. Health check

```bash
tg --json doctor
```

Returns `{"accounts": [...], "ok": true|false}`; each account entry carries
its own `ok` and a `checks` object (session file present, lock free, state
writable). This is offline — no Telegram call — so it's safe to run first
and often.

## 2. List dialogs

```bash
tg --json dialogs --limit 20
```

Returns `{"dialogs": [...]}`, each with `id`, `name`, `kind`, `username`,
`unread`, `mentions`, and `last_message_at`. Use the id or `@username` from
here as the `CHAT` argument in later commands.

```bash
tg --json dialogs --unread-only
```

Same shape, filtered to dialogs with a nonzero `unread` count — the natural
starting point for "what's waiting for me."

## 3. Read a channel

```bash
tg --json read CHAT --limit 20
```

Returns `{"dialog": {...}, "messages": [...], "page": {...}}`. Look at
`page.oldest_id`: pass it as `--before-id` to page further back in history.

## 4. Search

```bash
tg --json search CHAT "query" --limit 20
```

Returns `{"dialog": {...}, "query": "query", "messages": [...]}` — same
message shape as `read`. Check `messages` length against your `--limit` to
tell whether you've exhausted the results.

## 5. Send: preview, then commit

```bash
tg --json send CHAT "hello" --preview
```

Returns `{"preview_id": "p_...", "to": {...}, "text": "hello", ...,
"expires_at": "..."}` — nothing has been sent yet. Read `to` to confirm the
resolved recipient, then commit within 5 minutes:

```bash
tg --json send --commit p_9f3a
```

Returns `{"preview_id": "p_9f3a", "message_id": 42}` — the message is now on
Telegram. If this second call fails on a network or runtime error, re-run the
exact same `--commit` id rather than creating a new preview.

## 6. Download one media file

```bash
tg --json media download CHAT ID
```

Returns `{"source": "...", "path": "...", "bytes": ..., "resumed": false,
"parallel": 1}`. Check `path` for where the file landed (`~/Downloads` by
default) and `resumed` to see whether it continued a prior interrupted
transfer.

## 7. Check local state

```bash
tg --json store stats
```

Returns an inventory of `~/.local/state/tgcli/`: live/expired/spent/pending
preview counts and sizes, `audit_log` and `sessions` sizes, and any relic
directories. Look at `previews_world_readable` — it should be `0`.

## See also

- [overview.md](overview.md) — the execution model and streams these commands share.
- [accounts.md](accounts.md) — running against a specific or non-default account.
- [safety.md](safety.md) — the full preview → commit contract and safety gates.
- [../CONTRACT.md](../CONTRACT.md) — exact JSON shapes for every command used above.
- [../../SKILL.md](../../SKILL.md) — the full command-routing table for agent use.
