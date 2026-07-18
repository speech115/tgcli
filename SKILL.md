---
name: tgcli
description: Stateless Telegram CLI for reading dialogs, searching, safe message correspondence, downloading media, exporting data, and copying channels, non-forum supergroups, and private dialogs. Use it for any live Telegram task instead of the old MCP daemons.
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
| List unread dialogs | `tg --json dialogs --unread-only` |
| Filter dialogs by kind | `tg --json dialogs --kind channel` |
| Read recent messages | `tg --json read @channel --limit 20` |
| Read an older page | `tg --json read @channel --before-id 42 --limit 20` |
| Read newer or bounded messages | `tg --json read CHAT --after-id 42 --since ISO --until ISO --topic ID` |
| Search a dialog | `tg --json search @channel "query" --limit 20` |
| Search a dialog with filters | `tg --json search CHAT "query" --from @user --since ISO` |
| Search all dialogs | `tg --json search --all "query" --limit 20` |
| Read the latest message | `tg --json latest @channel` |
| Read an exact message | `tg --json message @channel 42` |
| Read a message with neighbours | `tg --json message @channel 42 --context 3` |
| Inspect a dialog | `tg --json info @channel` |
| Inspect dialog capabilities | `tg --json info @channel --full` |
| Count messages | `tg --json count @channel` |
| Download media | `tg --json media download https://t.me/channel/42 --parallel 4` |
| Preview a send | `tg --json send @channel "Hello" --preview` |
| Preview a reply/topic/silent send | `tg --json send CHAT "TEXT" --preview --reply-to ID --topic ID --silent` |
| Preview a file send | `tg --json send CHAT --file PATH --caption "TEXT" --preview` |
| Commit a preview | `tg --json send --commit p_9f3a` |
| Preview an edit | `tg --json edit @channel 42 "Corrected text" --preview` |
| Commit an edit | `tg --json edit --commit p_9f3a` |
| Preview a deletion | `tg --json delete @channel 42 --preview` |
| Commit a deletion | `tg --json delete --commit p_9f3a` |
| Preview a forward | `tg --json forward @source 42 @destination --preview` |
| Commit a forward | `tg --json forward --commit p_9f3a` |
| Mark a dialog read | `tg --json mark-read @channel` |
| Check local health | `tg --json doctor` |
| Export messages | `tg --json export messages @channel --output messages.jsonl` |
| Export subscribers | `tg --json export subscribers @channel --output subscribers.csv` |
| List channel clones | `tg --json clone status` |
| Preview a chat clone | `tg --json clone init SOURCE` |
| Commit clone destination creation | `tg --json clone init SOURCE --commit p_9f3a` |
| Copy or catch up a chat | `tg --json clone sync SOURCE` |

Send is deliberately two-step: preview first, then commit its single-use ID.
Previews expire after five minutes.

## Correspondence recipes

### Walk history

Start with `tg --json read CHAT --limit 100`. Save the returned
`page.oldest_id`; while it is not `null`, request the next older page with
`tg --json read CHAT --before-id OLDEST_ID --limit 100`. Stop on an empty
`messages` array. Results stay newest-first within each page, so reverse the
collected pages only if a consumer needs oldest-first processing.

### What's new since the last check

First discover pending conversations with
`tg --json dialogs --unread-only`. For each dialog, persist the last processed
message id in the caller's own state. Hold that `LAST_ID` fixed while fetching
the whole unread window: start with
`tg --json read CHAT --after-id LAST_ID --limit 100`, then paginate older
results with
`tg --json read CHAT --after-id LAST_ID --before-id OLDEST_ID --limit 100`,
where `OLDEST_ID` is the preceding page's `page.oldest_id`. Stop on an empty
`messages` array. Only after collecting every page, process the messages
oldest-first; once processing succeeds, advance the checkpoint to the maximum
collected message id. Do not use `unread` as a durable cursor: it is a Telegram
UI counter, whereas the message id is the stable per-dialog boundary.

### Send with retry

Create exactly one preview:
`tg --json send CHAT "TEXT" --preview`, then commit its `preview_id` with
`tg --json send --commit PREVIEW_ID`. If the commit fails due to a network or
runtime error, re-run that exact same `--commit PREVIEW_ID`; do not create a
new preview. Send and forward previews retain a Telegram `random_id`, so tgcli
can confirm the original operation without duplicating it. Do not retry exit 2
(a safety block) until its intentional cause is removed, or exit 5 until the
reported `retry_after` has elapsed.

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

`edit`, `delete`, and `forward` follow the same preview → commit rule as
`send`. `mark-read` is a content-free direct mutation, but it remains audited
and subject to those same safety gates. `doctor` is read-only: it reports
configured-account session presence, lock availability, local state
writability, Telegram authorization, and a top-level `ok` result.

## Account selection

Selection order is `--account` > `TGCLI_ACCOUNT` > the config default.
Available migration aliases are `main`, `recklessou`, and `teamsyncsage`.

## Migration note

The old `tools/telegram` MCP daemons have been decommissioned. Report a tgcli
regression rather than attempting to revive or use their old ports; restoring
them requires an explicit operator decision.
