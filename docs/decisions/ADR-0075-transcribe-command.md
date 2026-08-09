# ADR-0075: `tg transcribe` — server-side voice-message transcription

Date: 2026-08-09
Status: accepted (2026-08-09; owner request #97)

## Context

Transcribing a voice message today means downloading the `.ogg` via
`tg media download` and running a local engine — two steps, and the account
may already hold a Premium subscription whose `messages.transcribeAudio` does
it server-side. The raw method is reachable through `tg api --write`, but its
result arrives asynchronously (`updateTranscribedAudio` with `pending: true`)
and a caller must wait for the matching update — the raw passthrough cannot
express that wait, so the capability is effectively unavailable to agents.
Issue #97 asks for a wrapped command. Its other two asks are already met or
deliberately out of scope: the `voice_played` field (inversion of
`media_unread`) has shipped in the message JSON (`read.py`), and auto-
transcribing every voice note inside `tg read` is rejected here because it
changes a released read command, makes reads expensive, and burns the
server-side transcription quota on messages nobody asked about.

## Decision

Add a top-level `tg transcribe <chat> <message_id>` command that resolves the
peer and message, refuses anything that is not a voice message, calls
`messages.transcribeAudio`, and waits for the asynchronous result:

- Register a temporary `events.Raw(UpdateTranscribedAudio)` handler **before**
  invoking the request (race-free), matching on `transcription_id` and
  `pending == False`.
- The wait is bounded by the root `--timeout` flag; `tg transcribe` defaults
  it to 120 s (CONTRACT §1). Expiry raises the existing `TIMEOUT` error and
  reports the `transcription_id` so the caller can re-run or poll via `tg api`.
- `PREMIUM_ACCOUNT_REQUIRED` maps to `PolicyError` (exit 2, `BLOCKED`) with a
  plain-language message — no new exit-code taxonomy.
- JSON output (`--json`) is stable and additive only:

  ```json
  {
    "dialog": {"id": 123, "name": "Channel name"},
    "message_id": 42,
    "transcription": {"text": "...", "transcription_id": 987, "pending": false}
  }
  ```

- A message that is not a voice note raises `NotFoundError` (exit 4) with the
  message id, matching the "downloadable media not found" semantics of
  `media download`.

Scope boundaries: no change to `tg read` output beyond the already-shipped
`voice_played`; no local transcription; the distinct `archive transcribe`
(local Parakeet queue) is untouched. The request has no pre-emptive pacing
class (rare, per-message); the governor's per-type cooldown still covers its
FLOOD_WAIT.

## Rejected alternatives

- Auto-transcription inside `tg read` (issue #97 part 2): changes a released
  command, makes every read expensive, and consumes the server quota
  implicitly; if wanted later it must be an explicit opt-in flag behind its
  own ADR.
- Documenting the `tg api --write` path only: leaves the async wait and the
  Premium-refusal handling to every caller — the exact gap the issue names.
- A new `PREMIUM_REQUIRED` exit code: the taxonomy already has a blocked
  class; a distinct code would ripple through CONTRACT §4 for no consumer.
- Local fallback transcription when the account lacks Premium: silently
  couples server and local engines and doubles the failure surface.

## Contract impact

- New command `tg transcribe <chat> <message_id>`; `--timeout` default 120 s
  for it (CONTRACT §1).
- New §5 JSON shape for `transcribe`; §4 unchanged (reuses `NOT_FOUND`,
  `BLOCKED`, `TIMEOUT`).
- README, SKILL.md, and a new guide page document the command; FEATURES.md
  `messages` row already reads `wrapped` and needs no change.
