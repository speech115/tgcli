## 2026-08-09 — post-review fixes: transcribe + story media

**Did:** Retroactive two-axis review of the six merged PRs (157–162) found
seven major findings; this slice fixes the transcribe and story-media ones,
test-first. (1) `tg transcribe` (ADR-0075): the update wait matched only
`msg_id`, so a concurrent transcription of the same message — another dialog,
or a re-run — could satisfy it with foreign text; the handler now parks early
updates and matches on `result.transcription_id` once the RPC returns. (2)
`tg media download` (ADR-0076): `--codec` on a photo story silently
downloaded the photo; it now raises the same exit-4 `NOT_FOUND` as any
missing encoding. (3) A text/emoji story (no media at all) fell through to
the photo branch and died later with a `RUNTIME` exit 1; `_story_target` now
accepts only document/photo media and raises exit-4 `NOT_FOUND` otherwise.
(4) Photo stories reported no size, so `--parallel` always failed; the size
now mirrors Telethon's own byte-count computation (progressive sizes take
their max). (5) `--codec` was silently ignored on message and bulk sources —
now exit 2 `BLOCKED` before any network work (ADR-0078). (6) The transcribe
`--timeout` expiry promised the `transcription_id` (CONTRACT §5) but the
outer invocation deadline always won, dropping it from both JSON and plain
output; `_run_with_deadline` now disarms the SIGALRM backstop and lets the
command's own deadline surface within the grace window, and the plain error
line carries the id.

**Decided:** ADR-0078 (full form): `--codec` is story-only; message/bulk use
is exit 2. CONTRACT §5 updated: codec-scope sentence, and the `read`/`search`/
`transcribe` dialog examples corrected to the actual positive `entity.id`
(the negative bot-API form was copied into the new transcribe example from an
existing drift). MAP.md gains the `transcribe.py` row and the ADR-0076 media
description. Campaign plan: docs/plans/2026-08-09-post-review-campaign.md.

**Learned:** The `transcription_id` is only known after `transcribeAudio`
returns, which is why the shipped code fell back to msg_id — but the server
sends the update strictly after the reply, so a register-before + replay
design is race-free without dropping the fast-path update. The shared
`--timeout` machinery is a hang detector, not a command-deadline arbitrator:
an inner `wait_for` with the same bound never won the race, so the detailed
error was unreachable in every mode — a CONTRACT promise that no test
asserted.

**Next:** independent whole-diff review of this branch; separate slices for
the remaining review findings (readonly-transcribe gate + ADR-0079, api
allowlist docs, PROPOSALS.md restore, archive-refresh docs, workflow test
pins); owner decides on merge and the 2.0.3 release.
