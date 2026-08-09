# ADR-0079: `tg transcribe` is a mutation: `--readonly` gate and audit

Date: 2026-08-09
Status: accepted (2026-08-09; owner request: post-review campaign 2026-08-09)

## Context

ADR-0075 added `tg transcribe`, which calls `messages.transcribeAudio` and
waits for the asynchronous result. The independent post-merge review
(2026-08-09) found that the command ran under `--readonly` and wrote no audit
record, while the raw passthrough (`tg api`) classifies the same method as a
write: `transcribeAudio` is not in the ADR-0010 read allowlist and requires
`--write`. The classification is a safety property: the call consumes the
Premium transcription quota, and the transcript becomes visible to other
clients of the chat — a server-side state change a `--readonly` caller must
be able to rule out. ADR-0075 was silent on the classification.

## Decision

`messages.transcribeAudio` is a mutation for tgcli's safety model:

- `--readonly` (or `TGCLI_READONLY=1`) blocks `tg transcribe` in preflight,
  exit 2 (`BLOCKED`), before any session or network work — same gate as
  `mark-read` and the other no-handshake mutations.
- Every `tg transcribe` call writes an audit record with the target
  identifiers only (`chat`, `message_id`) — mirroring `mark-read` and the
  ADR-0010/0011 metadata-only rule. The transcription text is never logged.
- The raw `tg api` classification is unchanged (write path, `--write`
  required): the wrapper and the raw surface now agree.

## Rejected alternatives

- Classifying transcription as read-only: the quota consumption and the
  cross-client visibility are real side effects; `--readonly` would promise
  an immutable invocation it cannot deliver.
- Auditing only on success (`_audit_after`): the attempt itself is the
  state change (quota); the record must exist even when the wait times out.
  Preflight-time audit mirrors `mark-read`.

## Contract impact

- CONTRACT §1 `--readonly` row is unchanged (it already promises to
  hard-block any mutating call; transcribe now honors it).
- CONTRACT §5 Transcribe section gains the mutation note (readonly exit 2,
  audit record with chat + message_id).
- No exit-code taxonomy change (exit 2 already exists).
