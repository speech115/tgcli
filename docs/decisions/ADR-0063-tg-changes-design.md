# ADR-0063: `tg changes` — daemonless change feed design

Date: 2026-07-26
Status: proposed (FEED-001 re-entry; depends on ADR-0062 for the lock
contract)

## Context

FEED-001 (deferred by ADR-0028) is the one deferred feature that changes
what the tool *is*: without it an agent polls every chat with
`read --after-id`; with it, a workflow can observe → read → act. The
agreed shape — a foreground command that returns events and a cursor,
then exits — has waited on one blocker (session-lock contention, now
addressed by the ADR-0062 job-session proposal) and on the design inputs
recorded in ISSUES.md from the wacli review: deletions must be events,
gaps must be loud, story viewers are out of scope.

## Proposal

`tg changes --cursor C [--wait N] [--session-role job]`, foreground, one
JSON document, exit 0:

```json
{"events": [...], "next_cursor": "…", "gap": null}
```

- **Cursor** is an opaque string encoding Telegram updates state
  (`pts`/`qts`/`date` per `updates.getState` /
  `updates.getDifference`). `tg changes --init` returns
  `{"events": [], "next_cursor": …}` — the explicit baseline call; a
  missing or malformed cursor is exit 2, never a silent full-history
  replay.
- **Event vocabulary v1**, additive forever: `message_new`,
  `message_edit`, `message_delete` (a tombstone with ids only — Telegram
  does not deliver the deleted body; the consumer must never infer a
  deletion from a shorter re-read), `read_marker`. Every event carries
  the marked (`-100…`) peer id per CONTRACT §. Everything else in
  `getDifference` is dropped in v1 — dropped classes are counted in a
  `skipped` object so silence is measurable.
- **Gaps are loud.** When Telegram answers `differenceTooLong` (or the
  state is too old), the result is
  `{"events": [], "gap": {"reason": "…", "recover": {...}}, "next_cursor": <fresh>}`:
  creation history is recoverable via the per-chat `read --after-id`
  hint in `recover`; edit/deletion history in the gap window is lost and
  the document says so. Exit 0 — a gap is data, not an error.
- **`--wait N`** long-polls up to N seconds for the first event, then
  returns. Intended to run on the ADR-0062 job role; on the primary
  session it works but monopolizes the lock — documented, not forbidden
  (single-account users may accept it for a one-off).
- **No state files.** The cursor lives with the caller; tgcli stores
  nothing between invocations (ADR-0002 posture preserved).

## Rejected

- A daemon / `--follow` mode (ADR-0002).
- Deletion-by-absence semantics (wacli lesson, recorded in ISSUES.md).
- Story viewers in scope: `stories.getStoryViewsList` is poll-only and
  already read-allowlisted; a feed cannot deliver it.
- An sqlite events mirror: Telegram already stores history; a mirror is
  a second source of truth (wacli-review conclusion).

## Consequences (if accepted)

- New read command → CONTRACT section, guide page, SKILL routing,
  FEATURES matrix row for the `updates` namespace, boundary tests
  asserting the exact `getDifference` request types, and a release.
- FEED-001 closes; its ISSUES.md entry records the shipped shape.
- The `--events` NDJSON idea from ADR-0040 stays deferred: this ADR's
  single-document contract serves the scripted consumer; a streaming
  variant remains future work if a real workflow demands it.
