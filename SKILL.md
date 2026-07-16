---
name: tgcli
description: Stateless Telegram CLI for reading dialogs, searching, downloading media, safely sending messages, exporting data, and copying channels, non-forum supergroups, and private dialogs. Use it for any live Telegram task instead of the old MCP daemons.
---

# tgcli

Use `tg` for live Telegram tasks. It is stateless: every invocation opens the
selected account session, does one operation, and exits.

## Golden rules

- Always pass `--json` for machine use. Stdout is contract data; progress,
  warnings, and errors are on stderr.
- Check the exit code. Never parse human-readable output.

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | runtime error |
| 2 | blocked by safety policy |
| 3 | config or authentication error |
| 4 | not found |
| 5 | rate limited; JSON error includes `retry_after` |

- Run one process per account at a time. Exit 3 can mean a busy session lock;
  retry in a few seconds.

## Command routing

| Task | Wrapped command |
|---|---|
| List configured accounts | `tg --json accounts list` |
| Import old-stack sessions | `tg --json accounts import` |
| List dialogs | `tg --json dialogs --limit 50` |
| Read recent messages | `tg --json read @channel --limit 20` |
| Search a dialog | `tg --json search @channel "query" --limit 20` |
| Read the latest message | `tg --json latest @channel` |
| Read an exact message | `tg --json message @channel 42` |
| Inspect a dialog | `tg --json info @channel` |
| Count messages | `tg --json count @channel` |
| Download media | `tg --json media download https://t.me/channel/42 --parallel 4` |
| Preview a send | `tg --json send @channel "Hello" --preview` |
| Commit a preview | `tg --json send --commit p_9f3a` |
| Export messages | `tg --json export messages @channel --output messages.jsonl` |
| Export subscribers | `tg --json export subscribers @channel --output subscribers.csv` |
| List channel clones | `tg --json clone status` |
| Preview a chat clone | `tg --json clone init SOURCE` |
| Commit clone destination creation | `tg --json clone init SOURCE --commit p_9f3a` |
| Copy or catch up a chat | `tg --json clone sync SOURCE` |

Send is deliberately two-step: preview first, then commit its single-use ID.
Previews expire after five minutes.

## `tg api` — last resort

Use `tg api` only when no wrapped command covers the task. Prefer a wrapped
command whenever one exists. Read calls are default-deny and limited to the
ADR-0010 allowlist. Writes require `--write`; destructive verbs also require
an exact typed `--confirm METHOD`. The permanent denylist is never callable,
and authorized writes are audited.

## Safety gates

`--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block mutations before
network work. A block is exit 2; do not retry it until the safety condition is
intentionally changed.

## Account selection

Selection order is `--account` > `TGCLI_ACCOUNT` > the config default.
Available migration aliases are `main`, `recklessou`, and `teamsyncsage`.

## Migration note

The old `tools/telegram` MCP daemons have been decommissioned. Report a tgcli
regression rather than attempting to revive or use their old ports; restoring
them requires an explicit operator decision.
