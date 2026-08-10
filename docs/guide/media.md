# Media: inventory and download

`media manifest` lists media in a chat without downloading anything.
`media download` fetches one item, or many in bulk. Reach for `manifest`
first when you only need to know what is there.

## Inventory without downloading

```bash
tg --json media manifest CHAT --type photo --limit 50
```

| Flag | Effect |
| --- | --- |
| `--type {photo,video,audio,voice,document}` | keep only this media kind |
| `--since SINCE` | ISO 8601 lower bound on message date |
| `--limit N` | how many recent messages to scan (default 100) |

`manifest` walks recent messages newest-first with `iter_messages`, keeps
only the ones that carry media, and never downloads. `--since` stops the
walk at the first message older than the bound, so it only trims a
contiguous newest-first run, not an arbitrary date range. Each item is
`{"message_id", "type", "size", "mime", "filename"}`.

```json
{"dialog":{"id":3817664407,"name":"Channel"},"items":[{"message_id":42,"type":"photo","size":1234,"mime":"image/jpeg","filename":"a.jpg"}],"count":1}
```

`--plain` rows: `message_id`, `type`, `size`, `mime`, `filename`.

## Download one item

```bash
tg --json media download https://t.me/channel/42
```

`source` accepts a public `t.me/<username>/<message_id>` link, a private
`t.me/c/<channel_id>/<message_id>` link, or `CHAT ID` (chat reference plus a
positional message id).

| Flag | Effect |
| --- | --- |
| `--output PATH` | final file path; default `~/Downloads/<filename>` |
| `--parallel N` | opt-in parallel transfer; starts a fresh offset-based download, cannot resume |

An existing final path is refused and never overwritten. A single-stream
transfer (`--parallel` omitted or `1`) resumes a matching interrupted partial
file from `~/.local/state/tgcli/downloads/`; asking for `--parallel N`
greater than 1 always starts fresh and cannot resume. Progress goes to
stderr only.

"Matching" is the media, not the file name: the resume record holds the
document/photo id and the byte size, so if the source replaced the file behind
that message while your download was interrupted, the partial bytes are
dropped and the new file downloads whole. You get a one-line note on stderr
and `resumed: false` — never a silent mix of the two files.

```json
{"source": "@channel:42", "path": "/Users/me/Downloads/clip.mp4",
 "bytes": 104857600, "resumed": false, "parallel": 1}
```

`--plain` rows: `path`, `bytes`, `resumed`, `parallel`.

## Bulk download

```bash
tg --json media download CHAT --message-ids 10,11,12 --type video --since 2026-07-01 --limit 50 --output DIR --parallel 4
```

| Flag | Effect |
| --- | --- |
| `--message-ids ID,ID,...` | explicit ids for bulk download |
| `--type {photo,video,audio,voice,document}` | bulk: keep only this media kind |
| `--since SINCE` | bulk: ISO 8601 lower bound on message date |
| `--limit N` | bulk filter-mode cap (default 100, max 100) |
| `--output DIR` | destination directory (default `~/Downloads`) |

Bulk mode activates with `--message-ids` and/or the filter flags on a chat
reference with no positional `message_id`; combining a positional
`message_id` with any bulk flag is exit 2. When both `--message-ids` and
filters are given, the filters apply to those specific messages and
`--limit` caps the filtered result, preserving input-id order. Otherwise
(filters only, no explicit ids) the filters run over a `manifest`-style scan.

The hard cap is **100 downloads per invocation** — the length of
`--message-ids`, and the filter-mode `--limit`, both max out at 100 (default
100 when `--limit` is omitted). There is no `--all`; a run that needs more
than 100 items is issued again with a later `--since` or explicit ids.

```json
{"dialog":{"id":3817664407,"name":"Channel"},
 "items":[{"message_id":10,"path":"/Users/me/Downloads/a.jpg","bytes":2048,"resumed":false}],
 "count":1,"failed":[{"message_id":11,"error":"not found"}]}
```

A per-item not-found goes into `failed` and the loop continues; a
FloodWait, auth, or policy error stops the whole loop. Any non-empty
`failed` produces a nonzero exit (typically 4) while the JSON document is
still emitted on `--json`; files already written stay on disk.

## Story media

Story links `t.me/<user>/s/<id>` and `t.me/c/<channel_id>/s/<id>` download
story media — stories are not messages, so the same URL cannot be used with
`message` or the bulk flags.

```bash
tg --json media download https://t.me/kazbeksocrates/s/937
```

```json
{"source":"story:@kazbeksocrates:937","path":"/Users/me/Downloads/story.mp4",
 "bytes":14260634,"resumed":false,"parallel":1}
```

Video stories ship several encodings: the main document (typically HEVC) and
`alt_documents` alternatives (typically a smaller H.264 copy). `--codec`
picks one by its `video_codec` attribute — `hevc` is an alias of `h265`:

```bash
tg --json media download https://t.me/kazbeksocrates/s/937 --codec h264
```

A missing encoding is exit 4. Without `--codec` the main document downloads
as-is, and the result carries the additive `codec` field only when one was
selected.

## No default deadline

`media manifest` and `media download` (both forms) have no implicit overall
timeout — a large channel scan or a slow transfer may legitimately run past
the normal 60-second command deadline. An explicit `--timeout SEC` still
applies if given.

## See also

- [export.md](export.md) — bulk data extraction to a file instead of individual downloads
- [../CONTRACT.md](../CONTRACT.md) — canonical JSON shapes, caps, and exit codes
- [../../SKILL.md](../../SKILL.md) — one-line invocation recipes
