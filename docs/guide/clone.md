# Clone a chat

`tg clone` copies a supported chat's history into a private, tool-created destination and lets you catch it up later. It replaced an earlier `tg mirror` implementation (see [ADR-0017](../decisions/ADR-0017-clone-supersedes-mirror.md)).

## What can be cloned

Accepted source kinds, per [CONTRACT.md §11](../CONTRACT.md):

| Source kind | Destination |
| --- | --- |
| Broadcast channel | private owned broadcast channel |
| Megagroup supergroup, non-forum | private owned broadcast channel |
| Megagroup supergroup, forum | private owned forum megagroup, with a 1:1 topic map |
| Live legacy basic group | private owned broadcast channel |
| Private one-to-one dialog (including bots) | private owned broadcast channel |

Basic groups that migrated to a supergroup, deactivated groups, and any other peer shape exit 2 with a source-specific policy message. A broadcast source with a readable linked discussion group additionally gets a second destination megagroup cloning its comments ([ADR-0023](../decisions/ADR-0023-clone-channel-comments.md); attribution for megagroups/dialogs is [ADR-0021](../decisions/ADR-0021-clone-attributed-sources.md), forum topics are [ADR-0022](../decisions/ADR-0022-clone-forum-topics.md)). Destinations are always tool-created — cloning into a pre-existing or shared chat is not supported, and destinations are never deleted automatically.

## Check clone status

```bash
tg --json clone status [SOURCE] [--all]
```

`status` is local and read-only: it never loads config or opens a Telegram session. Without `SOURCE` it lists every clone state database; with `SOURCE` it filters by exact numeric source id or a case-insensitive title substring. Each readable entry includes `schema_version` and `integrity` (ADR-0060).

```json
{"clones":[{"clone_id":"hex","source":{"id":123,"title":"Source","kind":"broadcast"},"destination":{"id":999,"title":"[Clone] Source","username":null},"cursor":42,"copied":40,"cooldown_until":null,"created_at":"2026-07-15T12:00:00+00:00","last_synced_at":null,"comments":"enabled","schema_version":2,"integrity":"ok"}],"pending_import":0}
```

A clone destination is private, so `destination.title` is the only readable
name it has; `clone init` and `clone sync` record it as they resolve the peer,
and it stays `null` on a clone neither has touched since. `pending_import`
counts state slots this version cannot import — they carry no information at
all, so they are counted rather than listed, and `--all` shows them.

`--plain` columns: `source_peer_id`, `source_title`, `source_kind`, `destination` (title, or the peer id when no title is recorded), `cursor`, `copied`, `last_synced_at`, `comments`.

## Export clone state (rollback / diagnostics)

```bash
tg clone export-state SOURCE
```

Prints one clone's state as the v2 JSON document on stdout (always JSON). Use this to back up or roll back to a previous tgcli binary. `SOURCE` must match exactly one readable clone.

## Initialize a clone: preview then commit

`init` is the only clone step gated by preview → commit. Running it without `--commit` is the preview: a read-only network call that resolves the source, checks its kind is accepted, and stages the destination creation behind a five-minute single-use `preview_id`. Nothing is created yet.

```bash
tg --json clone init SOURCE
```

```json
{"preview_id":"p_...","expires_at":"...","clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"dialog"},"destination":null,"status":"planned","commit_required":true},"approximate_message_count":321,"protected":false,"supersede":{"existing":false,"readable":null,"replace":false},"peers_to_create":1}
```

Then commit to actually create the destination:

```bash
tg --json clone init SOURCE --commit PREVIEW_ID
```

| Flag | Effect |
| --- | --- |
| `--commit PREVIEW_ID` | consume the preview and create/recover the destination |
| `--replace` | supersede an incompatible or stale clone state slot; declared at preview time, honored at commit |
| `--no-comments` | posts-only clone (`comments: "disabled"`); creates one peer, skips discussion; declared at preview time |

Commit also mutes tool-created peers forever and files them into the Telegram
folder `Clone` (best-effort). JSON reports `ergonomics: {muted, folder}` —
`"added"` / `"present"` / `"unavailable"` for the folder. Failures warn on
stderr and never fail init. Re-run init on an existing clone to retrofit.

`peers_to_create` is 0 / 1 / 2 depending on whether a destination is already
recorded and whether this commit would also create a discussion group.
Requests are paced and governed per Telegram request type (ADR-0072). Exit
5 on `init --commit` / `sync` means the type is cooling: the run refused
locally with `retry_after`, and `tg doctor` reports the cooldown directly.
Never retry in a loop — the governor probes once at half the wait, and the
schedule resumes the run when the type clears. Prefer at most ~one
peer-creating init per account per day.

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"dialog"},"destination":{"id":999,"title":"Source"},"comments":"none","status":"ready","commit_required":false}}
```

`--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block `--commit` before config, session, or Telegram work; the read-only preview step is not blocked by those gates. Because `clone_id` is deterministic per source, one source maps to one state slot forever — a stale or version-mismatched slot fails commit closed (exit 2) until you re-run `init SOURCE --replace`, which archives the old state file (never the old Telegram destination) and starts a fresh destination pair.

## Sync: copy and catch up

```bash
tg --json clone sync SOURCE
```

| Flag | Effect |
| --- | --- |
| `--limit N` | copy at most N message batches this run; must be positive |

`sync` requires an initialized clone and reads new source history from the saved cursor forward (`reverse=True`, `min_id=cursor`), so destination order matches source order. It first verifies the destination's tail is exactly what tgcli expects (only Telegram service rows past the last confirmed message); an unexpected tail message exits 2 for manual repair before any copying. `--limit` caps this run; if source rows remain, the JSON reports `"more":true` and the next invocation resumes at the saved cursor. Sync's requests are paced by the governor; `clone sync` keeps no implicit deadline (CONTRACT §1), and an explicit `--timeout` is a hang detector that ignores governed sleep, so a paced sync is not punished for pacing. Pass `--max-runtime` to bound the whole run — exhausting it is a normal stop (exit 0) with a resume pointer.

```json
{"clone":{"id":"hex","source":{"id":123,"title":"Source","kind":"broadcast"},"destination":{"id":999,"title":"Source"}},"sync":{"copied":2,"skipped_unsupported":[],"forwarded":1,"reuploaded":1,"snapshots":0,"topics_created":0,"skipped_service":1,"skipped_autoforward":0,"reply_flattened":0,"quote_flattened":[],"markup_dropped":[],"poll_votes":[],"cursor":5,"discussion_cursor":0,"more":false,"pinned":{"source_id":12,"destination_id":9,"status":"set"},"participants":{"path":"~/.local/state/tgcli/clones/hex-participants.jsonl","source":{"peer_id":123,"status":"unavailable","count":0,"reason":null},"discussion":{"peer_id":null,"status":"none","count":0,"reason":null}}}}
```

On a broadcast destination, a run that copies everything it planned also carries
the source's pinned message over and reports it in the additive `sync.pinned`
object ([ADR-0055](../decisions/ADR-0055-clone-pinned-and-photo-fidelity.md)):
`set` when this run placed the pin, `unchanged` when the clone already pinned
once (later runs answer from state without a pin call), `unmapped` when the
source has no pin or its pinned message is not in the clone's id map yet, and
`occupied` when the destination already carried a pin, which the clone leaves
alone. Unpinning is never mirrored, and forum destinations omit the key.

`--plain` columns: `copied`, `forwarded`, `reuploaded`, `snapshots`, `reply_flattened`, `quote_flattened_count`, `skipped_service`, `skipped_unsupported_count`, `topics_created`, `cursor`, `clone_id`, `source_peer_id`, `destination_peer_id`, `more`, `skipped_autoforward`, `discussion_cursor`, `markup_dropped_count`.

## Refresh: backfill missing forward prefixes

```bash
tg --json clone refresh SOURCE
tg --json clone refresh SOURCE --commit PREVIEW_ID
```

Use this when a clone was copied before a body-prefix rule shipped (for
example ADR-0050's `Переслано от <label>` line) and some destination posts
still read as unattributed originals. A bare `refresh` is the preview: it
lists eligible posts (destination body still byte-identical to the unprefixed
source, and today's renderer would add a prefix) and exclusions (poll
snapshots, native re-forwards, album non-lead items). It never recreates or
reorders messages — commit only edits text and entities on the existing
destination ids. Media, `id_map`, and both cursors stay untouched. A second
run after a successful commit finds nothing left to fix.

## Native forward vs reupload

Each message batch picks one of two transports:

- **Native forward** — cheap, no download/upload. Broadcast sources forward with `drop_author=True` for the channel's own posts (clone reads as native content), and `drop_author=False` for posts that are themselves re-forwards, restoring the original forward header ([ADR-0025](../decisions/ADR-0025-clone-preserve-reforward-header.md)). Megagroup, forum, basic-group, and dialog sources always forward with `drop_author=False`, keeping Telegram's author attribution.
- **Download/reupload** — used whenever the source or message has `noforwards` (protected), or the message has a mapped reply that native forwarding cannot attach. Reuploads from an attributed source prepend `<display name>: ` to the text/caption to preserve authorship, since a reupload cannot carry Telegram's forward header. A protected source can never be forwarded, so it always reuploads and the native re-forward header is lost; when the source post itself carries `fwd_from`, the clone still prepends a truthful `Переслано от <label>` (or bare `Переслано`), including a channel title Telegram already shipped with the message even if a later GetChannels refuses that peer ([ADR-0050](../decisions/ADR-0050-clone-forward-attribution.md), [ADR-0064](../decisions/ADR-0064-forward-origin-from-message-chat.md)). A reuploaded document keeps its MIME type, its Telegram attributes, and the source's still-image thumbnail, so a PDF still renders as a page-preview card; a thumbnail Telegram refuses to hand over is dropped and the copy continues without it.

Unsupported message kinds (dice, etc.) advance the cursor and are reported in `skipped_unsupported`, never silently dropped. TTL/view-once media is also reported there rather than forwarded or downloaded. Each skip prints a stderr warning after its cursor is saved, so a later flood cannot hide work that future runs will not revisit.

An unreachable or send-rejected cross-chat quote is copied as a text fallback and recorded in `quote_flattened`. Each fallback prints its source id and reason to stderr as soon as the copy is durable. A completed run with any fallback still exits 2 with its full result; a later flood still exits 5, with the earlier warning preserved.

Bot buttons do not survive a reupload or a snapshot. A keyboard belongs to the bot that attached it: a user account cannot send one, and an album could not carry one even if it could, so only a native forward keeps the rows — and a protected source never takes that path. The clone does not rebuild the buttons in any form, not even URL rows as text. Each affected copy is listed in `sync.markup_dropped` with its button classes and prints a warning to stderr as it is copied — a run cut short by a flood leaves no result document, so the stderr lines are the record — and the run still exits 0 ([ADR-0085](../decisions/ADR-0085-a-clone-does-not-invent-a-bots-keyboard.md)).

## What clone does not do

- No watcher and no background process: a clone destination does not stay live in sync with its source. `sync` is an explicit, foreground invocation you run again whenever you want to catch up.
- No choice of destination kind: it always follows the source kind.
- No cloning into a pre-existing or shared chat — destinations are always freshly created (or recovered) by `init`.
- No automatic retry loop across FloodWait: a rate-limited sync exits 5 and you re-run `sync` after the reported cooldown.
- No bot chrome: inline keyboards and reply keyboards are not recreated on any copy path, and already-cloned posts are never revisited to add them.

## See also

- [export.md](export.md) — one-shot data extraction instead of a live destination chat
- [../CONTRACT.md](../CONTRACT.md) — §11 canonical clone contract
- [../../SKILL.md](../../SKILL.md) — one-line invocation recipes
