# ADR-0076: Story media in `tg media download`

Date: 2026-08-09
Status: accepted (2026-08-09; owner request #156)

## Context

`t.me/<channel>/s/<id>` links point at stories, which are not messages: today
`tg media download` rejects them (`NOT_FOUND: invalid media source`) and
`tg message <chat> <id>` cannot address them either. Story media is reachable
through `stories.getStoriesByID` (already read-allowlisted), and its video
documents carry `alt_documents` — alternative encodings of the same video,
typically a smaller H.264 copy beside the main HEVC one. The read-only
allowlist now includes `upload.getFile` (the minimal fallback of #156, merged
2026-08-09), so the file bytes are fetchable; what is missing is a wrapper
that parses the link, resolves the story, picks an encoding, and downloads.

## Decision

Extend `tg media download` to accept story links:

- `MediaSource` gains `story_id`; `parse_source` recognizes
  `t.me/<user-or-channel>/s/<id>` and private `t.me/c/<channel-id>/s/<id>`.
  A story source is single-download only: combining it with the bulk flags
  (`--message-ids`, `--type`, `--since`, `--limit`) is a `PolicyError`.
- Resolution uses `stories.getStoriesByID(peer, [story_id])`; a missing story
  raises `NotFoundError`. The download target is the story's `media`
  (photo or document) and reuses the existing striped/checkpointed download
  machinery — Telethon's `iter_download` issues `upload.getFile` under the
  hood, so no separate file-transfer path is added.
- Encoding selection is opt-in via `--codec {h264,h265,hevc,av1}` (`hevc`
  aliases `h265`, matching `documentAttributeVideo.video_codec` values).
  Without the flag the main document downloads as-is. With a codec, the
  matching document is chosen from `document` + `alt_documents` by its
  `video_codec` attribute; no match raises `NotFoundError` naming the codec.
- Output keeps the media-download shape, with `source` labeled
  `story:<peer>:<id>` and an additive `"codec"` field naming the chosen
  encoding (present only when `--codec` selected one).

The allowlisted `upload.getFile` stays available for raw callers; this
command is the task-first wrapper around it.

## Rejected alternatives

- A separate `tg story download` command: the download flow, output shape,
  resume/parallel machinery, and progress reporting are identical to media
  download; a new command would duplicate the whole surface for one URL
  shape.
- Always picking the smallest `alt_documents` copy by default: silently
  degrades the native quality of a story nobody asked to shrink; the flag
  keeps the default truthful (the main document) and the choice explicit.
- Implementing download through manual `upload.getFile` calls: reimplements
  what `iter_download` already does (locations, offsets, CDN fallback,
  striped workers) — new code to carry, new failure modes to test.
- Resolving stories through `messages.getMessages` or permalink scraping:
  stories are not messages and `t.me` scraping yields only the og-image.

## Contract impact

- `media download` accepts story sources and a new `--codec` flag (CONTRACT
  §5 media section); output gains the additive `codec` field and the
  `story:` source label.
- FEATURES.md `stories` row notes the download workflow; README, SKILL.md,
  and the media guide page document the new inputs.
