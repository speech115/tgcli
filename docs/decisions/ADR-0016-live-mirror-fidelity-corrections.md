# ADR-0016: Live mirror fidelity corrections

Status: accepted (2026-07-15).

> **Note (2026-07-15):** the `tg mirror` feature this ADR concerns is being replaced by `tg clone` — see [ADR-0017](ADR-0017-clone-supersedes-mirror.md). This ADR is retained as history.

Amends ADR-0014 and ADR-0015 after the first persistent production-path demo.

## Context

The first live `mirror sync` exposed two gaps that mocked requests did not
prove. Every newly created broadcast channel begins with a service message, so
the previous fail-closed content gate blocked before reaching user content.
Telegram also accepted `messages.forwardMessages(reply_to=...)` for an
unprotected reply without preserving the destination reply relationship.

The sync JSON needed a service-skip counter. The plain TSV contract already had
five frozen columns, so inserting the counter among them would have been a
breaking change.

## Decision

- New-history scans skip any message whose `action` is not `None`, flush an
  active album first, and report the per-run count as `skipped_service`.
  Service rows are never prepared, audited, dispatched, or used to advance the
  confirmed cursor. Other unsupported content remains fail-closed.
- Any supported batch carrying a mapped reply uses the existing deterministic
  download/reupload transport, even when the source is unprotected. Ordinary
  unprotected batches keep the native `drop_author=True` forward path.
- `skipped_service` is additive in JSON and is appended as the sixth plain TSV
  column. The legacy five columns retain their exact order.

## Consequences

- Fresh broadcast channels can be mirrored from message id 1 without weakening
  the unsupported-content boundary.
- Replies retain their mapped destination parent at the cost of reconstructing
  only reply-bearing batches; native media remains the default elsewhere.
- A trailing service row may be counted again on a later run because it does
  not advance the content cursor. This is observable and safe.
- The persistent showcase must use a fresh open pair after this change; a pair
  produced by the old native-reply behavior cannot become `visual_approved`.
