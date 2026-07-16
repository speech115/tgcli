# ADR-0018: Clone accepts service-only destination tails

Status: accepted (2026-07-15).

Clarifies the tail-verification safety model introduced by ADR-0017.

## Context

The first live clone acceptance run created a private destination and then
stopped before copying. Telegram had emitted two destination service messages:
channel creation and the title change performed by `clone init`. Clone assumed
a fresh-channel baseline of exactly one message and classified the second
service row as unexpected user content.

Blindly raising the numeric baseline to two would hide a real message manually
posted after channel creation. The safety boundary must distinguish harmless
Telegram service rows from ordinary destination content.

## Decision

When the destination's latest id exceeds the largest persisted clone mapping
(or the initial id-1 baseline), `clone sync` reads that visible tail:

- a tail containing only Telegram service actions is accepted;
- any ordinary message in the tail blocks before source history, audit, or copy;
- the error reports the number of ordinary unexpected messages, not service
  rows.

This applies both to initial creation/title events and to later service-only
tail events. It does not weaken detection of manually posted content.

## Consequences

- Fresh destinations created by `clone init` can sync on live Telegram.
- Harmless channel metadata events do not permanently block a clone.
- User-authored destination messages still require manual repair before sync.
- The implementation remains within ADR-0017's 400-line command budget.
