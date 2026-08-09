# ADR-0078: `--codec` applies to story sources only

Date: 2026-08-09
Status: accepted (2026-08-09; owner request: post-review campaign 2026-08-09)

## Context

ADR-0076 added `--codec {h264,h265,hevc,av1}` to `tg media download` for
story-video encoding selection. The flag lives on the shared `media download`
subparser, so it is also accepted — and silently ignored — for message and
bulk sources: dispatch never forwarded it and `resolve_message` consulted it
only for story sources. A caller passing `--codec` with a message link or
bulk flags got exit 0 with no `codec` field: an accepted-but-ignored flag
that hides the mistake. The independent post-merge review of the ADR-0076
slice (2026-08-09) flagged this as a CLI-boundary defect and offered
rejection as the tightest fix.

## Decision

`tg media download` rejects `--codec` on non-story sources. When the flag is
present and the source is not a story link (`t.me/<peer>/s/<id>` or
`t.me/c/<channel-id>/s/<id>`), the invocation fails with `PolicyError` exit 2
(`BLOCKED`) and the message `--codec applies to story sources only`, before
any network work. The check sits at the dispatch level and covers both the
single-message path and bulk mode.

## Rejected alternatives

- Documenting the ignore in CONTRACT §5: relabels the defect as contract
  instead of removing it; scripts keep silently succeeding without the
  encoding they asked for.
- Forwarding `--codec` to message/bulk downloads: message documents also
  carry `alt_documents`, but the story `video_codec` selection semantics do
  not map onto message media, and the ADR-0076 scope is story links only.

## Contract impact

CONTRACT §5 `media download` gains one sentence: `--codec` applies to story
sources only; using it with a message or bulk source is exit 2 (`BLOCKED`).
The exit-code taxonomy is unchanged (exit 2 already exists).
