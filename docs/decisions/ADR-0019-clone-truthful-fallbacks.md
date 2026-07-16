# ADR-0019: Clone uses truthful fallbacks for polls and Stories

Status: accepted (2026-07-16).

Amends ADR-0017 after a user-channel repair exposed two unsupported polls, two
expired Story references, a nested reply root, and a reply whose parent Story
had been skipped.

## Context

Skipping unsupported rows preserved progress but broke visible one-for-one
position and could remove the parent needed by a later reply. Recreating a poll
with the same question is not faithful if Telegram resets its votes.

A controlled live canary forwarded source polls with
`messages.forwardMessages(drop_author=True)`. Telegram created new poll IDs and
reset results from 80 and 89 voters to zero. The two source Story messages
returned only peer/Story IDs; their embedded Story media was no longer
available. A completed clone therefore could not recover the original bytes.

## Decision

- `MessageMediaPoll` becomes a static text snapshot captured at sync time:
  question, answer labels, per-answer counts and rounded percentages, total
  voters, single/multiple-choice mode, open/closed state, and UTC timestamp.
  It is explicitly labelled a snapshot and never presented as an interactive
  poll.
- `MessageMediaStory` becomes a text placeholder containing the resolved
  author name/title, optional username, and Story ID. It never claims to contain
  unavailable Story media.
- Both fallbacks are confirmed, mapped, audited as `clone-sync-snapshot`, and
  occupy the source position. Later replies can therefore target the fallback.
- Same-source nested replies preserve both mapped direct parent and mapped top
  root. If a reply parent or root is still unavailable, the content is copied
  in order without a reply relation instead of blocking the channel forever.
- Formatting and fallback classification live in `clone/fidelity.py`, capped at
  100 lines. The existing `commands/clone.py` 400-line and `clone/state.py`
  150-line budgets remain unchanged.

## Consequences

Poll votes are a point-in-time display and do not update after cloning. Voter
identities and native poll interactivity are not cloned. Story media cannot be
reconstructed from an expired reference without an external copy. The fallback
is truthful about those losses while preserving order, mapping, and reply
continuity.

