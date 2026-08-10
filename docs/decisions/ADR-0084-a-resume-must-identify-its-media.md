# ADR-0084: A resume must identify its media, not its path

Date: 2026-08-10
Status: accepted
Form: ADR-lite (ADR-0058)
Extends: [ADR-0083](ADR-0083-a-flood-must-not-destroy-finished-work.md)'s
resume identity to `media download`
Closes: #180

## Context

The pre-merge review of ADR-0083 found that the clone reupload resume trusted
a partial file on byte size alone, and the fix recorded Telegram's
document/photo id beside it. The same review noted that
`commands/media.py::_resume_offset` — the sibling that has had resumable
downloads since 1.0 — is weaker still: its record holds the source label
(`chat:message_id`) and the destination path, and nothing else. Neither the
byte size nor any media identity is stored.

So a `media download` interrupted mid-transfer, whose source then edits the
message and replaces the file, resumes onto the wrong bytes: the new file's
tail is appended to the old file's head, `_publish` moves it to the final
name, and the result is reported `resumed: true` with no error. The output is
a splice of two files that opens, has a plausible length, and is neither of
them. Easier to trigger than the clone case ADR-0083 closed, because there
not even the size had to match.

ADR-0083 reported this rather than fixing it: changing the on-disk record of a
released command is its own decision. This is that decision.

## Decision

The resume record gains `media_id` (Telegram's document or photo id, `null`
when the media exposes none) and `size`, and `_resume_offset` reuses a partial
file only when both still match the media it is about to download. On a
mismatch the partial file and its record are deleted and the transfer restarts
from zero — with a one-line stderr note, because a 2 GB download silently
starting over is worse than one that says why.

A mismatch is **not** an error. The operator asked for the media that is there
now; the source changing it is not operator error, so it is neither exit 2 nor
a prompt. This is the `resumable: false` precedent (a parallel transfer's
scattered stripes), not the "state does not match requested output"
precedent (a confused invocation).

`media_identity` moves to `tgcli/transfer.py` beside `media_byte_size` and is
shared by both callers. It accepts a `MessageMedia*` wrapper, a bare
`Document` (what a story's `--codec` pick hands over), or anything else
carrying an `id`.

Records written by earlier versions have no `media_id`/`size` keys, so they
compare unequal and restart once. In-flight partial downloads from before this
change therefore restart; that is the intended cost, not a migration to write.

## Rejected alternatives

- **Hashing the downloaded prefix.** Telegram serves no whole-file hash to
  compare against, so verification would mean re-reading the prefix over the
  network — the cost the resume exists to avoid.
- **Raising `PolicyError` on a changed media.** It reads as operator error and
  wedges scripts on something the operator cannot fix; the file they asked for
  is downloadable right now.
- **Recording only `media_id`.** A photo whose `id` is stable across a
  re-crop, or media exposing no id at all, would still splice. Size is nearly
  free and closes those.
- **Leaving `media download` as-is because the case is rare.** Rarity is not
  the problem; silence is. The failure produces a corrupt file with a correct
  name, a plausible size, and `resumed: true`.

## Contract impact

`docs/CONTRACT.md` §3 (`media download`): "matching" is defined as the media,
not the path; a changed media restarts the transfer and reports
`resumed: false` with a stderr note. No flag, JSON field, or exit code
changes. The state file under `~/.local/state/tgcli/downloads/` gains two
keys; it is internal state and not part of the contract.
