# ADR-0085: A clone does not invent a bot's keyboard

Date: 2026-08-10
Status: accepted
Closes: #82, #183

## Context

A destination post on the MIAMIVICE PENTHOUSE clone showed none of the bot
buttons its source post carries. The scan behind #82 found why: `reply_markup`
appears nowhere in `src/`, `tests/`, or `docs/CONTRACT.md`. The clone had never
read it, never sent it, and never said it was dropping it — the operator found
the loss by comparing two chats by eye.

What Telegram allows bounds the answer more than the code does. A keyboard
belongs to the bot that attached it: `messages.sendMessage` accepts
`reply_markup` from a bot, not from the user account this tool drives. Albums
are closed even in principle — `InputSingleMedia` has no markup field, so a
`sendMultiMedia` item could not carry one whoever sent it. The single path
that preserves the rows is a native forward, where Telegram re-renders them
itself; a protected (`noforwards`) source, which is exactly the MIAMIVICE
case, is routed to reupload by `transport.decide` and never reaches it.

So the question was never "how do we copy the buttons". It was: what does the
clone owe the operator when it cannot.

## Decision

`reply_markup` is outside clone fidelity. Reupload and snapshot copies arrive
without buttons, and the clone does not reconstruct them in any form.

The loss is reported rather than silent. Every copied message whose source
carried a keyboard, on any transport other than a native forward, contributes
`{"id":…,"buttons":[{"type":…,"text":…},…]}` to `sync.markup_dropped`, and the
plain row gains `markup_dropped_count`.

The stderr warning is emitted at the moment of the first loss, not in the
run's tail. A tail-only report is worth nothing in the case this ADR exists
for: a large protected channel floods mid-run (ADR-0083 exists because that is
routine), the run exits 5 with no result document, and the messages already
copied — keyboards gone, mappings saved, cursor advanced — are never revisited.
That is the silent drop the ticket opened on, reappearing in the most common
non-zero outcome. Reporting on discovery makes the record survive any exit;
one line per run rather than one per message keeps a button-heavy channel from
drowning stderr, and the complete list stays in the result document.

The exit code stays 0. A limit Telegram imposes on every user account is a
property of the protocol, not a failed run — this follows the `reply_flattened`
precedent, not `quote_flattened`, whose exit 2 marks something the clone
*chose* to degrade and an operator can act on.

Buttons are classified by TL class name and label only. That is what makes the
report actionable — an operator seeing `KeyboardButtonUrl` knows the target is
reachable from the source post, and `KeyboardButtonCallback` knows it is not
reachable by anyone but the bot — without the clone pretending it could act on
the distinction.

Already-synced posts are not revisited. `clone refresh` backfills the ADR-0054
body prefix, and nothing here changes that.

## Rejected alternatives

- **Rebuild URL rows as a link footer in the body.** The only class a user
  account could plausibly carry across. Rejected because it trades a visible,
  named loss for an invisible one: the destination body would no longer equal
  the source body, which is the invariant `refresh.eligible_for_backfill`
  reads to decide a message is untouched. Buttons would come back as
  something that is not a button, and every future body comparison would have
  to know about it.
- **Attempt `reply_markup` passthrough and degrade on refusal.** Costs a live
  probe and a new mid-batch error class to buy a capability the API reserves
  for bots. If Telegram ever opens it to user accounts, this ADR is the thing
  to revisit; nothing here is built to prevent that.
- **Prefer a native forward whenever a batch carries a keyboard.** Would trade
  reply-mapping fidelity for button fidelity on unprotected sources, and does
  nothing at all for the protected source that raised the issue.
- **Exit 2 like a quote fallback.** Would paint every sync of a
  button-carrying channel as a partial failure, forever, over a condition no
  operator can clear.
- **Drop silently and only document it.** The acceptance criteria in #82 ask
  for no silent drop, and a fidelity loss the operator can only find by
  eyeballing two chats is the failure mode that opened the ticket.

## The re-forward proof includes the keyboard

Found by this change's own review and closed here (#183), because the report
above has a blind spot without it.

ADR-0050's re-forward proves a repost's original in the linked discussion group
by comparing text, entities, and media. A keyboard was not compared, and
Telegram does not carry every button class into a linked group, so the two
could legitimately differ. Forwarding the proven original then publishes
someone else's buttons under a genuine header — and this ADR could not say so,
since the send degrades to a native forward and the collection reads the
transport that actually ran. The one transport documented as contributing
nothing to `markup_dropped` was also the only one where a keyboard *mismatch*,
as opposed to a total loss, could happen.

So `_same_content` compares the keyboard too — button classes, labels, and
payloads, since two `Open` rows pointing at different URLs are different
content. A mismatch declines the proof exactly as an edited body does: the
batch falls back to the Part A text prefix on the reupload path, where the
keyboard loss is reported normally. This costs a native forward only in the
case where forwarding would have published the wrong buttons.

The comparison key is its own function rather than `fidelity.dropped_buttons`:
one shapes a report for an operator, the other an identity for a comparison,
and the payload fields belong only in the second.

## Contract impact

`docs/CONTRACT.md` §11 (`clone sync`) states the rule, names the transports it
applies to, and documents `sync.markup_dropped`, the new plain column, the
stderr warning and its timing, and the unchanged exit code. The same section's
re-forward paragraph gains the keyboard in the list of keys a proven original
must match. No flag or command surface changes.
