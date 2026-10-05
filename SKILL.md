---
name: tgcli
description: Stateless Telegram CLI for reading dialogs, searching, safe message correspondence, downloading media, exporting data, and copying channels, supergroups, and private dialogs. Use it for any live Telegram task instead of the old MCP daemons.
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

- One process uses an account session at a time. A command whose session is
  busy waits up to 120 s for it (a stderr note says so), then exits 3; do not
  retry in a loop. For real concurrency, authorize a named role
  (`accounts login ALIAS --role job --phone PHONE`) and pass `--session-role job`.
- Never open a tgcli `.session` file with bare `python3` or a system/user-site
  Telethon. Use the `tg` entrypoint or `./.venv/bin/python` from this checkout;
  `tg doctor` reports the active Python and Telethon runtime under `runtime`.

## Command routing

| Task | Wrapped command |
|---|---|
| List configured accounts | `tg --json accounts list` |
| Show offline account / session status | `tg --json accounts show ALIAS` |
| Authorize / re-authorize a session | `tg --json accounts login ALIAS --phone PHONE`, then `--continue LOGIN_ID --code …` |
| Authorize a named session role | `tg --json accounts login ALIAS --role job --phone PHONE` |
| Poll a daemonless change feed | `tg --json changes --init` then `tg --json changes --cursor C [--wait N]` (the `v2:` cursor is account-bound; never edit or reuse it under another account) |
| Init local archive store | `tg --json archive init` |
| Opt a group/channel into archive scope | `tg --json archive add CHAT` / `remove CHAT` / `list` |
| Backfill selected dialogs into archive | `tg --json archive backfill CHAT [CHAT …] [--limit N]` |
| Backfill standing private dialogs | `tg --json archive backfill --private [--max-dialogs N] [--limit N]` |
| Sync archive from changes cursor | `tg --json archive sync [--max-events N] [--max-dialogs N] [--max-media N]` (applies full difference; caps catch-up RPCs and media downloads) |
| Transcribe archived voice/video notes | `tg --json archive transcribe [--limit N] [--max-attempts N]` (offline local FluidAudio/Parakeet queue) |
| Keep the archive current on a schedule | launchd or cron: `tg --session-role job --max-runtime 3000 --json archive sync` and `tg --max-runtime 3000 --json archive transcribe` |
| Rebaseline archive changes cursor | `tg --json archive rebaseline` |
| Offline archive status | `tg --json archive status` |
| Offline archive search | `tg --json archive search QUERY [--chat CHAT] [--from SENDER] [--since ISO] [--until ISO] [--kind KIND] [--transcripts-only] [--sort {relevance,date}] [--limit N] [--page N]` |
| Offline archive timeline | `tg --json archive read CHAT [--around-id ID | --around-date ISO] [--since ISO] [--until ISO] [--limit N]` |
| Offline archive history | `tg --json archive history CHAT MESSAGE_ID` |
| Remove a configured account | `tg --json accounts remove ALIAS --confirm` |
| Remove one session role | `tg --json accounts remove ALIAS --role job --confirm` |
| Import old-stack sessions | `tg --json accounts import` |
| The session died / a new machine | `tg --json accounts login ALIAS …` then `tg --json accounts show ALIAS` |
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
| Resolve a peer | `tg --json resolve @user` / `tg --json resolve +995…` |
| List / search contacts | `tg --json contacts list` / `tg --json contacts search "query"` |
| Mutual chats with a user | `tg --json mutual-chats @user` |
| Read-only batch (JSONL) | `tg batch <<'EOF'` / pipe JSONL ops (max 100; no doctor/mutations) |
| Media inventory (no download) | `tg --json media manifest @channel --type photo --limit 50` |
| Reply chain | `tg --json thread CHAT MESSAGE_ID [--replies] [--depth 20]` |
| Download media | `tg --json media download https://t.me/channel/42 --parallel 4` |
| Bulk download media | `tg --json media download @chan --message-ids 1,2 --type video --since ISO --limit 50 --output DIR` (filters combine; max 100) |
| Download story media | `tg --json media download https://t.me/channel/s/937 [--codec h264|hevc|av1]` (stories are not messages; single download only) |
| Transcribe a voice message | `tg --json transcribe @user 12345 [--timeout 120]` (Premium; waits for the server result) |
| Preview a send | `tg --json send @channel "Hello" --preview` |
| Preview a reply/topic/silent send | `tg --json send CHAT "TEXT" --preview --reply-to ID --topic ID --silent` |
| Preview a file send | `tg --json send CHAT --file PATH --caption "TEXT" --preview` |
| Preview a formatted send | `tg --json send CHAT "<b>bold</b> <tg-spoiler>hidden</tg-spoiler>" --format html --preview` |
| Commit a preview | `tg --json send --commit p_9f3a` |
| Propose a draft without sending | `tg --json draft set CHAT "TEXT" --preview` then `--commit` |
| Show / list drafts | `tg --json draft show CHAT` / `tg --json draft list` |
| Clear a draft | `tg --json draft clear CHAT --preview` then `--commit` |
| Preview an edit | `tg --json edit @channel 42 "Corrected text" --preview` |
| Preview a formatted edit | `tg --json edit CHAT 42 "<b>bold</b> <blockquote expandable>quote</blockquote>" --format html --preview` |
| Commit an edit | `tg --json edit --commit p_9f3a` |
| Harvest custom-emoji ids from a post | `tg --json message @channel 42` → read `custom_emoji[].id` |
| Preview a deletion | `tg --json delete @channel 42 --preview` |
| Commit a deletion | `tg --json delete --commit p_9f3a` |
| Preview a forward | `tg --json forward @source 42 @destination --preview` |
| Commit a forward | `tg --json forward --commit p_9f3a` |
| Mark a dialog read | `tg --json mark-read @channel` |
| Mark a dialog unread | `tg --json mark-unread @channel` |
| Pin / unpin a dialog | `tg --json dialog pin @channel` / `tg --json dialog unpin @channel` |
| Archive / unarchive a dialog | `tg --json dialog archive @channel` / `tg --json dialog unarchive @channel` |
| Mute / unmute a dialog | `tg --json dialog mute @channel --until ISO` / `--forever` / `unmute` |
| Check local health | `tg --json doctor` |
| Export messages | `tg --json export messages @channel --output messages.jsonl [--after-id ID] [--append|--resume]` |
| Export subscribers | `tg --json export subscribers @channel --output subscribers.csv` |
| List channel clones | `tg --json clone status` |
| Export clone state as v2 JSON | `tg clone export-state SOURCE` |
| Preview a chat clone | `tg --json clone init SOURCE` |
| Preview posts-only clone | `tg --json clone init SOURCE --no-comments` |
| Commit clone destination creation | `tg --json clone init SOURCE --commit p_9f3a` |
| Copy or catch up a chat | `tg --json clone sync SOURCE` |
| Backfill missing forward prefixes on an existing clone | `tg --json clone refresh SOURCE` |

Send is deliberately two-step: preview first, then commit its single-use ID.
Previews expire after five minutes.

## Formatting and custom emoji

`send` and `edit` take `--format {plain,md,html}`. `edit` defaults to `plain`
(verbatim, no entities); `send` defaults to `md`. Use `html` for the full
Telegram entity set: `<b>`/`<i>`/`<u>`/`<s>`, `<blockquote>` and
`<blockquote expandable>`, `<tg-spoiler>`, `<code>`/`<pre>`, `<a href>`, and
`<tg-emoji emoji-id="ID">glyph</tg-emoji>` for custom (premium) emoji. Offsets
are UTF-16-correct, so emoji do not shift the markup.

Custom emoji cannot be invented — reuse real ids. Every read
(`read`/`search`/`message`/`export`) now returns `custom_emoji` per message:
`{id, emoji, offset, length}`, where `id` is a **decimal string** (the reusable
`emoji-id`; not a JSON number, so JS parsers cannot round it). Harvest an id
from any readable post (e.g. read a channel that uses the emoji you want),
then drop `<tg-emoji emoji-id="ID">` into a `--format html` send/edit. Sending
custom emoji requires the account to have Telegram Premium.

Voice messages additionally expose `voice_played`: `false` means Telegram's
`media_unread` flag is set, `true` means it is clear, and `null` means the
message is not a voice message or the flag was unavailable.

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

## `tg run` — anything without a command

When no wrapped command covers the task, write a short Python script. It gets
`client` (Telethon, already authorized and paced), `functions`, `types`,
`account`, and `msg(message)`, which returns the same JSON object as `tg read`.
Bundle related calls into one script instead of one process per call.

```bash
tg run - <<'PY'
import json
full = await client(functions.channels.GetFullChannelRequest("@channel"))
print(json.dumps({"about": full.full_chat.about,
                  "members": full.full_chat.participants_count}))
for m in await client.get_messages("@channel", limit=5):
    print(json.dumps(msg(m), ensure_ascii=False))
PY
```

Scripts may only read. A request that changes Telegram is refused (exit 2)
unless you pass `tg run --write SCRIPT`, which audits every write first; use
`--write` only when the owner asked for that change. Prefer a wrapped command
for sends, edits, deletes, and forwards: it shows a preview first.

## `tg api` — last resort

Use `tg api` only when no wrapped command covers the task. Prefer a wrapped
command whenever one exists. Read calls are default-deny and limited to the
ADR-0010 allowlist. Writes require `--write`; destructive verbs also require
an exact typed `--confirm METHOD`. `auth.*` and `account.*` writes are never
callable, whatever the method name (ADR-0092) — session and account
lifecycle stays with `tg accounts`. Authorized writes are audited.

## Safety gates

`--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block mutations before
network work. A block is exit 2; do not retry it until the safety condition is
intentionally changed.

### Request governor (ADR-0072)

Every Telegram request is paced and gated per request type by a persisted
governor: history reads and dialog enumeration wait 3 s between requests,
and a flood arms a per-type cooldown that refuses locally (exit 5,
`retry_after`) with zero RPCs. `tg doctor` reports active cooldowns and
ledger health — it is the one command that works while everything else
refuses. A degraded governor ledger (`governor_degraded: true`) marks the
account unhealthy and makes governed commands fail closed (exit 2,
`BLOCKED`) until the ledger file is repaired. The default `--timeout` is a
hang detector that ignores governed sleep; `--max-runtime` bounds a long
run as a normal stop. Exit 5 means wait out `retry_after` — never retry
FloodWait in a tight loop; the governor probes once at half the wait and
clears early-lifted limits itself.

### Clone peer budget (ADR-0045)

`clone init` creates 1–2 Telegram peers (channel, plus discussion group when
comments are enabled). Prefer at most ~one peer-creating init per account per
day. Check the preview's `peers_to_create` before `--commit`. Use
`--no-comments` when a posts-only clone is enough.

`edit`, `delete`, and `forward` follow the same preview → commit rule as
`send`. `mark-read`, `mark-unread`, and `dialog pin`/`unpin` are content-free
direct mutations, but they remain audited and subject to those same safety
gates. `doctor` is read-only: it reports configured-account session presence,
lock availability, local state writability, Telegram authorization, and a
top-level `ok` result.

## Account selection

Selection order is `--account` > `TGCLI_ACCOUNT` > the config default.
`--session-role NAME` (ADR-0062) selects a named session beside the primary;
omit it for the primary. A missing role is exit 3 with remediation — never a
silent fallback. Available migration aliases are `main`, `recklessou`, and
`teamsyncsage`.

## Migration note

The old `tools/telegram` MCP daemons have been decommissioned. Report a tgcli
regression rather than attempting to revive or use their old ports; restoring
them requires an explicit operator decision.
