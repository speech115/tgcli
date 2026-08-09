## 2026-08-09 — story media in `tg media download` (ADR-0076)

**Did:** `media download` now accepts story links — public
`t.me/<user>/s/<id>` and private `t.me/c/<id>/s/<id>`. `MediaSource` gains
`story_id`; `parse_source` recognizes the new shapes; resolution goes through
`stories.getStoriesByID` (boundary test asserts the exact request with an
`InputPeerChannel`). Resolution now returns a `_DownloadTarget` (media,
filename, size, codec) shared by messages and stories; the striped/checkpointed
transfer machinery is reused unchanged. `--codec {h264,h265,hevc,av1}` picks
an encoding from `document` + `alt_documents` by the `video_codec` attribute
(`hevc` aliases `h265`); no match is exit 4, no flag means the main document.
Story sources are single-download only (bulk flags → exit 2). Output keeps the
media shape with the `story:` source label and an additive `codec` field.
CONTRACT §5, SKILL.md, FEATURES.md, and the media guide document it.

**Decided:** 13 focused tests; the message-resolution tests were updated to
the `_DownloadTarget` shape (bulk fakes return targets now).

**Learned:** `t.me/c/<id>/s/<id>` cannot collide with the existing private
message regex (`/c/(\d+)/(\d+)`) because `s` is not a digit — story parsing
sits safely alongside. `documentAttributeVideo.video_codec` is the codec
signal Telegram actually ships (h264/h265/av1).

**Next:** merge both CONTRACT-changing slices as releases (2.0.1 transcribe,
2.0.2 story media) with the CHANGELOG/compare-link bookkeeping.
