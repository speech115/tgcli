## 2026-08-10 — a clone does not invent a bot's keyboard

**Did:** landed ADR-0085 (#82). `fidelity.dropped_buttons` classifies a
message's `reply_markup` rows by TL class and label; `clone sync` records one
row per copied message that lost a keyboard on any transport but a native
forward, reports them in `sync.markup_dropped`, adds `markup_dropped_count` to
the plain row, and prints a single stderr warning naming the count and the
reason. Exit code unchanged. CONTRACT §11 now states the rule instead of the
clone dropping the field in silence.

**Decided:** buttons are outside clone fidelity, and nothing is rebuilt. The
grilling with the owner settled three things: no reconstruction (not even URL
rows as a text footer), warning plus counter with exit 0 rather than the
`quote_flattened` exit-2 shape, and no repair pass over already-synced posts —
the MIAMIVICE post 55 stays as it is. A URL-row footer was the tempting
option; it fails because the destination body would stop equalling the source
body, which is exactly the invariant `refresh.eligible_for_backfill` reads.

**Learned:** the ticket's investigation checklist asked for
`tg --json message` to dump the markup, but `message_to_dict` has no such
field — the raw dump is `tg api channels.getMessages`, already allowlisted.
Also: `InputSingleMedia` has no markup field at all, so an album could not
carry a keyboard even sent by a bot. That closes the "could we ever?" question
for albums independently of the user-account limit, and is why the ADR states
the rule for all rebuilding transports rather than probing per message shape.
No live probe was run: under a report-only contract the outcome is identical
for every button class, so classification was not on the critical path.

**Next:** if Telegram ever opens `reply_markup` to user accounts, ADR-0085 is
the decision to revisit — the reporting shape is already there to degrade
from. Repair of already-cloned posts stays out of band until the owner asks.
