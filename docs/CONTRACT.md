# CLI Automation Contract

Version: 1.2.21 (tracks the package release; see `CHANGELOG.md` and
`pyproject.toml`). Any change here lands in the same commit as the code
change (AGENTS.md / ADR-0038).

## 1. Invocation

```
tg [global-flags] <command> [subcommand] [args] [options]
```

Commands include offline local-state helpers (`store`, `clone status`,
`accounts import`) that need no config or Telegram session, plus the usual
account-scoped surface (`doctor`, reads, mutations, …).

Global flags (available on every command):

| Flag | Meaning |
|------|---------|
| `--account <alias>` | account alias from config; default: config `default_account` |
| `--session-role <name>` | named session role beside the primary (ADR-0062); omit for primary |
| `--json` | machine output: one JSON document to stdout |
| `--plain` | stable TSV to stdout (no colors, no alignment) |
| `--readonly` | hard-block any mutating call in this invocation |
| `--timeout <sec>` | overall invocation deadline covering preflight and execution (default 60; no default deadline for media, exports, or `clone init|sync|refresh`, which may wait out a short FloodWait; `accounts login` defaults to 120 and `--continue` takes none) |
| `-v/--verbose` | Python and Telethon debug diagnostics on stderr for this invocation |

Env equivalents: `TGCLI_ACCOUNT`, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1`.
Flag beats env, env beats config.

`--session-role NAME` resolves to `sessions/<session>@<NAME>.session` before
the client opens. Role names use the same charset/length rules as account
aliases; `primary` is reserved (omit the flag to use the default session).
There is **no implicit fallback**: a missing or unauthorized role is exit 3
(`CONFIG`) with remediation `run: tg accounts login <alias> --role NAME`,
never a silent switch to the primary. Symmetrically, omitting the flag always
uses the primary even when roles exist. A role appears only through an
explicit interactive `accounts login --role` — never created by using the
flag.

## 2. Streams

- **stdout** — contract data only. With `--json`: exactly one JSON document.
  With `--plain`: TSV rows. Default (human) mode: readable tables/text.
- **stderr** — everything else: progress, hints, warnings, error messages.
  With `--json`, the final error is also mirrored to stderr as a single-line
  JSON object: `{"error": {"code": "FLOOD_WAIT", "message": "...", "retry_after": 42}}`.
  With `--json`, the error envelope is written to stdout as the run's single
  JSON document, then the identical line is copied to stderr as that last-line
  mirror — a `--json` caller may read either stream for the same object.
  It is the **last** line of stderr, not the whole stream: progress and
  warnings legitimately precede it. Every failure yields exactly one
  envelope — including an untranslated network or RPC failure, which is
  reported as `RUNTIME` rather than a traceback; the traceback appears only
  under `-v`.
- `clone sync` prints progress to stderr in every mode, including `--json`
  (ADR-0049): `[sync <source_id>] <n>/~<total> · <activity>`, where `<n>` is
  messages copied into the destination so far (earlier runs included),
  `<total>` the best-effort source message count (`?` when unavailable), and
  `<activity>` a transport (`forwarded` / `reuploaded` / `snapshots`), a phase
  (`comments`, `roster`), or a ~5 MB transfer mark
  (`reupload · <file> · download|upload <done>/<size> MB (<pct>%)`). These
  lines are plain — no `\r`, cursor control, or color — and **informative,
  not contract data**: the shape may change without a version bump, agents
  must not parse it, and `2>/dev/null` silences it.

## 3. Stability Rules

- JSON: adding fields is allowed anytime; renaming/removing/retyping fields
  is a breaking change → requires ADR + major version bump.
- TSV: column order is frozen per command; new columns append at the end.
- Datetimes: ISO 8601 UTC (`2026-07-06T12:00:00+00:00`). IDs: as integers.
- Long options must be spelled in full. Option-prefix abbreviations (`--c`
  for `--confirm`, `--w` for `--write`) are not part of the contract and are
  rejected as unrecognized arguments.
- A value that begins with `-` is read as options, not as a positional: pass
  the flags first and such values after `--`
  (`tg draft set --preview --json -- @chat -hi`).

## 4. Exit Codes

| Code | Meaning | Typical cause |
|------|---------|---------------|
| 0 | success | |
| 1 | runtime error | network, unexpected exception |
| 2 | blocked by safety policy | `--readonly` + mutating command, `TGCLI_NO_SEND` |
| 3 | config/auth error | missing `--account` / `default_account`, dead session, bad api_id |
| 4 | not found | unknown dialog, message id, media; unknown alias on `accounts show\|remove` (lookup) |
| 5 | rate limited | FloodWait longer than threshold; `retry_after` in error JSON |

Exit 1 covers several distinguishable error codes in the JSON envelope:
`TIMEOUT` (the `--timeout` deadline elapsed), `RUNTIME` (an untranslated
network or RPC failure), and `USAGE` (argument misuse detected after the
global flags parsed). A parse-time usage error still exits 1 with the usage
text on stderr and nothing on stdout, because argv was never parsed far
enough to know `--json` was asked for. A stdout pipe closed by the reader
(`tg … --json | head`) exits 0 and journals `BROKEN_PIPE`; a run killed by
SIGINT, SIGTERM, or SIGHUP journals `INTERRUPTED` / `TERMINATED` with
`exit_code` 128+signal and then dies by that signal, so the shell still sees
a signal death. SIGKILL cannot be caught and journals nothing.

## 5. Core JSON Shapes (phase 1–4)

`tg dialogs [--unread-only] [--kind {user,group,channel}] --json`:
```json
{"dialogs": [{"id": -1001234, "name": "Channel", "kind": "channel",
              "username": "chan", "unread": 3,
              "mentions": 0,
              "last_message_at": "2026-07-06T11:59:00+00:00"}]}
```

Megagroup dialogs are classified as `group` even though Telethon also marks
them as channels; broadcast channels remain `channel`.

`tg read <chat> --json`:
```json
{"dialog": {"id": -1001234, "name": "Channel"},
 "messages": [{"id": 42, "date": "2026-07-06T10:00:00+00:00",
               "from": {"id": 111, "name": "Alice", "username": null},
               "text": "hello", "media": null, "media_info": null,
               "media_kind": null,
               "voice_played": null,
               "reply_to": null, "quote_text": null, "permalink": null,
               "edited_at": null,
               "outgoing": false, "forwarded_from": null, "reactions": [],
               "custom_emoji": [],
               "topic_id": null, "grouped_id": null, "is_service": false}],
 "page": {"oldest_id": 42, "newest_id": 42}}
```

All message-shape additions since 0.1 are additive; `media` remains the Telethon
class name string, `media_info` carries structured metadata, and `media_kind` is
the normalized category (`photo`, `video`, `video_note`, `audio`, `voice`, or
`document`) or `null`. `custom_emoji` is a
(possibly empty) list of the message's custom (premium) emoji as
`{"id", "emoji", "offset", "length"}`, where `id` is the reusable `document_id`
as a **decimal string** (so IEEE-754 JSON number parsers cannot round it; the
string is the `emoji-id` accepted by `--format html`) and `emoji` is the
fallback unicode glyph; offsets are UTF-16 code units. This lets an agent
harvest custom-emoji ids from any readable post and reuse them when composing
formatted messages. `reply_to` is the parent message id as an int for same-chat
replies (and when `reply_to_peer_id` is absent); for a cross-chat quote reply it
is `{"id": <msg_id>, "peer": <bot-api peer id>}` so the id is not resolved
against the chat being read. `quote_text` is the quoted fragment string when
the reply header carries one, otherwise `null`.
`voice_played` is `false` when Telegram reports `media_unread: true`, `true`
when it reports `media_unread: false`, and `null` for non-voice messages or when
Telegram did not provide the flag.

`read` accepts `--before-id INT` (messages older than an id), `--after-id INT`
(messages newer than an id), `--since ISO`, `--until ISO`, and `--topic INT`
(forum topic id). The additive `page` object reports the lowest and highest
returned message ids, or `null` for an empty page. `--since` preserves
newest-first output and stops when it reaches the lower date boundary.

`tg search <chat> <query> --json` uses the same `dialog` and message shapes as
`read`, adding the submitted query:
```json
{"dialog": {"id": -1001234, "name": "Channel"}, "query": "hello",
 "messages": [{"id": 42, "date": "2026-07-06T10:00:00+00:00",
               "from": {"id": 111, "name": "Alice", "username": null},
               "text": "hello", "media": null, "media_info": null,
               "media_kind": null,
               "voice_played": null,
               "reply_to": null, "quote_text": null, "permalink": null,
               "edited_at": null,
               "outgoing": false, "forwarded_from": null, "reactions": [],
               "custom_emoji": [],
               "topic_id": null, "grouped_id": null, "is_service": false}]}
```

Chat-scoped `search <chat> <query>` accepts `--from @username` to restrict
results to that sender and `--since ISO` as an inclusive lower date boundary.
Search remains newest-first and stops when it reaches a message older than
`--since`.

`tg search --all <query> --json` searches across accessible dialogs. Its top
level response has `query` and `messages`; each message retains the standard
message shape and adds a per-hit `dialog` object with the source dialog `id`
and `name`:
```json
{"query": "hello", "messages": [{"id": 42,
 "dialog": {"id": -1001234, "name": "Channel"}}]}
```

`search --all` takes exactly one query positional and may use `--limit`.
`--from` and `--since` are chat-scoped filters and are incompatible with
`--all`. The scoped form remains `search <chat> <query>`.

`tg latest <chat> --json` and `tg message <chat> <message_id> --json` return
one message in that same shape:
```json
{"dialog": {"id": -1001234, "name": "Channel"},
 "message": {"id": 42, "date": "2026-07-06T10:00:00+00:00",
             "from": {"id": 111, "name": "Alice", "username": null},
             "text": "hello", "media": null, "media_info": null,
             "media_kind": null,
             "voice_played": null,
             "reply_to": null, "quote_text": null, "permalink": null,
             "edited_at": null,
             "outgoing": false, "forwarded_from": null, "reactions": [],
             "custom_emoji": [],
             "topic_id": null, "grouped_id": null, "is_service": false}}
```

`tg message <chat> <message_id> --context N --json` adds an optional
`context` array containing existing messages in the inclusive ID window from
`message_id - N` through `message_id + N`; the target message is excluded and
neighbors are ordered by ascending id.

`tg info <chat> --json`:
```json
{"id": -1001234, "name": "Channel", "kind": "channel", "username": "chan"}
```

`tg info <chat> --full --json` adds `role`, `can`, `slowmode_seconds`,
`participants_count`, and `about` to that base shape. `role` is `"creator"`,
`"admin"`, `"member"`, or `null` for a user dialog. `can` contains
best-effort `send_messages`, `send_media`, `pin_messages`, `delete_messages`,
and `edit_messages` booleans (or `null` when Telegram does not expose enough
rights data). `slowmode_seconds`, `participants_count`, and `about` come from
full channel metadata for channels and megagroups; they are `null` for user
dialogs and basic groups. The `can` map is a preflight aid, not authorization
truth — Telegram remains the authority. A creator reports all listed
capabilities as `true`. For an admin, broadcast posting capabilities use the
available `post_messages` flag, and pin/delete/edit use their respective
Telegram admin-right flags; tgcli does not infer ungranted admin capabilities.

`tg count <chat> --json`:
```json
{"dialog": {"id": -1001234, "name": "Channel"}, "count": 73}
```

`tg resolve <ref> --json`:
```json
{"peer": {"id": 111, "type": "user", "username": "alice",
          "display_name": "Alice Smith", "is_contact": true,
          "is_bot": false}}
```

`REF` is a `+<digits>` phone number, `@username`, `t.me` link, or numeric
dialog id. A phone ref calls `contacts.resolvePhone` only — it never calls
`contacts.importContacts` — and maps the returned peer to its entity via the
response's `users`/`chats` lists; an empty result is exit 4 (not found).
Phone resolution (and raw `tg api contacts.resolvePhone`) shares a
client-side cooldown of about 3 seconds across `tg` processes; reservation is
atomic across concurrent processes. A call that arrives too soon exits 5
(`FLOOD_WAIT`) with `retry_after`. Telegram's `PHONE_NOT_OCCUPIED` is exit 4.
Every other ref goes through the standard chat-reference parser and
`get_entity`. `type` is one of `user`, `bot`, `group`, `channel`: `bot` when
the entity reports
`bot`, `channel` for a broadcast channel, `group` for a megagroup or basic
group, otherwise `user`. `display_name` is the chat title, or first+last name
for a user/bot. `is_contact` and `is_bot` reflect the entity's own Telegram
flags.

```
tg contacts list
tg contacts search <query> [--global]
```

`tg contacts list --json`:
```json
{"contacts": [{"id": 111, "type": "user", "username": "alice",
               "display_name": "Alice Smith", "is_contact": true,
               "is_bot": false}]}
```

`list` calls `contacts.getContacts` once and maps every returned user through
the same `peer` shape as `resolve`.

`tg contacts search <query> --json` filters `contacts list`'s result in
Python by a case-insensitive substring match over `display_name` and
`username`; it makes no additional Telegram request. The response adds
`"scope": "local"`. `--global` instead calls `contacts.search` with `q` set
to `<query>` and returns its `users` mapped the same way, with
`"scope": "global"`; local `contacts list` is not consulted for `--global`.
`--global` results are capped at 50 (`contacts.search`'s own `limit`
argument); there is no flag to raise it.

```
tg mutual-chats <user>
```

`tg mutual-chats <user> --json`:
```json
{"peer": {"id": 111, "type": "user", "username": "alice",
          "display_name": "Alice Smith", "is_contact": true,
          "is_bot": false},
 "chats": [{"id": 200, "type": "group", "username": "shared",
            "display_name": "Shared Group", "is_contact": false,
            "is_bot": false}],
 "count": 1}
```

`mutual-chats` resolves `<user>` like `resolve` (non-phone refs) and calls
`messages.getCommonChats` with `limit` 100. `peer` is the resolved user/bot;
`chats` are common groups/channels mapped through the same `peer` shape.
An empty `chats` list is success (`count` 0). A missing user is exit 4. A
non-user/non-bot peer (group or channel) is exit 2 (`BLOCKED`). Plain rows
are one TSV line per chat: `id`, `type`, `username`, `display_name`.

```
tg draft show CHAT
tg draft list
tg draft set CHAT TEXT --preview [--format {plain,md,html}] \
  [--reply-to MESSAGE_ID] [--topic TOPIC_ID]
tg draft set --commit PREVIEW_ID
tg draft clear CHAT --preview
tg draft clear --commit PREVIEW_ID
```

Message drafts (ADR-0039) leave prepared text in a dialog input box without
sending it. There is no `draft send`: the human presses send in the Telegram
client. `set` mirrors `send`'s formatting defaults (`--format` default `md`)
and reply/topic flags; `--file` is out of scope for v1.

`tg draft show CHAT --json` returns one draft object (not a message):

```json
{"draft": {"chat": {"id": 111, "name": "Alice"}, "text": "hello",
 "custom_emoji": [], "reply_to_msg_id": null, "topic_id": null,
 "date": "2026-07-23T12:00:00+00:00", "is_empty": false}}
```

An empty or missing draft is still success with `text: ""`, `is_empty: true`,
and `date: null`. `date` is the draft's last-edited time, not a send time.
`custom_emoji` follows the same `{id, emoji, offset, length}` shape as
messages (ADR-0030).

`tg draft list --json` returns `{"drafts":[…]}` — every non-empty draft on
the account, each in the same object shape. `show` and `list` are typed read
operations (`draft.show`, `draft.list`) and are available under
`TGCLI_READONLY` and inside `tg batch`.

`draft set` / `draft clear` use the same preview→commit handshake as `edit`.
A set preview carries `old_text` (the current draft body that will be
overwritten), `text`, `format`, `reply_to`, `topic`, and `to`:

```json
{"preview_id":"p_9f3a","to":{"id":111,"name":"Alice"},"old_text":"half-written",
 "text":"**reply**","format":"md","reply_to":42,"topic":null,
 "expires_at":"2026-07-23T12:05:00+00:00"}
```

A clear preview carries `old_text` and `to` only. Commits return
`{"preview_id":"p_9f3a","draft":{…}}` with the resulting draft object.
`--commit` accepts only a preview id (`expected_kind` `draft-set` /
`draft-clear`). Preview creation is non-mutating and permitted under
readonly gates; commit is blocked by `--readonly` / `TGCLI_READONLY` /
`TGCLI_NO_SEND`. Authorised commits append `draft-set` /
`draft-clear` audit records before the network call and
`draft-set-result` / `draft-clear-result` after success.
Immediately before saving, a commit re-reads the complete observable draft
state — text, reply, topic, and every formatting entity — and fails closed
(exit 2) if it no longer matches the preview's internal snapshot. A matching
requested state is treated as the successful retry of an already-applied save.
Telegram exposes no conditional-save/version token, so an edit made after that
read and before `saveDraft` remains a residual race; callers must make a new
preview after any blocked commit.

`tg media download <t.me/link|chat> [message_id] --json`:
```json
{"source": "@channel:42", "path": "/Users/me/Downloads/clip.mp4",
 "bytes": 104857600, "resumed": false, "parallel": 1}
```

The command accepts public `t.me/<username>/<message_id>` and private
`t.me/c/<channel_id>/<message_id>` links, or a chat reference plus message
ID. Without `--output`, the final file is written to `~/Downloads`; an
existing final path is refused and never overwritten. Progress is emitted only
to stderr. Single-stream transfer resumes a matching interrupted partial file
from `~/.local/state/tgcli/downloads/`; `--parallel N` is opt-in, requires a
positive `N`, and starts a fresh offset-based transfer.

Bulk mode (ADR-0032) activates with `--message-ids id,id` and/or filter flags
`--type` / `--since` / `--limit` on a chat reference (no single `message_id`).
When explicit IDs and filters are combined, the filters apply to those
messages and `--limit` caps the filtered downloads, preserving input-ID order.
Do not combine a positional `message_id` with bulk flags (exit 2). Hard cap
**100** downloads per invocation (`--message-ids` length and filter `--limit`;
default filter limit 100). `--output` is a destination directory. Success /
partial JSON:
`{"dialog":{…},"items":[{"message_id","path","bytes","resumed"}],"count":N,
"failed":[{"message_id","error"}],"skipped":[{"message_id","reason"}]}`.
`count` is the number of files downloaded in this run only. A per-item
`output path already exists: …` becomes an additive `skipped` row and the
loop continues. Per-item NotFound goes into `failed` and continues;
FloodWait/auth/other policy failures stop the loop. Any non-empty `failed` →
nonzero exit (typically 4) while still emitting the JSON document on
`--json`; successful files remain on disk. Unbounded `--all` is not offered.

```
tg media manifest CHAT [--type photo|video|video_note|audio|voice|document] [--since ISO] [--limit N]
```

`media manifest` is a dry-run inventory (ADR-0029): it walks recent messages
with `iter_messages` (default `--limit` 100), keeps only those with media, and
never downloads. Each item is
`{"message_id":42,"type":"photo","size":1234,"mime":"image/jpeg","filename":"a.jpg"}`.
`--type` filters to one kind; `--since` drops older messages (newest-first walk
stops at the first message older than the bound). Success JSON:
`{"dialog":{"id":-1001234,"name":"Channel"},"items":[...],"count":N}`. Plain
rows are `message_id`, `type`, `size`, `mime`, `filename`.

### TSV Shapes

`dialogs` retains its phase-1 columns and appends `mentions` as the final
column. `read` and `search` output one row per
message as `id`, `date`, `from_name`, `text`; `latest` and `message` use the
same single-row shape. `info` outputs `id`, `kind`, `username`, `name`.
`count` outputs one `count` value. `info --full` keeps the same `info` TSV
columns; its additive fields are JSON-only. `resolve` outputs one row:
`id`, `type`, `username`, `display_name`. `contacts list` and `contacts
search` output the same four columns, one row per contact; `scope` is
JSON-only.
`media download` outputs `path`, `bytes`, `resumed`, `parallel`. `media
manifest` outputs `message_id`, `type`, `size`, `mime`, `filename`. `draft show`
and `draft list` output `chat_id`, `chat_name`, `text`, `is_empty`. `send` preview
rows retain their existing columns and append `file`, `reply_to`, `format`. `edit` preview
rows are `preview_id`, `message_id`, `old_text`, `text`, `format`; `delete` preview rows
are `preview_id`, `message_id`, `text`; `forward` preview rows are
`preview_id`, `source`, `message_id`, `destination`. `draft set` preview rows are
`preview_id`, `chat_id`, `old_text`, `text`, `format`; `draft clear` preview rows
are `preview_id`, `chat_id`, `old_text`, empty text, empty format. Send, edit, delete, and
forward commit rows are `preview_id`, `message_id`. Draft set/clear commit rows are
`preview_id`, `chat_id`, `text`, `is_empty`. `mark-read` rows are
`dialog_id`, `read`.

```
tg send CHAT (TEXT | --file PATH [--caption TEXT]) --preview \
  [--reply-to MESSAGE_ID] [--topic TOPIC_ID] [--silent]
```

`tg send CHAT TEXT --preview --json` or a file preview returns:
```json
{"preview_id": "p_9f3a", "to": {"id": 111, "name": "Alice"},
 "text": "hello", "file": null, "file_size": null, "file_sha256": null,
 "reply_to": null,
 "topic": null, "silent": false, "expires_at": "2026-07-06T12:05:00+00:00"}
```
For a file preview, `text` is the optional caption, `file` is its absolute
path, `file_size` is its byte size, and `file_sha256` is the lowercase SHA-256
digest of the same open byte stream. Immediately before upload, commit opens
that absolute source once and copies it into a unique temporary snapshot while
computing the snapshot's size and digest. A mismatch is blocked with exit 2;
otherwise only the verified snapshot is uploaded, so later replacement of the
original path cannot change the sent bytes. The snapshot is removed after
success or any upload, request, or confirmation failure. MIME type and Telegram
filename continue to derive from the original path. `--caption` requires
`--file`; a file send cannot take positional text. The stored preview
additionally includes the target, `kind: "send"`, the chosen `format`, and a
positive `random_id` for the later idempotent commit path. `send` accepts
`--format {plain,md,html}` (default `md`, preserving the historical
Markdown-to-entity behavior for text and captions); `plain` sends verbatim and
`html` uses the same entity set as `edit --format html` (bold/italic/quote/
expandable quote/spoiler/code/links/`tg-emoji` custom emoji). The commit
re-renders from the stored `format` and passes explicit entities.
`--format html` fails closed: markup the parser would silently delete from
the body is rejected with exit 2 (`BLOCKED`) at preview time — before a
preview record is written and before anything is sent, edited, or saved as a
draft. That covers unterminated markup (`if a<b then c`), a tag outside the
supported set (`List<int> is generic`), an unclosed supported tag, and HTML
comments, declarations, or processing instructions. A bare `<` followed by a
space or a digit (`5 < 6 and 7 > 8`) is ordinary text and still renders
unchanged. The same check runs at commit, so a preview minted by an older
version cannot publish a truncated body.
`tg edit CHAT MESSAGE_ID TEXT --preview [--format {plain,md,html}]` records the
chosen format in the preview (default `plain`). Unlike `send`, edit does not
apply the client's default parse mode: `plain` sends TEXT verbatim with no
entities (parse disabled), so literal `*`, `_`, `<` survive. `md` uses Telethon
Markdown. `html` supports the full entity set — `<b>`/`<i>`/`<u>`/`<s>`,
`<blockquote>` and `<blockquote expandable>`, `<tg-spoiler>` (or
`<span class="tg-spoiler">`), `<code>`/`<pre>`, `<a href>`, and
`<tg-emoji emoji-id="…">` custom emoji. Offsets are computed in UTF-16 code
units, so surrogate-pair emoji shift following entities correctly. The commit
re-renders TEXT from the stored `format` and passes explicit
`formatting_entities`.
Previews expire after five minutes. A send commit moves its preview through
`.json` → `.pending` → `.used`: a failed commit may be re-committed; Telegram
deduplicates by `random_id` within the preview TTL. Only a confirmed send marks
the preview used, and only after its result audit record persists. Commit JSON
is `{"preview_id": "p_9f3a", "message_id": 42}`.
All `--preview` invocations for `send`, `edit`, `delete`, `forward`,
`draft set`, and `draft clear` are non-mutating:
they may resolve or read a Telegram target and write a local preview record,
but never send, edit, delete, or save a Telegram draft. They remain permitted with
`--readonly`, `TGCLI_READONLY=1`, or `TGCLI_NO_SEND=1`. Those gates apply to
`--commit` only, before configuration, session, audit, or mutation work.
Every authorised send commit appends one JSON object to
`~/.local/state/tgcli/audit.jsonl` (or `TGCLI_STATE_DIR/audit.jsonl`) before
network dispatch, including the stored `random_id`; a successful confirmed
commit appends `send-result` with its preview and message ids. When the
invocation used `--session-role`, the audit object also includes `"role"`
(ADR-0062); primary-session rows omit the field. If the pre-send
audit record cannot be written, the mutation is blocked with exit 2; tgcli
never performs an unaudited authorised write. Preview creation itself does not
send or audit a mutation.

```
tg edit CHAT MESSAGE_ID TEXT --preview
tg edit --commit PREVIEW_ID
tg delete CHAT MESSAGE_ID --preview
tg delete --commit PREVIEW_ID
```

`edit` previews read the target message and return its immutable commit
payload alongside both the previous and requested text:

```json
{"preview_id":"p_9f3a","message_id":42,"old_text":"before","text":"after","expires_at":"2026-07-06T12:05:00+00:00"}
```

`delete` previews return the target message's `preview_id`, `message_id`, and
`text` with the same expiry. Their commits return
`{"preview_id":"p_9f3a","message_id":42}`. Each command accepts either its
complete preview arguments with `--preview` or only `--commit PREVIEW_ID`; a
preview of another kind is blocked before configuration or session work and is
not consumed. Their commits use the same five-minute `.json` → `.pending` →
`.used` lifecycle, retry behavior, and fail-closed audit boundary as `send`.
Edit and delete commits have no `random_id`; their pre-dispatch audit
records are `edit` or `delete`, and successful result records are
`edit-result` or `delete-result`.

```
tg forward SOURCE MESSAGE_ID DESTINATION --preview
tg forward --commit PREVIEW_ID
tg mark-read CHAT
```

`forward` preview resolves both source and destination, reads the source
message, and returns:

```json
{"preview_id":"p_9f3a","source":"@source","message_id":42,"destination":"@destination","text":"hello","expires_at":"2026-07-06T12:05:00+00:00"}
```

The stored forward payload also has a positive `random_id` and the submitted
source and destination chat references. Preview resolves both references only
as a preflight; commit re-resolves the stored chat references into input peers,
then sends that source message to that destination through Telegram's native
forward request. It returns
`{"preview_id":"p_9f3a","message_id":43}` only after exact `random_id`
confirmation. Forward previews and commits use the same validation, readonly
gates, retryable `.json` → `.pending` → `.used` lifecycle, and fail-closed
`forward` / `forward-result` audit records as send. A native forward does not
take reply or topic flags and therefore creates no reply header.

`mark-read` is a content-free, idempotent direct mutation: it has no preview,
but `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block it before
configuration, session, audit, or Telegram work. On success it returns
`{"dialog":{"id":-1001234},"marked_read":true}` and writes a fail-closed
`mark-read` audit record containing the submitted chat reference before the
Telegram acknowledgement.

```
tg mark-unread CHAT
```

`mark-unread` mirrors `mark-read`: same direct gating and audit timing, no
preview. On success it returns
`{"dialog":{"id":-1001234},"marked_unread":true}` and writes a fail-closed
`mark-unread` audit record. Plain rows are `dialog_id`, `unread`.

```
tg dialog pin CHAT
tg dialog unpin CHAT
```

`dialog pin` / `dialog unpin` are content-free, idempotent direct mutations
(ADR-0029): same `--readonly` / `TGCLI_READONLY` / `TGCLI_NO_SEND` gating as
`mark-read`, no preview. On success they return
`{"dialog":{"id":-1001234},"pinned":true|false}` and write a fail-closed
`dialog-pin` or `dialog-unpin` audit record with the submitted chat reference.
Plain rows are `dialog_id`, `pinned|unpinned`.

```
tg dialog archive CHAT
tg dialog unarchive CHAT
tg dialog mute CHAT (--until ISO8601 | --forever)
tg dialog unmute CHAT
```

`dialog archive` / `unarchive` / `mute` / `unmute` follow the same direct
gating and audit timing as pin (ADR-0032): no preview. Archive moves the
dialog into Telegram folder id `1`; unarchive restores folder id `0`. Mute
requires exactly one of `--until <ISO8601>` or `--forever` (omitting both is
exit 2 `BLOCKED`; both together is also exit 2). Forever uses Telegram's
`mute_until = 2**31-1`; `--until` is parsed as ISO 8601 (naive values are
UTC). Unmute sets `mute_until = 0`. Success JSON:
`{"dialog":{"id":…},"archived":true|false}` or
`{"dialog":{"id":…},"muted":true|false,"until":null|<ISO>}` (`until` is
null for forever mute and for unmute). Audit verbs: `dialog-archive`,
`dialog-unarchive`, `dialog-mute`, `dialog-unmute`. Plain rows:
`dialog_id`, `archived|unarchived` or `muted-forever|muted-until:<ISO>|unmuted`.

```
tg thread CHAT MESSAGE_ID [--replies] [--depth N] [--limit N]
```

`thread` is a read-only reply-chain discovery command (ADR-0029). It always
returns `{dialog, root, ancestors, replies, note}` where `root` and each
ancestor/reply use the universal message JSON shape. Ancestors walk
`reply_to` upward, ordered oldest→newest, excluding the root; `--depth`
defaults to 20 and is hard-capped at 100 (cycles stop the walk). `replies`
is empty unless `--replies` is set **and** the root exposes a cheap
comment/forum thread (`message.replies`); otherwise `replies` stays `[]` and
`note` is `"no cheap reply thread for this message; replies omitted"`.
`--limit` caps replies (default 50). Plain rows are the same message TSV as
`read`, one row per root then ancestors then replies.

## 5.0 Read-only batch (`tg batch`; ADR-0032)

```
tg batch [--fail-fast] < ops.jsonl
```

`batch` reads JSONL ops from stdin and writes one JSON result object per
line to stdout under a **single** account session. Hard cap **100** ops
(excess → exit 2 before network); blank lines are ignored and do not count as
ops. ISO date fields use the same parsing as their standalone commands
(`read` `since`/`until`, `search` `since`, `media.manifest` `since`).
Allowlisted `op` values:
`dialogs`, `read`, `search`, `latest`, `message`, `info`, `count`,
`resolve`, `mutual-chats`, `contacts.list`, `contacts.search`,
`media.manifest`, `thread`, `draft.show`, `draft.list`. Mutations, `doctor`, `export`, `clone`,
`media.download`, `api`, and `accounts` are rejected (exit 2).

Each stdout line is `{"ok":true,"op":"…","data":{…}}` or
`{"ok":false,"op":"…","error":{"code":"…","message":"…"}}`. Process exit is
**0 only if every op succeeded**; otherwise the first failure's exit code
(full JSONL still written unless `--fail-fast` stops after the first error).

## 5.05 Local State (`tg store`; ADR-0040)

```
tg store stats
```

Offline inventory of `TGCLI_STATE_DIR` (default `~/.local/state/tgcli/`).
No config and no Telegram session. `--json` emits:

```json
{"previews":{"live":{"count":2,"bytes":120},"expired":{"count":1,"bytes":40},
 "spent":{"count":3,"bytes":90},"pending":{"count":1,"bytes":30}},
 "previews_world_readable":0,
 "logins":{"live":{"count":2,"bytes":80},"expired":{"count":0,"bytes":0}},
 "audit_log":{"bytes":20},"invocations":{"bytes":0},
 "sessions":{"count":1,"bytes":4096},
 "session_backups":{"count":1,"bytes":4096},
 "clones":{"bytes":0,"db":{"count":0,"bytes":0},"wal":{"count":0,"bytes":0},
           "shm":{"count":0,"bytes":0},"imported":{"count":0,"bytes":0}},
 "clone_media_cache":{"count":0,"bytes":0},
 "archive":{"bytes":0,"db":{"count":0,"bytes":0},"wal":{"count":0,"bytes":0},
            "shm":{"count":0,"bytes":0}},
 "downloads":{"bytes":0},
 "relics":[{"name":"labs","bytes":11}]}
```

Preview buckets are classified from each file's stored `expires_at` (not mtime):
`live` = `.json` within TTL, `expired` = `.json` past TTL, `spent` = `.used`,
`pending` = `.pending`. `previews_world_readable` counts preview files with any
other-user permission bit set (legacy `0644` bodies). Login attempts under
`logins/` are classified by `LOGIN_TTL` (30 minutes); `count` is the number of
files in each attempt pair (json and staged session, plus journal when present)
and `bytes` is their total size. `session_backups` reports
`sessions/*.session.bak` and is never deleted by cleanup. `clones` aggregates
everything under `clones/` and additionally breaks out SQLite state files
(`.db` / `.db-wal` / `.db-shm`) plus one-time JSON import backups
(`.json.imported`, ADR-0060); `.imported` files are reported and never
auto-deleted. `clone_media_cache`
reports abandoned `clones/<clone_id>-media/` directories left by a failed
`clone sync` reupload batch (ADR-0052); their bytes are also included in the
aggregate `clones` figure. `archive` reports
`archive/<account>/` under the state root (ADR-0068): aggregate bytes plus
`archive.db` / WAL / SHM counts; never auto-deleted. A custom
`[archive] root` outside the state root is not inventoried here. Relic directories
(`mirrors`, `mirror-lab`, `labs`, `probes`) are reported when present and never
auto-deleted. New previews are written mode `0600`; `store cleanup` also
tightens surviving preview modes to `0600`.

`--plain` columns: `category`, `count` (nullable for size-only rows), `bytes`.

```
tg store cleanup [--older-than Nd|Nh|N] [--include-pending] [--confirm]
```

Reaps **spent** (`.used`) and **expired** (`.json` past TTL) previews under
the state root, **expired** login attempts under `logins/` (json + staged
session), and abandoned `clones/*-media/` directories (mtime-gated; never the
clone's own `.json` state). Default is dry-run: stdout lists what would be
removed and
stderr prints a one-line `--confirm` hint. With `--confirm`, those files are
deleted. Never touches `audit.jsonl`, `sessions/` (including `.bak`), live
login attempts, live `.json` within TTL, clone state JSON, a media cache
younger than one hour (`MEDIA_CACHE_MIN_AGE`, the running-sync guard), the
archive store under `archive/` (ADR-0068), or relic directories.
`.pending` files are
protected (ADR-0028 `random_id`) and are eligible only with `--include-pending`
and only when far past TTL (`expires_at + PREVIEW_TTL`). Staged sessions are
attempt state (under `logins/`), not account sessions — cleanup distinguishes
them by directory.

`--older-than` accepts an integer day count (`7`) or `Nd`/`Nh` (`7d`, `12h`);
age is measured from each preview's stored `expires_at` (mtime fallback), and
for a clone media cache from the newest mtime in the directory — the directory
itself or any file inside it. A media cache is eligible only when that age
also clears the one-hour floor, whether or not `--older-than` was given.

`store cleanup --confirm` mutates local state, so `--readonly` /
`TGCLI_READONLY=1` blocks it with exit 2 before any deletion. Dry-run (no
`--confirm`) is always allowed. `TGCLI_NO_SEND=1` does **not** block it: that
guard is for Telegram sends, and cleanup reaches no network.

`--json` emits:

```json
{"removed":[],"would_remove":["p_spent0.used","p_expired.json"],"bytes":130,
 "confirmed":false,"kept":{"audit_log":true,"sessions":true,
 "session_backups":true,"archive":true,"relics":["labs"]}}
```

With `--confirm`, `removed` is populated and `would_remove` is empty.

## 5.1 Environment Health (`tg doctor`; ADR-0028 / ADR-0040)

```
tg doctor [--account ALIAS] [--connect]
```

`doctor` is a read-only health report: without `--account`, it checks every
configured account; with it, it checks only that account. **By default it is
offline** — config/session file presence, lock freeness, state writability,
preview/audit/session permission tightness, and total state size — and does
not open a Telegram client. `session_perms_ok` covers the account's
`.session` and its `.session.bak`; a missing file is healthy. All permission
checks reject group **and** other bits, not just other. Live authorization (`get_me`) runs only under `--connect`.

When `--connect` is absent, `checks.authorized` is `null` (unknown), not
`false`. Per-account `ok` reflects only local checks offline; with `--connect`,
`ok` also requires `authorized: true`.

The health checks make short-lived local probes: for an existing session they
may create and acquire its `.lock` file, and they create then remove a
`.doctor-probe` file in the preview-state directory. A missing session is not
locked and creates no lock file. These probes do not mutate Telegram.

`--json` emits:

```json
{"runtime":{"python":"/home/me/tgcli/.venv/bin/python","python_version":"3.12.9","telethon":"1.44.0"},
"accounts":[{"alias":"main","session":"/home/me/.local/state/tgcli/sessions/main.session",
"checks":{"session_file":true,"lock_free":true,"state_writable":true,
"preview_perms_ok":true,"audit_perms_ok":true,"session_perms_ok":true,
"state_size":4096,"authorized":null},
"user":null,"roles":[],"ok":true}],"ok":true}
```

The top-level `runtime` object identifies the interpreter and Telethon build
that produced the report. It is diagnostic only and does not change health
status or exit codes.

With `--connect`, `authorized` is a boolean and `user` is populated on success.
Any ordinary online exception, including a session/configuration failure, is
represented as `checks.error`, with `authorized: false`, `user: null`, and
`ok: false` for that account. Each authorized named session role
(ADR-0062; files `sessions/<session>@<role>.session`) appears under
`roles[]` with the same local session/lock/permission probes (and, under
`--connect`, the same live authorization probe) as the primary; per-account
`ok` requires every role report to be healthy too. `--plain` uses frozen
columns: `alias`, `status` (`ok|fail|unknown`), `username`, `failures`.
Role rows use `alias@role` in the `alias` column. `unknown` means local
checks passed and authorization was not probed. When `preview_perms_ok` is
false, `doctor` prints a one-line remedy hint to **stderr** (`tg store
cleanup --confirm`); stdout stays the JSON/rows document only.

When `doctor` itself runs, it always exits 0; consult the top-level `ok` and
per-account `ok` values for health failures. An invalid or unreadable config,
or an explicitly unknown `--account`, prevents the check from running and
retains the normal config/auth exit 3.

## 6. Raw API Passthrough (`tg api`, phase 2+; ADR-0010)

```
tg api <Namespace.method> --params '<json>' [--write] [--confirm <method>]
```

- `--params` is required and must be a JSON object. In phase 2, only the
  reviewed explicit allowlist in ADR-0010 may run through the configured
  session (40 methods as of 2026-07-22; e.g. `users.getFullUser`,
  `messages.getHistory`, `channels.getParticipants`,
  `contacts.resolvePhone`, `stories.getPeerStories`).
- Without `--write`, every method outside the ADR-0010 read allowlist is
  blocked before config loading or session acquisition with exit 2.
- With `--write`, the same `--readonly`, `TGCLI_READONLY=1`, and
  `TGCLI_NO_SEND=1` gates run before config/session/network work. Destructive
  `delete*`, `reset*`, `leave*`, `block*`, `edit*Admin*`, and `edit*Banned*`
  methods require an exact `--confirm <Namespace.method>`, as do the
  irreversible one-way conversions `messages.migrateChat` and
  `channels.convertToGigagroup`, which no prefix rule covers; the permanent
  denylist `account.deleteAccount`, `auth.logOut`, `auth.resetAuthorizations`,
  and `account.resetAuthorization` is always exit 2. Authorised raw writes
  append one JSONL audit object before dispatch, naming the method **and**
  the target identifiers present in `--params` (`peer`, `channel`, `chat`,
  `chat_id`, `id`, `participant`, `user_id`) so the log answers what a write
  touched; message bodies and credentials are never recorded (ADR-0011).
- A parameter for a peer field may be given as a chat reference — `@username`,
  a `t.me` link, or a numeric id in either the raw or `-100`-marked form — and
  is resolved to an input peer before dispatch; an unresolvable reference is
  exit 4. Constructor objects in `--params` must name an `Input*` type, except
  the `channels.getParticipants` filter union, whose members are accepted by
  their own names, and the rights objects `ChatAdminRights` /
  `ChatBannedRights` required by `channels.editAdmin` /
  `channels.editBanned` (no `Input*` form exists).
- `--json` output: `{"method": "users.getFullUser", "result": {…}}` where
  `result` is the TL object as a dict, or a JSON scalar (`true`/`false`,
  number, `null`) when the RPC returns a bare Bool/int/null instead of a
  TLObject (e.g. `account.updateStatus`).
- **Stability exemption:** `result` mirrors the Telegram TL layer of the
  pinned Telethon version and may change when that pin is upgraded; the §3
  stability rules do not apply inside `result`. Everything outside `result`
  follows §3 as usual.

## 7. Export (phase 5)

```
tg export messages <chat> --output <path> [--limit <n>]
    [--after-id <id>] [--append] [--resume]
tg export subscribers <channel> --output <path> [--limit <n>]
```

- `--output` is required. It is the only destination for the export records;
  without `--append`/`--resume`, the command writes a sibling temporary file and
  replaces the destination only after the complete export succeeds. An existing
  destination is unchanged on a failed full export.
- `--after-id N` exports only messages with `id > N` (Telethon `min_id`).
- `--append` appends JSONL lines to an existing file (creating it if missing).
  It requires `--after-id` or `--resume`; otherwise exit 2 (`BLOCKED`).
- `--resume` reads the last non-empty JSONL line's message `id` from
  `--output`, then behaves as `--append --after-id <that>`. Missing, empty, or
  corrupt last line → exit 1. No sidecar state file.
- `messages` iterates through a Telethon takeout session from oldest to newest.
  The destination is UTF-8 JSONL: one `read`-shape message object per line.
  Export resolves the source entity too, so rows preserve the same permalink
  and cross-chat `reply_to` fidelity as live `read`.
- `subscribers` writes UTF-8 CSV with the frozen header
  `id,username,first_name,last_name,phone,is_bot`; standard CSV quoting is
  used for field values. Username and name cells whose first non-whitespace
  character is `=`, `+`, `-`, or `@` are prefixed with a single quote so
  spreadsheet programs do not interpret them as formulas; leading tabs,
  carriage returns, and spaces do not evade the guard. For **broadcast** channels, when `--limit` is
  omitted, tgcli unions saturating prefix searches over
  `channels.getParticipants` to walk past Telegram's hard 200-row cap for a
  single query. A `--limit` greater than 200 on a broadcast channel exits 2
  (`BLOCKED`): it must not run a full-channel crawl and then take an arbitrary
  post-dedupe slice. Megagroups and `--limit` ≤ 200 keep a single
  `iter_participants` pass. Emoji/CJK-only display names with no searchable
  character may leave a member unreachable.
- Success on `--json` is one completion document:
  `{"export":{"kind":"messages|subscribers","format":"jsonl|csv",
  "path":"<path>","count":42,"dialog":{"id":-1001234,"name":"Channel"}}}`.
  When `--after-id`, `--append`, or `--resume` is used on messages, the
  document also includes additive `"after_id"` (int or null) and
  `"appended"` (bool). `count` is this run's written rows only.
  `--plain` emits one TSV row in the frozen order `kind,format,path,count`.
- A `TakeoutInitDelayError` exits 5 as `FLOOD_WAIT`, includes
  `retry_after`, and tells the user to retry after that many seconds.
- Exports have no implicit overall timeout because a 10k-message takeout may
  legitimately exceed the normal 60-second command deadline. An explicit
  `--timeout` still applies.
- An existing integer takeout identifier is reused. A malformed local
  identifier (for example a legacy `b''` value) is cleared before a new
  takeout is initialized, preventing a Telethon serialization traceback.

## 8. Untrusted Content

Message texts, dialog names, and file names are untrusted input. In `--json`
mode they are passed through as data (JSON escaping is sufficient). In human
mode control characters are stripped. tgcli never interpolates message
content into shell commands or file paths without sanitizing.

## 9. Invocation Journal and Diagnostics

Every successfully parsed command appends one JSON object to
`~/.local/state/tgcli/invocations.jsonl` (or `TGCLI_STATE_DIR/invocations.jsonl`):
`timestamp`, `command`, resolved `account` when applicable, `role` when
`--session-role` was set (ADR-0062; omitted for the primary), `exit_code`,
structured `error` code when applicable, and `duration_ms`. The journal never
contains message/search text, chat references, raw API parameters, or command
output. A journal-write failure emits a warning to stderr but does not change
the command result.

`-v` / `--verbose` enables Python and Telethon debug logs on stderr for the
current process. Stdout remains contract data in all output modes.

## 10. Accounts (phase 6 / ADR-0042)

```
tg accounts list
tg accounts import [ALIAS ...] [--source-root PATH] [--force]
tg accounts show ALIAS
tg accounts remove ALIAS [--confirm] [--keep-session]
tg accounts remove ALIAS --role NAME [--confirm]
tg accounts login ALIAS [--phone PHONE] [--api-id N] [--api-hash H]
                        [--force] [--timeout SECONDS] [--qr-format link|text]
                        [--password-stdin] [--role NAME]
tg accounts login --continue LOGIN_ID [--code VALUE|-] [--password-stdin]
```

`accounts list` is a local-only command: it reads config only and never opens
a Telegram session. `--json` emits the configured default and each alias with
its session basename (never `api_id` / `api_hash`):

```json
{"default_account": "main",
 "accounts": [{"alias": "main", "session": "main"}]}
```

`--plain` emits frozen TSV columns: `alias`, `session`.

`accounts import` is a local-only command: it never opens a Telegram
connection. With no aliases it tries `main`, `recklessou`, and `teamsyncsage`,
and reports a missing old-stack source as a warning rather than failing. An
explicitly named missing source exits 4. The command copies old-stack SQLite
sessions with an online backup into `TGCLI_STATE_DIR/sessions`; an existing
destination is left untouched unless `--force` is supplied. A busy destination
lock or missing or unparseable credentials for a newly configured account
exits 3.

`--json` emits:

```json
{"imported": [{"alias": "pl", "session": "/home/me/.local/state/tgcli/sessions/pl.session",
               "status": "imported|skipped_existing|source_missing",
               "config": "added|unchanged"}]}
```

`--plain` emits frozen TSV columns: `alias`, `status`, `config`.

`accounts show` is strictly offline (ADR-0042 §11): config presence, resolved
session path, existence, size, mtime (ISO-8601 UTC), whether the account lock
is currently held, and the `.bak` slot. `authorized` is always `null`. An
alias absent from config exits 4 (lookup of a named registry entry —
`NotFoundError`; distinct from `--account` config resolution, which is exit 3).
Lock state is probed only when the session file exists — a missing session
creates no lock file (same rule as `doctor`, §5.1). The probe uses
`LOCK_EX | LOCK_NB` and is released immediately — never stolen, never waited on.

```json
{"alias": "main", "in_config": true, "session": "/…/sessions/main.session",
 "exists": true, "bytes": 32768, "modified": "2026-07-24T12:00:00+00:00",
 "locked": false, "backup": "/…/sessions/main.session.bak", "authorized": null,
 "roles": [{"name": "job", "session": "/…/sessions/main@job.session",
            "exists": true, "locked": false, "authorized": null}]}
```

`roles` lists every named session role file beside the primary (ADR-0062);
an account with none emits `"roles": []`. Each entry is offline: name,
resolved path, existence, lock probe, and `authorized: null`.

`--plain` emits: `alias`, `exists`, `bytes`, `modified`, `locked`, `backup`,
`authorized`.

`accounts remove` deletes the named `[accounts.<alias>]` block and, unless
`--keep-session`, the session file and its `.bak`. Without `--confirm` it
deletes nothing, prints a `--confirm` hint to stderr, and exits 2. It refuses
(exit 2) when the account lock is held, when the alias is `default_account`,
or under `--readonly` / `TGCLI_READONLY=1` with `--confirm`. `TGCLI_NO_SEND`
does not apply. An unknown alias exits 4. The audit record `accounts-remove`
is written before any deletion (fails closed).

With `--role NAME`, `accounts remove` deletes **only** that role's
`.session` / `.bak` (never config, never the primary). `--keep-session` is
rejected (exit 2). A missing role file exits 4. The audit action is
`accounts-remove-role` and includes `"role"`. Success JSON adds `"role"` and
keeps `"config": "unchanged"`.

```json
{"alias": "x", "config": "removed", "session": "deleted", "backup": "deleted"}
```

`session` / `backup` are `deleted`, `kept` (`--keep-session`), or `absent`.
`--plain` emits: `alias`, `config`, `session`, `backup`, `role` (empty when
removing the whole account).

`accounts login` authorizes a session (ADR-0042). No `--phone` ⇒ QR path;
`--phone` ⇒ phone + confirmation code. `--api-id` / `--api-hash` are required
together and only for an alias absent from config. `--continue` takes no
`ALIAS` and rejects `--phone` / `--api-id` / `--api-hash` / `--force` /
`--role`. `--role NAME` authorizes a named session role beside an **already
configured** alias (ADR-0062); it never appends config and refuses an
unknown alias (exit 3). `--timeout` defaults to **120** seconds on the QR
path when unset. The cloud password is never accepted as an argv value; use
a native dialog or `--password-stdin`. `--code` is accepted only with
`--continue`; headless environments without a dialog must pass
`--code VALUE` or `--code -` rather than blocking on stdin. `--readonly` /
`TGCLI_READONLY=1` block login; `TGCLI_NO_SEND` does not. A still-authorized
existing session (or role) refuses without `--force` (exit 2) — including an
orphan session file for an alias not yet in config. Promotion by atomic
rename is the only writer of `sessions/<alias>.session` (or
`sessions/<alias>@<role>.session`); attempt state lives under `logins/` and
records the role when set.

Terminal success:

```json
{"alias": "main", "method": "qr", "status": "authorized", "next": null,
 "user": {"id": 123, "username": "x", "phone": "+7…89"},
 "session": "/…/sessions/main.session",
 "backup": "/…/sessions/main.session.bak"}
```

A role login adds `"role": "job"` and points `session` at the role file.

Step completed but more needed (exit 0):

```json
{"alias": "main", "method": "phone", "status": "pending", "next": "code",
 "login_id": "l_…", "expires_at": "2026-07-24T12:00:00+00:00"}
```

`--plain` emits frozen TSV columns: `alias`, `method`, `status`, `next`,
`login_id`, `phone` (masked), `session`, `role`. Pending steps may leave
`next`, `login_id`, `session`, or `role` empty; authorized success fills
`session` and may clear `next`.

Exit codes (existing set): 0 step ok including `"next": "code"|"password"`;
1 QR wait timed out (attempt kept; error names `login_id`); 2 readonly /
authorized-without-`--force` / `--continue` flag conflicts; 3 invalid code /
invalid cloud password / banned or invalid number / missing api credentials;
4 unknown alias or unknown/expired `login_id`; 5 `FLOOD_WAIT` with
`retry_after`. Phones in JSON, `--plain`, stderr, and audit are masked
(`+7…89`); codes and passwords never appear there.

## 11. Chat Clone (ADR-0017, ADR-0021, ADR-0022, ADR-0023, ADR-0051, ADR-0054)

`tg clone` is the canonical chat-copy surface. It accepts broadcast channels,
megagroup supergroups (forum and non-forum), live legacy basic groups, and
private one-to-one User dialogs including dialogs with bots. Basic groups that
migrated to a supergroup or were deactivated, and other peer shapes, exit 2 with
a source-specific policy message. The destination type follows the source kind:
a forum source clones into a private owned forum megagroup with a 1:1 topic map;
every other source clones into a private owned broadcast channel. Destinations
are tool-created and tool-controlled; cloning into pre-existing or shared groups
is not supported.

A broadcast source with a readable linked discussion group (`ChannelFull.
linked_chat_id`; a monoforum's `linked_monoforum_id` is never read — it is
not a comment section) additionally gets a second private owned megagroup,
created and linked before the first post is synced, cloning the source's
comment threads. State and every `status`/`init`/`sync` response carry a
`comments` field: `"enabled"` (linked group readable, threads clone),
`"unavailable"` (linked group exists but is unreadable — the channel still
clones posts-only, and the marker is permanent; there is no backfill, only a
fresh clone against a new destination, reached with `init --replace` — see
below), `"disabled"` (owner opted out of the discussion leg with
`init --no-comments`; posts-only forever for this state slot — ADR-0045), or
`"none"` (no linked group, or a non-broadcast source). Existing
clones from before this feature have `comments: "none"` and are never
retroactively upgraded in place.

```text
tg clone status [SOURCE]
tg clone init SOURCE
tg clone init SOURCE --replace
tg clone init SOURCE --no-comments
tg clone init SOURCE --commit PREVIEW_ID
tg clone sync SOURCE [--limit N]
tg clone refresh SOURCE
tg clone refresh SOURCE --commit PREVIEW_ID
tg clone export-state SOURCE
```

`status` is local and read-only: it never loads config or opens a Telegram
session. Without `SOURCE` it lists every clone state database (and any
legacy `.json` awaiting one-time import); with `SOURCE` it
filters by exact numeric source id — either the raw peer id or its
`-100`-marked form — or by case-insensitive title substring. JSON:

```json
{"clones":[{"clone_id":"hex","source":{"id":123,"title":"Source","kind":"broadcast"},"destination_id":999,"cursor":42,"copied":40,"cooldown_until":null,"created_at":"2026-07-15T12:00:00+00:00","last_synced_at":null,"comments":"enabled","schema_version":1,"integrity":"ok"}]}
```

Each readable entry carries `schema_version` (SQLite `PRAGMA user_version`)
and `integrity` (`"ok"` or the integrity-check error string). Plain status
columns are `source_peer_id`, `source_title`, `source_kind`,
`destination_peer_id`, `cursor`, `copied`, `last_synced_at`, `comments`.

A corrupt or legacy (unsupported-version) state file never aborts the listing:
without `SOURCE` it appears as a marked entry `{"clone_id":"hex","unreadable":
true,...,"schema_version":null,"integrity":"<error>"}` with every other field
null, and in plain output its `source_title`
column carries the `clone_id` and its `comments` column reads `unreadable`.
Because an unreadable file's identity cannot be matched, it is omitted from
`SOURCE`-filtered listings. Readable entries never carry the `unreadable` key.

`export-state SOURCE` is local and read-only (ADR-0060). It prints exactly one
clone's state as the v2 JSON document (`CloneState.to_dict()` shape) on
stdout — the permanent rollback/diagnostic path (downgrade = export + previous
binary). `SOURCE` uses the same id/title filter as `status` and must match
exactly one readable clone; unknown or ambiguous → exit 2. Output is always
JSON (independent of `--json` / `--plain`).

`init SOURCE` is a read-only network preview. It resolves the source, verifies
that its kind is accepted, reads the approximate message count and
protected-content flag, and stores `source_kind` (and the `--replace` intent)
in a five-minute single-use preview through the shared safety mechanism. It does
not create clone state or mutate Telegram. The response carries a `supersede`
object describing the existing state slot, computed read-only (a legacy or
corrupt file never makes preview fail): `existing` is whether a state file
exists, `readable` is whether it loads under the current version (`null` when
absent), and `replace` echoes the flag. JSON:

```json
{"preview_id":"p_...","expires_at":"...","clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"dialog"},"destination":null,"status":"planned","commit_required":true},"approximate_message_count":321,"protected":false,"supersede":{"existing":false,"readable":null,"replace":false},"peers_to_create":1,"account_flood":{"cooldown_until":null,"last_peer_created_at":null}}
```

`peers_to_create` is how many `CreateChannelRequest` calls this commit would
make: `0` when a destination peer id is already recorded and `--replace` is
not set, `1` for a posts-only init (`--no-comments`, non-broadcast, or a
broadcast with no linked discussion), or `2` when a broadcast source has a
linked discussion and no destination is recorded yet (or `--replace` will
supersede the slot). `account_flood` mirrors the
account-scoped flood record (ADR-0045): `cooldown_until` / 
`last_peer_created_at` as ISO timestamps or null. Both fields are data for
the caller — they never block preview. `--no-comments` is stored in the
preview payload and honored at commit; it creates a posts-only clone with
`comments: "disabled"`, creates one peer, and links nothing. Re-running
init with `--no-comments` against a slot whose state already has
`comments: "enabled"` is exit 2 (`PolicyError`) — the flag cannot orphan an
existing linked group (use `--replace` for a fresh posts-only clone).

`init SOURCE --commit PREVIEW_ID` requires a matching unexpired clone-init
preview. `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block before
preview consumption, config, session, audit, or Telegram work. The commit uses
a mutation-safe session, verifies that the resolved account id, source id, and
source kind still match the preview, then creates or recovers one private
creator-owned destination of the source-dependent kind. JSON:

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"dialog"},"destination":{"id":999,"title":"[Clone] Source"},"comments":"none","status":"ready","commit_required":false},"ergonomics":{"muted":true,"folder":"added"}}
```

After the destination (and discussion group, when enabled) is ready, init
best-effort mutes each tool-created peer forever and files them into the
Telegram folder titled `Clone` (ADR-0046). `ergonomics.muted` is `true` when
every peer is muted or was already muted; `false` if any mute RPC failed
(stderr warning). `ergonomics.folder` is `"added"` when peers were written
into the filter, `"present"` when they were already members, or
`"unavailable"` on folder limits / RPC failure (stderr warning). Mute and
folder failures never fail init (exit stays 0). Retrofit existing clones with
a plain `clone init` re-run — no new peers.

Plain init columns are unchanged by comments support: `status`, `clone_id`,
`source_peer_id`, `destination_peer_id`. `comments` and `ergonomics` are
JSON-only on `init`.

For a broadcast source, commit also creates or recovers a second peer: a
private owned megagroup titled `<creation_marker>-discussion`, using the
same crash-recovery marker-scan discipline as the destination channel (a
crash between group-create and `SetDiscussionGroupRequest` recovers by
re-adopting the marked group and relinking — idempotent). Its
about/avatar are copied from the source discussion group and its title is
set to `[Clone] ` + the source group's display name (ADR-0044), then
`channels.SetDiscussionGroupRequest` links it to the destination channel
strictly before `sync` sends the first post. A linked source group that
cannot be read (private, not a member) skips group creation entirely and
sets `comments: "unavailable"`; a source with no linked group at all sets
`comments: "none"`. Non-broadcast sources never attempt this and always
report `comments: "none"`. Init's live ~15h FLOOD_WAIT window on rapid peer
creation applies to up to two peers per run instead of one.

Before creation, state with `destination_peer_id:null` and title marker
`tgcli-clone-<clone-id-prefix>` is atomically saved under
`TGCLI_STATE_DIR/clones/<clone_id>.db` (SQLite/WAL; ADR-0060). A legacy
`<clone_id>.json` is imported once on first `load()` into `.db` and renamed
to `<clone_id>.json.imported` (kept until manual cleanup; never auto-deleted).
Both `.db` and `.json` present for the same id is exit 2 (ambiguous). Recovery
adopts exactly one matching
private creator-owned destination of the required kind, creates when none
exists, and exits 2 without mutation on multiple or wrong-shape matches. Once a
destination id is recorded, repeated init resolves and reuses it without
scanning or creating. Destinations are user-owned and never deleted
automatically.

Because `clone_id` is deterministic per source, one source maps to one state
slot forever, and `commit` fail-closes (exit 2) on any slot whose version it
cannot load — so a stale or legacy clone cannot be re-created by a bare `init`.
`init SOURCE --replace` supersedes it: at commit, before loading state, it
archives the existing `<clone_id>.db` (plus WAL/SHM sidecars when present)
and any leftover `<clone_id>.json`, and, if present, the ADR-0024 roster
sidecar `<clone_id>-participants.jsonl` by renaming each to
`*.superseded-<UTC>` (archive, never delete — the old destination in Telegram
is untouched), appends a `clone-init-replace` audit record, then starts a fresh
clone against a **new** destination pair. To guarantee the new pair, a replaced
clone's creation marker gets a random suffix (`tgcli-clone-<prefix>-<hex>`) so
it never re-adopts a marker-titled destination left by an interrupted prior
init. `--replace` against an empty slot is a plain fresh init. `--replace` is
declared on the preview step and carried in the preview payload; the commit
honors the payload. Without `--replace`, the version-mismatch exit-2 message
ends `; re-run clone init --replace to supersede it`; a v2 destination is never
silently reused as if the flag had been passed.

After creation or recovery, init titles tool-created peers with a visible
`[Clone] ` prefix (ADR-0044): the destination becomes
`[Clone] {source_title}` and a discussion group becomes
`[Clone] {display_name(source_group)}`. `source.title` in JSON and
`CloneState.source_title` stay unprefixed. The rename is idempotent — a
re-run edits only on mismatch — so an existing clone adopts the prefix via
a plain `clone init` without creating peers. Init then copies a non-empty
channel or basic-group description, or User bio, and copies a non-empty
static source avatar before returning `status: ready`. Empty source fields
cause no mutation. The avatar copy is idempotent: the copied source photo id
is recorded in clone state and a re-run skips the copy (no upload, no
`EditPhoto`, no audit record) until the source avatar changes.
Avatar bytes use a temporary directory that is removed on success or failure.
Animated or video avatar motion is not preserved (ADR-0020).

Every create, title-edit, description-edit, and avatar-edit attempt appends a
fail-closed shared audit record before dispatch. A profile-copy failure exits 2
while retaining the recorded destination for a new-preview retry; it never
creates a second destination. Telegram FloodWait during profile reads, downloads,
uploads, or edits persists `retry_not_before` in clone state **and** arms an
account-scoped cooldown record under
`TGCLI_STATE_DIR/clones/account-<account_user_id>.json` (ADR-0045). Later
`clone init --commit`, `clone sync`, and `clone refresh` for **any** clone of
that account exit 5
locally (no network) while either the per-clone or the account deadline is
active — `retry_after` is computed from `max(per-clone, account)`. Read-only
surfaces (`clone status`, init preview, refresh preview) are never blocked by
readonly gates; refresh preview still respects an active FloodWait cooldown
because the scan is real network work. A roster FloodWait
(ADR-0024) still arms neither cooldown. Init keeps the global 60-second
default timeout.

`sync SOURCE` requires initialized state and a private creator-owned destination
of the source-dependent kind. It verifies the destination tail before reading
new source history. Rows after the largest persisted message or topic mapping
(or the fresh-destination id-1 baseline) are accepted only when every visible
tail row is a Telegram service action, such as channel creation or the init
title change. Any ordinary tail message exits 2 with its count in `unexpected`
and requires manual repair; no source scan, audit, or copy occurs (ADR-0018).

Sync iterates source history with `reverse=True` and
`min_id=cursor`, so confirmed destination messages follow source order. The
allowlist is unprotected non-reply text/no-media, `MessageMediaWebPage`,
`MessageMediaPhoto`, and `MessageMediaDocument`. TTL/view-once photo or document
media is reported in `skipped_unsupported` instead of being forwarded or
downloaded.

Contiguous messages whose `grouped_id is not None` are one album batch, including
the valid edge case `grouped_id=0`. The complete ordered album is sent by one
request with one distinct random id per item. Every requested random id must
have exactly one unique positive `UpdateMessageID` before all mappings and the
batch cursor are atomically saved. Missing, duplicate, extra, or invalid
confirmation exits 2 without partially advancing state; the next run's tail
verification detects a batch Telegram accepted but tgcli could not confirm.

Service messages advance the cursor and increment `skipped_service` without
audit or Telegram mutation. Polls become human-readable static result cards with
a Russian heading, question, options, Unicode progress bars, counts, rounded
percentages, and total voters; no timestamps or implementation labels are
shown. When `total_voters > 0` but Telegram has not revealed the per-option
breakdown, the card shows `распределение по вариантам недоступно` instead of
misleading `0% · 0` rows (ADR-0048). For anonymous, open, non-quiz polls in that
state, sync may cast a transient vote, read the revealed results, retract the
vote, and subtract the own vote from the rendered totals; public polls, quizzes,
and closed polls never vote. `--readonly` / `TGCLI_NO_SEND` never reach a poll
at all: they block `clone sync` as a whole before config/session work (see
below), so no transient vote can be cast under them. The snapshot renderer
re-checks the same two gates and keeps the honest placeholder, which matters
only if it is ever driven outside `clone sync`. Cast and retract each append an
audit record; a
retract failure warns on stderr and adds a `poll_votes` marker — sync does not
abort. Story references become two-line `Stories недоступна` placeholders whose
resolved author name/title is a clickable `t.me` link when possible; Story IDs
are not shown. Both use the audited `clone-sync-snapshot` path, receive
source-to-destination mappings, and count as copied. Truly unsupported kinds
such as dice advance the cursor and appear in `skipped_unsupported`; nothing is
skipped silently (ADR-0019).

For forum clones, a topic-create service message creates the matching
destination topic (counted in `topics_created`, not `skipped_service`); messages
arriving for an unmapped topic recover it from the source topic's current title.

When `comments == "enabled"`, sync interleaves the channel-posts leg and the
discussion-group leg in fixed windows of 50 batches (ADR-0051, amending
ADR-0023's sequential ordering clause only): posts×WINDOW, then comments up
to the first source-group anchor whose channel post id is newer than the
posts cursor, then the next posts window, until both legs are exhausted.
An unmapped cross-leg comment parent whose post id lies beyond the posts
cursor defers (stops the comments leg without sending) rather than
flattening; a parent behind the cursor and absent from the map still
flattens as before. Each leg keeps its own cursor (`cursor` /
`discussion_cursor` in state and in the JSON `sync` object; the top-level
`sync.clone` object itself carries no `comments` field). `--limit N` counts
batches across both legs — a run may return comments where a pre-ADR-0051
`--limit` returned only posts; `sync.more` stays true when the budget
stopped either leg. A run that stops inside a comments window leaves later
comments lagging until the next invocation.
If resolving the source discussion group fails because Telegram refuses
access (`ChannelPrivateError`, `ChatForbiddenError`, `ChatAdminRequiredError`,
or an unresolved peer), sync does **not** exit non-zero: it persists
`comments: "unavailable"`, clears any leftover `discussion_cursor` /
`discussion_id_map` (state forbids phase-2 progress when comments are not
`enabled`), skips the comments phase and the discussion roster snapshot for
this and later runs, and exits 0 — the same permanent honest marker init
would have written for an unreadable linked group (ADR-0023). A
`FloodWaitError` while resolving that group still exits 5 and arms the
account-scoped cooldown (ADR-0045). An unavailable *destination*
discussion group remains exit 2 (`PolicyError`). When `comments` is
`"disabled"`, `"unavailable"`, or `"none"`, the comment phase and the
discussion roster snapshot are skipped entirely.
Telegram's own auto-forwards of channel posts into the discussion group
(recognized by `fwd_from.saved_from_peer`/`saved_from_msg_id` matching the
source channel and post) are read-only anchors, never copied, and counted in
`skipped_autoforward`; a channel album auto-forwards as an album, so a
mixed batch (some anchors, some not) exits 2 before any copy. A comment's
thread parent is remapped from the source anchor through the source post
(`id_map`) to the destination anchor
(`messages.GetDiscussionMessageRequest`, cached per run) so it lands as a
reply in the right destination thread, including its quote; comment-on-
comment parents remap through the discussion group's own `id_map`
(`discussion_id_map`). An unmappable parent flattens on the same
`reply_flattened` rule as any other clone. Off-thread discussion-group
messages clone as ordinary attributed megagroup content. The discussion
group has its own tail verification, tolerating Telegram's own anchors in
the tail the same way the channel tail tolerates topic-create service rows;
an unexpected discussion-group tail message exits 2 the same way.

For broadcast sources, an unprotected non-reply batch of the channel's own
content uses native forwarding with `drop_author=True`; the destination does
not expose a source-forward header. A broadcast post that is itself a forward
(carries `fwd_from` — a re-forward from another user, channel, or story) instead
forwards with `drop_author=False`, so Telegram restores its original forward
header pointing at the true origin, never at the cloned source channel (an album
decides as one batch since every item shares the header). Only the native
forwarded path preserves this header: a re-forward that also has a mapped reply,
or any post from a protected source, travels by reupload or snapshot and loses
`fwd_from`. On those paths the clone prepends a Russian `Переслано от <label>`
line built only from what `fwd_from` asserts (`from_id` / `from_name` /
`post_author`, or the bare word `Переслано` when nothing resolves) — it never
claims a discussion-group origin it cannot prove (ADR-0050). Resolving
`from_id` uses a successful entity lookup when possible; when a later
GetChannels-shaped lookup refuses a private or left peer, the clone still
uses a Channel/User Telegram already shipped with that message
(accompanying `chats` / Telethon `message.forward.get_chat()` or
`get_sender()`), matching the title clients show in forward chrome
(ADR-0064). When such a
reposted, single-message reupload batch can prove its original in the
clone's linked source discussion group — the group is reachable and not
`noforwards`, exactly one of its messages matches `fwd_from.from_id` and
`fwd_from.date`, and that message's text, formatting entities, and media —
including whether that media is hidden behind a spoiler — are
identical to the post — the clone forwards the original out of the source
group into the
destination channel instead of prefixing text, so the destination carries
Telegram's own header (ADR-0050 Decision 3). That batch is then counted as
`forwarded`, not `reuploaded`, in the sync JSON transport counts. The
upgrade is confined to that shape: an album, a snapshot-mode repost (a
reposted poll or story), and a repost that also carries a mapped reply are
never re-forwarded. Any unproven case — no linked group, a protected group,
no single matching candidate, or mismatched content — keeps the text-prefix
fallback above. For
attributed megagroup, forum, basic-group, and dialog sources, the
same batch uses native forwarding with `drop_author=False`, retaining
Telegram's author header.

### Prefix backfill (`tg clone refresh`; ADR-0054)

ADR-0054 adds a separate mutation path onto the existing ADR-0050 prefix rule;
it amends nothing already in this section. A clone copied before a prefix rule
shipped can be repaired without recopying:

```text
tg clone refresh SOURCE
tg clone refresh SOURCE --commit PREVIEW_ID
```

A bare `tg clone refresh SOURCE` *is* the preview (same grammar as `clone
init`, no separate `--preview` flag). It walks the posts-leg `id_map`, applies
the eligibility rule, and mints a five-minute single-use preview. A post is
eligible only when the destination body is **byte identical to the source body
with no prefix at all** and today's renderer would produce a prefix. That
single test proves the copy predates the improvement and that nobody edited
the destination by hand. Three exclusions are reported in the preview's
`excluded` list and never edited: poll/story snapshots (`poll-snapshot`),
native re-forwards that already carry a destination `fwd_from`
(`native-reforward`), and the discussion leg (never scanned — only
`id_map` is walked). Album followers (non-lead items of a `grouped_id`) are
also excluded (`album-non-lead`): sync only ever attached the author prefix to
the lead item. When an album's lead cannot be *proven* — the nearest lower
mapped source id was not returned by Telegram, so the deleted-lead case is
indistinguishable from a healthy follower — the candidate is excluded as
`album-lead-unknown` rather than promoted: a skipped message is harmless, a
prefix written onto the wrong live message is not. Bodies that fail eligibility for any other reason appear as
`not-eligible` and are skipped without alarm — the steady state of an already-
fixed or hand-edited clone.

Preview JSON:

```json
{"preview_id":"p_…","expires_at":"…","clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"broadcast"}},"refresh":{"eligible":[{"source_id":54,"destination_id":154}],"excluded":[{"source_id":60,"reason":"poll-snapshot"}]}}
```

The persisted preview stores `kind: "clone-refresh"`, `source`,
`account_user_id`, `source_peer_id`, and the `eligible` id pairs only — never
the rendered text (recomputed fresh at commit). `--readonly` /
`TGCLI_READONLY` / `TGCLI_NO_SEND` gate the commit only, not the preview scan
(identical rule to `clone init`).

`--commit PREVIEW_ID` consumes the preview (single-shot via
`safety.consume_preview`, not `begin_commit`), then fail-closes (exit 2) if
the live account/`source` peer no longer match the preview's
`account_user_id` / `source_peer_id`, or if any eligible
`{source_id,destination_id}` pair no longer matches the current posts-leg
`id_map` (e.g. after `clone init --replace`). It re-checks each surviving
candidate against a fresh destination read, and issues
`messages.EditMessageRequest` with text
and entities only — `media` is never set, so existing media stays untouched;
`id_map` and both cursors are unchanged. Each surviving edit writes a
`clone-refresh-prefix` audit record before its RPC. A candidate that no longer
matches eligibility is skipped, not forced. `MessageNotModifiedError` on an
individual edit is swallowed and the run continues. Commit JSON:

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"broadcast"},"destination":{"id":999,"title":"Source"}},"refresh":{"edited":[{"source_id":54,"destination_id":154}],"skipped":[{"source_id":60,"reason":"not-eligible"}],"count":1}}
```

`--plain` columns: `source_id`, `destination_id`, `status` (`edited` or an
exclusion reason). Exit 0 when every candidate was either edited or correctly
declined.

FloodWait during preview or commit exits 5 through the same per-clone and
account-scoped cooldown as `sync` and `init --commit`; there is no retry loop
inside `refresh` (ADR-0045). Recovery is a **fresh** preview after the
cooldown, not a retried `--commit` of the same already-consumed preview id
(contrast `send`/`edit`'s `begin_commit` retry idiom). Posts already fixed no
longer match eligibility, so a second run is a quiet no-op.

A batch uses download/reupload reconstruction when the source or any message
has `noforwards`, or when it has a mapped reply. Attributed reuploads prepend
`<display name>: ` followed by a blank line to text or the leading album
caption, so the author header sits on its own line above the message body.
Original entity
offsets shift by the prefix's UTF-16 code-unit length, and repeated sender
lookups are cached for the sync run. This preserves both attribution and the
mapped destination reply relationship that native forwarding drops.

Ordinary replies preserve the mapped direct parent and, when available, the
mapped nested top root. If the direct parent is unavailable, content copies in
order without a reply relation and increments `reply_flattened`; an unprotected
attributed message uses a native author-preserving forward for this fallback.
If only the nested top root is unavailable, the mapped direct parent remains
linked. `MessageReplyStoryHeader` also flattens and reports because it has no
message id to map. In forum clones, placement-only topic headers are not replies;
real in-topic replies map both their parent and topic. Non-forum clones, and
headers that carry scheduled/ephemeral/todo/poll-option targets, flatten the
reply relation and continue (ADR-0036). Cross-chat quote replies are resolved by
target reachability (ADR-0036): a mapped target (same leg or the other leg's
post→anchor path) keeps a native `InputReplyToMessage` with quote text/entities/
offset; a foreign peer the account can read is quoted in place; an unreachable
or send-rejected foreign peer is rendered as a text fallback (peer title line,
blockquote quote, author's unmodified body) and recorded in `quote_flattened`
as `{"id", "peer", "reason"}`. The fallback's peer line is the title Telegram
ships alongside the quoting message (`ChannelForbidden` carries one even for a
peer the account is banned from), falling back to `id <n>` when the response
names no such chat. A quote whose stored fragment no longer matches its parent
— the parent was edited after the quote was made — is refused by Telegram with
`QUOTE_TEXT_INVALID`; the send is retried once with the fragment dropped and
the reply link kept, reported as `reason: "quote-rejected"`. `reply_from` /
`reply_media` are server-rendered decorations and are ignored. Malformed quote
metadata, an invalid reply parent, and inconsistent album reply shapes still
exit 2 before audit or mutation.
When a run plants at least one quote fallback it finishes copying, writes the
full result document (including advanced cursors), and exits 2 (`PartialFailure`
with `PolicyError` cause); a run that plants none exits 0.

Reupload sends text and webpage messages with `sendMessage`, photos/documents
with `sendMedia`, and albums with per-item `uploadMedia` followed by one
ordered `sendMultiMedia`. Captions and entities are retained; documents retain
MIME type, Telegram attributes, and — when the source carries a downloadable
still-image thumb — that preview, so a PDF or sticker keeps its native card
instead of degrading to a bare file row. A thumb that cannot be fetched is
omitted and the reupload continues; a preview is fidelity, not content. Reupload downloads persist under
`~/.local/state/tgcli/clones/<clone_id>-media/` (name `src-<message_id>`). A
file is reused when its on-disk byte size matches what the source reports;
anything else is re-downloaded. The directory is removed after a successful
send and left on disk after a failed one so a retry does not re-download. A
download failure leaves the batch cursor and mapping unchanged and occurs
before the fail-closed `clone-sync-reupload` audit/write boundary. Striped
downloads of a `Photo` (files over 512 KB) select the largest `PhotoSize` by
byte count explicitly rather than trusting Telegram's `sizes` list order
(ADR-0055). A `FloodWaitError` of at most 60
seconds (`SHORT_WAIT`) is waited out once in the foreground when the
per-process wait budget still has room (at most 180 seconds of pausing per
invocation; `WAIT_BUDGET`), after a non-contractual stderr progress line
naming the seconds, then the same request is retried; a second failure, a wait
over 60 seconds, or a spent budget persists the clone cooldown and raises.
Both `UpdateMessageID` batches and the single-message
`UpdateShortSentMessage` envelope require exact positive confirmation before
state advances.

`--limit N` must be positive and copies at most N message batches. If another
source row remains, JSON reports `"more":true`; the next run resumes at the
saved cursor. Sync has no implicit overall timeout, uses a mutation-safe
session, and is blocked by all readonly gates before config/session work.
A short FloodWait (≤ 60 s) is waited out once under the 180-second per-process
budget as above; a second failure, a longer wait, or a spent budget persists
the clone cooldown and exits 5 without advancing the current message. JSON:

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"broadcast"},"destination":{"id":999,"title":"[Clone] Source"}},"sync":{"copied":2,"skipped_unsupported":[{"id":4,"kind":"MessageMediaDice"}],"forwarded":1,"reuploaded":1,"snapshots":0,"topics_created":0,"skipped_service":1,"skipped_autoforward":0,"reply_flattened":0,"quote_flattened":[],"poll_votes":[],"cursor":5,"discussion_cursor":0,"more":false,"pinned":{"source_id":12,"destination_id":9,"status":"set"},"participants":{"path":"~/.local/state/tgcli/clones/hex-participants.jsonl","source":{"peer_id":123,"status":"unavailable","count":0,"reason":"ChatAdminRequiredError"},"discussion":{"peer_id":55,"status":"collected","count":42,"reason":null}}}}
```

On a broadcast destination, `sync.pinned` reports the pin carry-over
(ADR-0055). Forum destinations omit the key entirely. Status values:

- `set` — this run placed `messages.UpdatePinnedMessage` with `silent=true`
  on the mapped destination id (audited as `clone-sync-pin`);
- `unchanged` — the clone already pinned once; later completing runs answer
  from state with zero pin RPCs and never re-pin or unpin. This status is
  also how a crash between the pin RPC and the state save recovers: when the
  destination's current pin is exactly the message this run intended to pin,
  the clone adopts it (saving `pinned_dest_id`, no pin RPC, no audit row) at
  the cost of the same two `GetFull*` reads the occupancy check makes;
- `unmapped` — the source has no pin, or its `pinned_msg_id` is absent from
  `id_map` (service message, skipped-unsupported, deleted); nothing is
  persisted, retried on the next completing run;
- `occupied` — the destination already had a pin when first checked; the
  clone leaves it alone and records that permanently.

Unpinning is never mirrored. RPC cost: one `channels.GetFullChannel` (or the
matching `GetFullUser` / `GetFullChat` for non-channel sources) on every
completing run until resolved; a second `GetFullChannel` on the destination
only on the run the source pin first becomes mappable; zero after resolution;
plus one `messages.UpdatePinnedMessage` when a pin is actually placed. A run
that stops early (`more:true`) never calls the pin phase and reports only
what state already knows (`unmapped` / previously-resolved `set` /
`occupied`).

`sync.poll_votes` is an additive list of per-poll markers from the ADR-0048
capture path (`status` of `captured`, `skipped`, `capture_failed`, or `retract_failed`, plus
`reason` / `error` when applicable). Empty when no poll needed capture. A
FloodWait that blocks the retract also yields `retract_failed`: the transient
vote is disclosed on stderr and in the marker rather than vanishing into a
generic exit 5, because a standing vote must never be silent.

After message copying, `sync` snapshots the source's audience (ADR-0024). The
`participants` object reports, per source-side peer (`source` = the cloned
channel/chat, `discussion` = its linked comment group when
`comments == "enabled"`), a `status` of `"collected"`, `"unavailable"`
(Telegram refused — a broadcast channel you do not administer always refuses),
`"deferred"` (FloodWait; retried next run, partial results discarded, and the
main clone cooldown is left unset so it never blocks message sync), or `"none"`
(no discussion group). Collected participants are rewritten atomically to the
JSONL sidecar at `participants.path` (one object per line, tagged `peer` plus
the `export subscribers` columns). This snapshot is source-side only; the
destination is never populated with collected users. Roster collection is
best-effort and never fails a sync whose messages already copied.

Plain sync columns are `copied`, `forwarded`, `reuploaded`, `snapshots`,
`reply_flattened`, `quote_flattened_count`, `skipped_service`,
`skipped_unsupported_count`, `topics_created`, `cursor`, `clone_id`,
`source_peer_id`, `destination_peer_id`, `more`, `skipped_autoforward`,
`discussion_cursor`. The `participants` roster is JSON-only; the plain row does
not carry it.

## 12. Change Feed (`tg changes`; ADR-0063 / FEED-001)

```
tg changes --init [--peer P …]
tg changes --cursor C [--peer P …] [--drop-peer P …] [--wait N]
```

Foreground, daemonless update feed. One JSON document per invocation; no
state files — the opaque cursor is the only continuity and lives with the
caller. Works under `--readonly` and on any `--session-role` (ADR-0062).
It mutates nothing and writes no audit mutation rows.

`--init` baselines a new cursor from `updates.getState` (and, for each
`--peer`, the channel's current `pts` via `channels.getFullChannel`). It
rejects `--cursor`, `--drop-peer`, and `--wait` (exit 2). A missing or
corrupt `--cursor` on a regular call is exit 2 — never a silent
full-history replay.

`--peer P` (repeatable) **adds** a channel/supergroup subscription,
baselined at the current `pts` with a stderr note and **no history
replay**. Private dialogs and basic groups ride the common
`updates.getDifference` tier and cannot be subscribed (exit 2).
`--drop-peer P` removes a subscription; dropping an unsubscribed peer is
exit 2. Subscription membership lives **in the cursor**.

`--wait N` (N > 0) long-polls up to N seconds for the first event, then
waits a fixed **2-second settle** window (bounded by the remaining
deadline — N is never exceeded) to batch a burst. Without `--wait` the
command returns immediately with whatever is pending (no settle). When
`--wait` is set and no explicit `--timeout` is given, there is no
implicit 60s deadline so the wait budget is not clipped.

`--json` document:

```json
{"events":[…], "next_cursor":"v1:…", "gap":null,
 "skipped":{"UpdatePinnedMessage":2}}
```

Event vocabulary (additive forever):

| `type` | Body |
|--------|------|
| `message_new` / `message_edit` | `peer` (marked id), `message` in the exact `tg read` shape, `truncated` bool (Telegram short form — never padded) |
| `message_delete` | `peer`, `ids` (tombstone; body is not delivered) |
| `channel_activity` | `peer` only — an unsubscribed channel changed (`UpdateChannelTooLong`) |

Unhandled update classes are **counted** in `skipped` (never silently
discarded). `UpdateDeleteMessages` (private deletes with no peer in the
TL update) is counted there in v1.

When Telegram returns `differenceTooLong` or `channelDifferenceTooLong`,
`gap` is set and the cursor is rebased for that scope; exit **0** — a gap
is data:

```json
{"gap":{"scope":"common"|-1001234, "reason":"differenceTooLong|channelDifferenceTooLong",
 "recover":{"creation":"… read --after-id hint …", "edits_deletes":"lost"}}}
```

`--plain` columns: `events_count`, `next_cursor`, `gap_scope`,
`skipped_total`.

Boundary constants: common `GetDifferenceRequest.pts_total_limit =
100000`; per-channel `GetChannelDifferenceRequest.limit = 100` with
`ChannelMessagesFilterEmpty`.

## 13. Local Archive (`tg archive`; ADR-0068 Phase 1–5 + ADR-0069)

```
tg archive init
tg archive add CHAT
tg archive remove CHAT
tg archive list
tg archive status
tg archive search QUERY [--chat CHAT] [--from SENDER] [--since ISO]
  [--until ISO] [--kind KIND] [--transcripts-only]
  [--sort {relevance,date}] [--limit N] [--page N]
tg archive read CHAT [--around-id ID | --around-date ISO]
  [--since ISO] [--until ISO] [--limit N]
tg archive history CHAT MESSAGE_ID
tg archive backfill CHAT [CHAT ...] [--limit N]
tg archive backfill --private [--limit N] [--max-dialogs N]
tg archive sync [--max-events N] [--max-dialogs N] [--max-media N]
tg archive transcribe [--limit N] [--max-attempts N]
tg archive rebaseline
```

Per-account SQLite/WAL store under `~/.local/state/tgcli/archive/<alias>/`
(override with `[archive] root = "…"` in `config.toml`). Directory mode
`0700`; `archive.db` mode `0600`. Schema v4 tables: `messages`, `revisions`,
`tombstones`, `transcripts`, `scope`, `sync_state` (with peer identity
columns), `account_sync` (account-level `tg changes` cursor + gap), plus an
FTS5 index over message text and transcript text with
`tokenize = "unicode61 remove_diacritics 2"` (Cyrillic `ё`/`е` folded at
FTS write/query time). Opening a v1/v2/v3 store migrates in place. The message
payload is the universal `message_to_dict` JSON shape from `tg read` — not
a second representation.

**Account binding.** `init` binds the live `get_me().id` and selected alias
into `meta`. Every network command (`init`, `add`, `remove`, `backfill`,
`sync`, `rebaseline`) re-checks the live user id against the store before
touching data; mismatch is exit **2** and never merges stores. Offline
commands (`list`, `status`, `search`, `read`, `history`, `transcribe`) do not open a Telegram session; they
refuse an alias/store mismatch (exit 2) and report `NOT_FOUND` (exit 4)
when the store is missing.

**Scope.** Private 1:1 dialogs are a standing category — always in scope,
including future correspondents; `add` of a private user is exit 2.
Groups and channels join only via `add` / leave via `remove`. `list`
returns the standing category plus the explicit allowlist.

**Search.** Offline FTS5 over archived messages only. Default MATCH
is exact (no auto-prefix on short tokens); if `QUERY` contains FTS
operators such as `*`, `"`, `AND`/`OR`/`NOT`/`NEAR`, or parentheses, it is
passed through as a raw MATCH escape hatch. Default `--limit` is **20**;
hard cap **50 per page**. Empty/whitespace `QUERY`, non-positive/over-cap
`--limit`, non-positive/over-cap `--page`, or an inverted date range is exit
**2**. Optional `--chat` scopes to one peer
resolved offline from `scope` **or** `sync_state` identity
(`chat_ref` / `username` / numeric `peer_id`, including private peers that
have no `scope` row); unknown chat is exit **4**. JSON includes `hits`
plus a `scope` object noting archived-peers-only coverage and whether any
dialog has `more: true` (staleness). `--from` accepts a numeric sender id,
`@username`, or stored sender name. `--since`/`--until` are inclusive ISO
bounds. `--kind` accepts `text`, `photo`, `video`, `video_note`, `audio`,
`voice`, or `document`; `--transcripts-only` restricts MATCH to successful
transcript text. Relevance sorting uses SQLite FTS5 BM25 (`rank` ascending)
with message date as the recency tiebreak; `--sort date` uses newest date
first. `--page` is 1-based (default 1, hard cap 10,000); `has_more` and
`next_page` make the next bounded query explicit. Hits include a short
`snippet`, its `snippet_source`, `match_fields`, the stored HTTPS `permalink`
when available, and a `tg_link` of the form
`tg://openmessage?chat_id=PEER_ID&message_id=MESSAGE_ID` for a live handoff.
`--plain` emits one TSV row per hit: `peer_id`, `message_id`, `date`,
`chat_ref|title`, `text`, `transcript`, `transcript_status`, `tg_link`,
`snippet`.

**Offline timeline and history.** `tg archive read CHAT` never opens Telegram.
It returns at most 50 stored messages in chronological order. Without a
center it returns the newest bounded window; `--around-id` centers on a
message id and `--around-date` centers on an ISO date/datetime. `--since` and
`--until` further bound the window. Each message keeps the universal
`tg read` payload and adds `peer_id`, transcript fields, and `tg_link`.
`tg archive history CHAT MESSAGE_ID` returns the current stored body, all
append-only revisions, and a deletion tombstone when present. A message with
a tombstone has `status: "deleted"`; historical bodies remain readable. An
unknown chat is exit **4**, and a message with no current/revision/tombstone
record is exit **4**.

**Backfill.** Either one or more `CHAT` arguments **or** `--private`
(standing private category enumeration) — never both, and never an
empty→all sentinel. Default `--limit` is **100** messages per dialog; hard
caps are **1000** messages per dialog and **20** explicit `CHAT`s per
invocation. `--private` uses `--max-dialogs` (default **20**, hard cap
**100**) and skips dialogs whose last checkpoint already has `more:
false`. Groups/channels must be `add`ed first (exit 2 otherwise); private
dialogs may be backfilled without `add`. Each run walks recent→older
history (resuming from the stored oldest id), upserts the universal
message shape, appends revisions on edit, persists per-dialog identity on
`sync_state` (`kind`/`title`/`username`/`chat_ref`), and checkpoints
progress. Dialogs in one invocation are processed sequentially. A
`FLOOD_WAIT` during backfill arms the shared per-account cooldown
(ADR-0045/0052), persists the dialog checkpoint, and exits **5**.

**Sync.** `tg archive sync` holds an account-level `tg changes` cursor in
`account_sync` (not only per-peer). First run initializes the cursor via
`updates.getState`; later runs call `changes.once` (exact
`GetDifferenceRequest` / subscribed `GetChannelDifferenceRequest`). Explicit
scope channels are subscribed into the cursor. Apply vocabulary:
`message_new` / `message_edit` → upsert (+ revisions on edit); new private
peers auto-enter `sync_state`; `message_delete` → tombstones (channel
deletes carry `peer`; private `UpdateDeleteMessages` are resolved against
local message ids); `channel_activity` for scoped channels/groups triggers
a bounded catch-up (`iter_messages` with `min_id`). Difference events from
the poll are **always applied in full** before the cursor advances — there
is no apply-side truncation (dropping a tail while advancing the cursor
would silently lose archive history). Caps bound the expensive catch-up
RPCs only: `--max-events` is the catch-up **message** budget (default
**500**, hard cap **5000**) and `--max-dialogs` is catch-up dialog count
(default **20**, hard cap **50**). Non-positive / over-cap values are exit
**2**. Private `UpdateDeleteMessages` resolves peers by matching local
`message_id` values on users/basic groups only (channel/supergroup
`-100…` peers are excluded — their id space collides). If the same id
exists in more than one matching dialog, sync tombstones every matching
peer (MTProto does not name the peer). A `differenceTooLong`-class gap is
stored loudly in `account_sync` and surfaced by `status` / sync JSON; exit
**0** (a gap is data). Light reconciliation (sampled local vs Telegram
message totals, rotating across tracked dialogs) runs at the end of sync
and is reported under `reconcile` / `status.reconcile`.

**Media and transcription.** `backfill` and `sync` acquire queued `voice`
and `video_note` media into the account-local `media/` directory. Backfill
uses a fixed default budget of **50** media items per run; `sync` exposes
`--max-media`, which defaults to **50** and accepts at most **500** items.
The sync flag limits media downloads only, so message events and the sync
cursor are still applied in full. Downloads are idempotent: a transcript
queue row is marked with its controlled relative `media_path` only after the file is
published successfully. Download failures remain retryable and a
`FLOOD_WAIT` arms the shared account cooldown and exits **5**.

`tg archive transcribe` is foreground-only and offline. It drains the
newest ready media rows first through the local `transcribe` CLI
(FluidAudio/Parakeet), with `--limit` default **20** and hard cap **100**.
`--max-attempts` defaults to **3** and hard cap **5**. Successful rows store
transcript text, engine, and model version in `transcripts` and refresh the
FTS row. Retryable engine failures remain queued until the attempt cap;
terminal or exhausted failures become `no_transcript` with the last error,
so they remain visible in `status`; the `no_transcript` marker is also
searchable and every hit reports `transcript` plus `transcript_status`. A
rebuild or
message edit preserves an existing transcript rather than replacing it with
an empty FTS value.

**Rebaseline.** `tg archive rebaseline` is the explicit gap recovery:
re-inits the changes cursor (and re-subscribes explicit scope channels)
and clears the stored gap. It does not silently rebuild message history.

**Readonly.** `init` / `add` / `remove` / `backfill` / `sync` / `transcribe` /
`rebaseline` mutate local state and are blocked by `--readonly` /
`TGCLI_READONLY=1` (exit 2). `list`, `status`, and `search` are allowed
under readonly; `read` and `history` are also offline read-only commands.

`--json` shapes:

```json
{"created":true,"path":"…/archive/main/archive.db",
 "account":{"alias":"main","user_id":42},"schema_version":4}
```

```json
{"account":{"alias":"main"},
 "standing":{"kind":"private","description":"private 1:1 dialogs"},
 "explicit":[{"peer_id":-1001234,"kind":"channel","title":"News",
              "username":"news","chat_ref":"@news","added_at":"…"}]}
```

```json
{"account":{"alias":"main","user_id":42},"path":"…",
 "schema_version":4,
 "counts":{"messages":0,"revisions":0,"tombstones":0,"transcripts":0,
           "scope":0,"transcript_queue":0},
 "dialogs":[],"transcript_queue":0,
 "transcript_status":{"pending":0,"retryable":0,"done":0,"no_transcript":0},
 "transcript_errors":[],"last_errors":[],
 "gap":null,"last_sync_at":null,"reconcile":null,"has_cursor":false}
```

```json
{"account":{"alias":"main"},"query":"елка","match":"елка","match_mode":"exact",
 "limit":20,"page":1,"next_page":null,"has_more":false,
 "chat":null,"peer_id":null,
 "filters":{"from":null,"since":null,"until":null,"kind":null,
            "transcripts_only":false},"sort":"relevance",
 "hits":[{"peer_id":7,"message_id":1,"date":"…","text":"…",
          "transcript":null,"transcript_status":null,"kind":"text",
          "chat_ref":"@alice","title":"Alice","rank":-1.2,
          "match_fields":["text"],"snippet":"[[елка]]","snippet_source":"text",
          "permalink":null,"tg_link":"tg://openmessage?chat_id=7&message_id=1"}],
 "scope":{"archived_peers_only":true,"stale":true,
          "note":"Results cover archived peers only. At least one dialog still has more history on Telegram (more=true)."}}
```

```json
{"account":{"alias":"main"},"chat":"@alice","peer_id":7,
 "identity":{"chat_ref":"@alice","title":"Alice","username":"alice","kind":"user"},
 "around_id":42,"around_date":null,"since":null,"until":null,"limit":20,
 "messages":[{"id":42,"peer_id":7,"date":"…","text":"…","tg_link":"tg://openmessage?chat_id=7&message_id=42"}],
 "scope":{"archived_peers_only":true,"stale":false,"note":"Results cover archived peers only."}}
```

```json
{"account":{"alias":"main"},"chat":"@alice","peer_id":7,"message_id":42,
 "status":"deleted","current":{"id":42,"text":"new body","tg_link":"tg://openmessage?chat_id=7&message_id=42"},
 "revisions":[{"edited_at":"…","recorded_at":"…","message":{"id":42,"text":"old body"}}],
 "tombstone":{"deleted_at":"…"}}
```

```json
{"account":{"alias":"main","user_id":42},"mode":"chats","limit":100,
 "dialogs":[{"chat":"@alice","peer_id":7,"kind":"user",
             "dialog":{"id":7,"name":"Alice"},"stored":2,"inserted":2,
             "updated":0,"more":true,"oldest_id":2,"newest_id":3}],
 "stored":2}
```

```json
{"account":{"alias":"main","user_id":42},"mode":"private","limit":100,
 "max_dialogs":20,"dialogs":[…],"stored":5,"skipped_complete":2}
```

```json
{"account":{"alias":"main","user_id":42},"initialized":false,
 "max_events":500,"max_dialogs":20,"max_media":50,
 "applied":{"events":3,"received":3,"inserted":1,"updated":1,"edits":1,
            "tombstones":1,"skipped_out_of_scope":0,"channel_activity":0},
 "catchups":[],"gap":null,"skipped":{},"next_cursor":"v1:…",
 "media":{"limit":50,"queued":0,"downloaded":0,"skipped":0,"failed":[],"remaining":false},
 "reconcile":{"sampled":1,"mismatched":0,"next_offset":1,
              "comparisons":[…]},"requests":1}
```

```json
{"account":{"alias":"main","user_id":42},"rebaselined":true,
 "next_cursor":"v1:…","gap":null,"peers":["@news"]}
```

```json
{"account":{"alias":"main"},"limit":20,"max_attempts":3,
 "queued":2,"attempted":2,"transcribed":1,"retryable":0,
 "no_transcript":1,"skipped_missing_media":0,"remaining":false,
 "errors":[{"peer_id":7,"message_id":8,"status":"no_transcript",
             "error":"…"}]}
```

`--plain` rows: `init` → `created,alias,user_id,path`; `list` → standing +
explicit peer rows; `status` → counts summary (+ gap/cursor/transcript
statuses); `search` → per-hit
`peer_id,message_id,date,chat_ref|title,text,transcript,transcript_status,tg_link,snippet`;
`read` → per-message `id,date,from_name,text,tg_link`; `history` → status,
message id, current text, revision count, and deletion date;
`backfill` → mode +
per-dialog `chat,stored,inserted,updated,more` plus media counters; `sync` →
applied and media counters; `transcribe` → queue counters;
`rebaseline` → `rebaselined,peers,gap`.

`refresh` and `purge` remain later phases.
