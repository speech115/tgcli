# ADR-0048: Clone poll snapshots — transient vote for the per-option breakdown

Date: 2026-07-24
Status: accepted

## Context

Poll snapshots (ADR-0019) render per-option counts from
`media.results.results`, but Telegram reveals the breakdown only to
accounts that voted (or when the poll is closed). A clone made by a
non-voting account therefore shows the correct `total_voters` with
`0% · 0 голосов` per option — observed live on the икона discussion
clone (2026-07-24) and misread as "nobody voted" rather than "data not
available".

The owner explicitly chose the vote-based capture over a
"breakdown unavailable" placeholder, accepting that it briefly mutates
the source poll.

## Decision

1. **Transient vote, narrow conditions.** While snapshotting a poll that
   has `total_voters > 0` and no breakdown, sync casts a vote from the
   cloning account **only when the poll is anonymous, not a quiz, and
   still open**, reads the now-revealed results, then immediately
   retracts the vote (`SendVoteRequest` with empty options).
2. **Own vote is subtracted.** The snapshot reports the source's real
   numbers: the chosen option's count minus one; `total_voters` minus
   one. The rendered totals must match what the poll showed before the
   transient vote.
3. **Excluded cases keep an honest placeholder.** Public (non-anonymous)
   polls — voting would expose the account identity; quizzes — a vote is
   an answer and cannot be retracted; closed polls already include
   results. Where the breakdown stays unavailable, the snapshot replaces
   the misleading zeros with an explicit "распределение по вариантам
   недоступно" line (fixing the observed confusion for every path).
4. **Safety posture.** The vote+retract pair is a mutation of a foreign
   peer: it runs inside sync's existing mutation gates (`--readonly` /
   `TGCLI_NO_SEND` block it — with the snapshot degrading to the
   placeholder, not failing), each cast/retract appends an audit record,
   and a FloodWait surfaces through the standard cooldown path. If the
   retract fails, sync reports it loudly (stderr + JSON marker) so the
   owner can retract manually — the vote must never linger silently.

## Consequences

- Anonymous-poll snapshots gain real percentages; the poll's public
  stats are unchanged after retraction (transiently +1 for the seconds
  between cast and read).
- The account's vote is anonymous by the poll's own nature; no identity
  leak. Public polls and quizzes deliberately stay breakdown-less.
- Two extra RPC per affected poll during sync; polls are rare — flood
  impact negligible.
- A retract failure leaves a real vote standing in the source poll until
  manually removed; the loud marker makes that state visible.
- CONTRACT: snapshot semantics prose updated; JSON sync report gains an
  additive marker for cast/retract outcomes.
