# CLI Automation Contract

Version: 0.1 (pre-implementation draft; frozen at first release).
Any change here lands in the same commit as the code change (AGENTS.md).

## 1. Invocation

```
tg [global-flags] <command> [subcommand] [args] [options]
```

Global flags (available on every command):

| Flag | Meaning |
|------|---------|
| `--account <alias>` | account alias from config; default: config `default_account` |
| `--json` | machine output: one JSON document to stdout |
| `--plain` | stable TSV to stdout (no colors, no alignment) |
| `--readonly` | hard-block any mutating call in this invocation |
| `--timeout <sec>` | overall invocation deadline (default 60; no default deadline for media and exports) |
| `-v/--verbose` | Python and Telethon debug diagnostics on stderr for this invocation |

Env equivalents: `TGCLI_ACCOUNT`, `TGCLI_READONLY=1`, `TGCLI_NO_SEND=1`.
Flag beats env, env beats config.

## 2. Streams

- **stdout** — contract data only. With `--json`: exactly one JSON document.
  With `--plain`: TSV rows. Default (human) mode: readable tables/text.
- **stderr** — everything else: progress, hints, warnings, error messages.
  With `--json`, the final error is also mirrored to stderr as a single-line
  JSON object: `{"error": {"code": "FLOOD_WAIT", "message": "...", "retry_after": 42}}`.

## 3. Stability Rules

- JSON: adding fields is allowed anytime; renaming/removing/retyping fields
  is a breaking change → requires ADR + major version bump.
- TSV: column order is frozen per command; new columns append at the end.
- Datetimes: ISO 8601 UTC (`2026-07-06T12:00:00+00:00`). IDs: as integers.

## 4. Exit Codes

| Code | Meaning | Typical cause |
|------|---------|---------------|
| 0 | success | |
| 1 | runtime error | network, unexpected exception |
| 2 | blocked by safety policy | `--readonly` + mutating command, `TGCLI_NO_SEND` |
| 3 | config/auth error | missing account, dead session, bad api_id |
| 4 | not found | unknown dialog, message id, media |
| 5 | rate limited | FloodWait longer than threshold; `retry_after` in error JSON |

## 5. Core JSON Shapes (phase 1–3)

`tg dialogs --json`:
```json
{"dialogs": [{"id": -1001234, "name": "Channel", "kind": "channel",
              "username": "chan", "unread": 3,
              "last_message_at": "2026-07-06T11:59:00+00:00"}]}
```

`tg read <chat> --json`:
```json
{"dialog": {"id": -1001234, "name": "Channel"},
 "messages": [{"id": 42, "date": "2026-07-06T10:00:00+00:00",
               "from": {"id": 111, "name": "Alice", "username": null},
               "text": "hello", "media": null, "media_info": null,
               "reply_to": null, "permalink": null, "edited_at": null,
               "outgoing": false, "forwarded_from": null, "reactions": [],
               "topic_id": null, "grouped_id": null, "is_service": false}],
 "page": {"oldest_id": 42, "newest_id": 42}}
```

All message-shape additions since 0.1 are additive; `media` remains the Telethon
class name string, `media_info` carries structured metadata.

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
               "reply_to": null, "permalink": null, "edited_at": null,
               "outgoing": false, "forwarded_from": null, "reactions": [],
               "topic_id": null, "grouped_id": null, "is_service": false}]}
```

`search` accepts `--from @username` to restrict results to that sender and
`--since ISO` as an inclusive lower date boundary. Search remains newest-first
and stops when it reaches a message older than `--since`.

`tg latest <chat> --json` and `tg message <chat> <message_id> --json` return
one message in that same shape:
```json
{"dialog": {"id": -1001234, "name": "Channel"},
 "message": {"id": 42, "date": "2026-07-06T10:00:00+00:00",
             "from": {"id": 111, "name": "Alice", "username": null},
             "text": "hello", "media": null, "media_info": null,
             "reply_to": null, "permalink": null, "edited_at": null,
             "outgoing": false, "forwarded_from": null, "reactions": [],
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

`tg count <chat> --json`:
```json
{"dialog": {"id": -1001234, "name": "Channel"}, "count": 73}
```

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

### TSV Shapes

`dialogs` retains its phase-1 columns. `read` and `search` output one row per
message as `id`, `date`, `from_name`, `text`; `latest` and `message` use the
same single-row shape. `info` outputs `id`, `kind`, `username`, `name`.
`count` outputs one `count` value.
`media download` outputs `path`, `bytes`, `resumed`, `parallel`. `send` preview
rows retain their existing columns and append `file`, `reply_to`. `edit` preview
rows are `preview_id`, `message_id`, `old_text`, `text`; `delete` preview rows
are `preview_id`, `message_id`, `text`. Both mutation commit rows are
`preview_id`, `message_id`.

```
tg send CHAT (TEXT | --file PATH [--caption TEXT]) --preview \
  [--reply-to MESSAGE_ID] [--topic TOPIC_ID] [--silent]
```

`tg send CHAT TEXT --preview --json` or a file preview returns:
```json
{"preview_id": "p_9f3a", "to": {"id": 111, "name": "Alice"},
 "text": "hello", "file": null, "file_size": null, "reply_to": null,
 "topic": null, "silent": false, "expires_at": "2026-07-06T12:05:00+00:00"}
```
For a file preview, `text` is the optional caption, `file` is its absolute
path, and `file_size` is its byte size. `--caption` requires `--file`; a file
send cannot take positional text. The stored preview additionally includes the
target, `kind: "send"`, and a positive `random_id` for the later idempotent
commit path.
Previews expire after five minutes. A send commit moves its preview through
`.json` → `.pending` → `.used`: a failed commit may be re-committed; Telegram
deduplicates by `random_id` within the preview TTL. Only a confirmed send marks
the preview used, and only after its result audit record persists. Commit JSON
is `{"preview_id": "p_9f3a", "message_id": 42}`.
Every authorised send commit appends one JSON object to
`~/.local/state/tgcli/audit.jsonl` (or `TGCLI_STATE_DIR/audit.jsonl`) before
network dispatch, including the stored `random_id`; a successful confirmed
commit appends `send-result` with its preview and message ids. If the pre-send
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
not consumed. The same readonly gates, five-minute `.json` → `.pending` →
`.used` lifecycle, retry behavior, and fail-closed audit boundary apply as for
`send`. Edit and delete commits have no `random_id`; their pre-dispatch audit
records are `edit` or `delete`, and successful result records are
`edit-result` or `delete-result`.

## 6. Raw API Passthrough (`tg api`, phase 2+; ADR-0010)

```
tg api <Namespace.method> --params '<json>' [--write] [--confirm <method>]
```

- `--params` is required and must be a JSON object. In phase 2, only the
  reviewed explicit allowlist in ADR-0010 may run through the configured
  session (35 methods as of 2026-07-10; e.g. `users.getFullUser`,
  `messages.getHistory`, `channels.getParticipants`).
- Without `--write`, every method outside the ADR-0010 read allowlist is
  blocked before config loading or session acquisition with exit 2.
- With `--write`, the same `--readonly`, `TGCLI_READONLY=1`, and
  `TGCLI_NO_SEND=1` gates run before config/session/network work. Destructive
  `delete*`, `reset*`, `leave*`, `block*`, `edit*Admin*`, and `edit*Banned*`
  methods require an exact `--confirm <Namespace.method>`; the permanent
  denylist `account.deleteAccount`, `auth.logOut`, `auth.resetAuthorizations`,
  and `account.resetAuthorization` is always exit 2. Authorised raw writes
  append one JSONL audit object before dispatch.
- `--json` output: `{"method": "users.getFullUser", "result": {…}}` where
  `result` is the TL object as a dict.
- **Stability exemption:** `result` mirrors the Telegram TL layer of the
  pinned Telethon version and may change when that pin is upgraded; the §3
  stability rules do not apply inside `result`. Everything outside `result`
  follows §3 as usual.

## 7. Export (phase 5)

```
tg export messages <chat> --output <path> [--limit <n>]
tg export subscribers <channel> --output <path> [--limit <n>]
```

- `--output` is required. It is the only destination for the export records;
  the command writes a sibling temporary file and replaces the destination only
  after the complete export succeeds. An existing destination is unchanged on
  a failed export.
- `messages` iterates through a Telethon takeout session from oldest to newest.
  The destination is UTF-8 JSONL: one `read`-shape message object per line,
  with `id`, `date`, `from`, `text`, `media`, and `reply_to` fields.
- `subscribers` writes UTF-8 CSV with the frozen header
  `id,username,first_name,last_name,phone,is_bot`; standard CSV quoting is
  used for field values. Username and name cells beginning with `=`, `+`, `-`,
  or `@` are prefixed with a single quote so spreadsheet programs do not
  interpret them as formulas.
- Success on `--json` is one completion document:
  `{"export":{"kind":"messages|subscribers","format":"jsonl|csv",
  "path":"<path>","count":42,"dialog":{"id":-1001234,"name":"Channel"}}}`.
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
`timestamp`, `command`, resolved `account` when applicable, `exit_code`,
structured `error` code when applicable, and `duration_ms`. The journal never
contains message/search text, chat references, raw API parameters, or command
output. A journal-write failure emits a warning to stderr but does not change
the command result.

`-v` / `--verbose` enables Python and Telethon debug logs on stderr for the
current process. Stdout remains contract data in all output modes.

## 10. Accounts (phase 6)

```
tg accounts import [ALIAS ...] [--source-root PATH] [--force]
```

This is a local-only command: it never opens a Telegram connection. With no
aliases it tries `main`, `recklessou`, and `teamsyncsage`, and reports a
missing old-stack source as a warning rather than failing. An explicitly named
missing source exits 4. The command copies old-stack SQLite sessions with an
online backup into `TGCLI_STATE_DIR/sessions`; an existing destination is left
untouched unless `--force` is supplied. A busy destination lock or missing or
unparseable credentials for a newly configured account exits 3.

`--json` emits:

```json
{"imported": [{"alias": "pl", "session": "/home/me/.local/state/tgcli/sessions/pl.session",
               "status": "imported|skipped_existing|source_missing",
               "config": "added|unchanged"}]}
```

`--plain` emits frozen TSV columns: `alias`, `status`, `config`.

## 11. Chat Clone (ADR-0017, ADR-0021, ADR-0022, ADR-0023)

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
below), or `"none"` (no linked group, or a non-broadcast source). Existing
clones from before this feature have `comments: "none"` and are never
retroactively upgraded in place.

```text
tg clone status [SOURCE]
tg clone init SOURCE
tg clone init SOURCE --replace
tg clone init SOURCE --commit PREVIEW_ID
tg clone sync SOURCE [--limit N]
```

`status` is local and read-only: it never loads config or opens a Telegram
session. Without `SOURCE` it lists every JSON state file; with `SOURCE` it
filters by exact numeric source id or case-insensitive title substring. JSON:

```json
{"clones":[{"clone_id":"hex","source":{"id":123,"title":"Source","kind":"broadcast"},"destination_id":999,"cursor":42,"copied":40,"cooldown_until":null,"created_at":"2026-07-15T12:00:00+00:00","last_synced_at":null,"comments":"enabled"}]}
```

Plain status columns are `source_peer_id`, `source_title`, `source_kind`,
`destination_peer_id`, `cursor`, `copied`, `last_synced_at`, `comments`.

A corrupt or legacy (unsupported-version) state file never aborts the listing:
without `SOURCE` it appears as a marked entry `{"clone_id":"hex","unreadable":
true,...}` with every other field null, and in plain output its `source_title`
column carries the `clone_id` and its `comments` column reads `unreadable`.
Because an unreadable file's identity cannot be matched, it is omitted from
`SOURCE`-filtered listings. Readable entries never carry the `unreadable` key.

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
{"preview_id":"p_...","expires_at":"...","clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"dialog"},"destination":null,"status":"planned","commit_required":true},"approximate_message_count":321,"protected":false,"supersede":{"existing":false,"readable":null,"replace":false}}
```

`init SOURCE --commit PREVIEW_ID` requires a matching unexpired clone-init
preview. `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block before
preview consumption, config, session, audit, or Telegram work. The commit uses
a mutation-safe session, verifies that the resolved account id, source id, and
source kind still match the preview, then creates or recovers one private
creator-owned destination of the source-dependent kind. JSON:

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"dialog"},"destination":{"id":999,"title":"Source"},"comments":"none","status":"ready","commit_required":false}}
```

Plain init columns are unchanged by comments support: `status`, `clone_id`,
`source_peer_id`, `destination_peer_id`. `comments` is JSON-only on `init`.

For a broadcast source, commit also creates or recovers a second peer: a
private owned megagroup titled `<creation_marker>-discussion`, using the
same crash-recovery marker-scan discipline as the destination channel (a
crash between group-create and `SetDiscussionGroupRequest` recovers by
re-adopting the marked group and relinking — idempotent). Its
title/about/avatar are copied from the source discussion group, then
`channels.SetDiscussionGroupRequest` links it to the destination channel
strictly before `sync` sends the first post. A linked source group that
cannot be read (private, not a member) skips group creation entirely and
sets `comments: "unavailable"`; a source with no linked group at all sets
`comments: "none"`. Non-broadcast sources never attempt this and always
report `comments: "none"`. Init's live ~15h FLOOD_WAIT window on rapid peer
creation applies to up to two peers per run instead of one.

Before creation, state with `destination_peer_id:null` and title marker
`tgcli-clone-<clone-id-prefix>` is atomically saved under
`TGCLI_STATE_DIR/clones/<clone_id>.json`. Recovery adopts exactly one matching
private creator-owned destination of the required kind, creates when none
exists, and exits 2 without mutation on multiple or wrong-shape matches. Once a
destination id is recorded, repeated init resolves and reuses it without
scanning or creating. Destinations are user-owned and never deleted
automatically.

Because `clone_id` is deterministic per source, one source maps to one state
slot forever, and `commit` fail-closes (exit 2) on any slot whose version it
cannot load — so a stale or legacy clone cannot be re-created by a bare `init`.
`init SOURCE --replace` supersedes it: at commit, before loading state, it
archives the existing `<clone_id>.json` and, if present, the ADR-0024 roster
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

After creation or recovery, init applies the source title/display name, copies a
non-empty channel or basic-group description, or User bio, and copies a
non-empty static source avatar before returning `status: ready`. Empty source
fields cause no mutation.
Avatar bytes use a temporary directory that is removed on success or failure.
Animated or video avatar motion is not preserved (ADR-0020).

Every create, title-edit, description-edit, and avatar-edit attempt appends a
fail-closed shared audit record before dispatch. A profile-copy failure exits 2
while retaining the recorded destination for a new-preview retry; it never
creates a second destination. Telegram FloodWait during profile reads, downloads,
uploads, or edits persists `retry_not_before` in clone state; later commit
attempts exit 5 locally while that deadline is active. Init keeps the global
60-second default timeout.

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
shown. Story references become two-line `Stories недоступна` placeholders whose
resolved author name/title is a clickable `t.me` link when possible; Story IDs
are not shown. Both use the audited `clone-sync-snapshot` path, receive
source-to-destination mappings, and count as copied. Truly unsupported kinds
such as dice advance the cursor and appear in `skipped_unsupported`; nothing is
skipped silently (ADR-0019).

For forum clones, a topic-create service message creates the matching
destination topic (counted in `topics_created`, not `skipped_service`); messages
arriving for an unmapped topic recover it from the source topic's current title.

When `comments == "enabled"`, sync runs a second phase after phase 1
(channel posts) reaches exhaustion: it copies the linked source discussion
group into the clone's own linked group, oldest to newest, under its own
cursor (`discussion_cursor` in state and in the JSON `sync` object; the top-
level `sync.clone` object itself carries no `comments` field). `--limit N`
is not split between phases — phase 1 spends the full budget first, and
phase 2 only starts if phase 1 did not stop on the limit; a run that stops
inside phase 2 leaves comments lagging posts until the next invocation.
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
or any post from a protected source, travels by reupload and loses `fwd_from`.
For attributed megagroup, forum, basic-group, and dialog sources, the
same batch uses native forwarding with `drop_author=False`, retaining
Telegram's author header.

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
real in-topic replies map both their parent and topic. Non-forum clones reject
forum reply headers. Cross-peer, scheduled, ephemeral, todo, poll-option,
reply-from, reply-media, malformed quote, and inconsistent album reply shapes
exit 2 before audit or mutation. Supported quote text, entities, and offset are
retained.

Reupload sends text and webpage messages with `sendMessage`, photos/documents
with `sendMedia`, and albums with per-item `uploadMedia` followed by one
ordered `sendMultiMedia`. Captions and entities are retained; documents retain
MIME type and Telegram attributes. Downloaded files live only in a temporary
directory and are removed on success or failure. A download failure leaves the
batch cursor and mapping unchanged and occurs before the fail-closed
`clone-sync-reupload` audit/write boundary. Upload/send FloodWait persists the
clone cooldown. Both `UpdateMessageID` batches and the single-message
`UpdateShortSentMessage` envelope require exact positive confirmation before
state advances.

`--limit N` must be positive and copies at most N message batches. If another
source row remains, JSON reports `"more":true`; the next run resumes at the
saved cursor. Sync has no implicit overall timeout, uses a mutation-safe
session, and is blocked by all readonly gates before config/session work.
FloodWait persists the clone cooldown and exits 5 without advancing the current
message. JSON:

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"broadcast"},"destination":{"id":999,"title":"Source"}},"sync":{"copied":2,"skipped_unsupported":[{"id":4,"kind":"MessageMediaDice"}],"forwarded":1,"reuploaded":1,"snapshots":0,"topics_created":0,"skipped_service":1,"skipped_autoforward":0,"reply_flattened":0,"cursor":5,"discussion_cursor":0,"more":false,"participants":{"path":"~/.local/state/tgcli/clones/hex-participants.jsonl","source":{"peer_id":123,"status":"unavailable","count":0,"reason":"ChatAdminRequiredError"},"discussion":{"peer_id":55,"status":"collected","count":42,"reason":null}}}}
```

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
`reply_flattened`, `skipped_service`, `skipped_unsupported_count`,
`topics_created`, `cursor`, `clone_id`, `source_peer_id`,
`destination_peer_id`, `more`, `skipped_autoforward`, `discussion_cursor`. The
`participants` roster is JSON-only; the plain row does not carry it.
