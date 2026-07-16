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

- `MessageMediaPoll` becomes a static human-readable result card: a Russian
  heading, question, answer labels, Unicode progress bars, per-answer counts and
  rounded percentages, and total voters. It is never presented as an
  interactive poll and contains no implementation metadata or timestamps.
- `MessageMediaStory` becomes the two-line placeholder `Stories недоступна` /
  `Автор: <name>`. The resolved author name/title is a clickable `t.me` link
  when a username is available. Story IDs and implementation metadata are not
  exposed in the destination chat.
- Both fallbacks are confirmed, mapped, audited as `clone-sync-snapshot`, and
  occupy the source position. Later replies can therefore target the fallback.
- Same-source nested replies preserve both mapped direct parent and mapped top
  root. If the direct parent is unavailable, content copies in order without a
  reply relation and reports `reply_flattened`. If only the optional top root is
  unavailable, the mapped direct parent remains linked. Story reply headers,
  which contain no message id, use the same explicit flatten fallback.
- Formatting and fallback classification live in `clone/fidelity.py`, capped at
  100 lines. The existing `commands/clone.py` 400-line and `clone/state.py`
  150-line budgets remain unchanged.

## Consequences

Poll votes are a point-in-time display and do not update after cloning. Voter
identities, native poll interactivity, and source open/closed mode are not
cloned. Story media cannot be reconstructed from an expired reference without
an external copy. The fallback is truthful about those losses while preserving
order, mapping, and reply continuity.
