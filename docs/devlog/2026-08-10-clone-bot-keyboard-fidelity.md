## 2026-08-10 — a clone does not invent a bot's keyboard

**Did:** landed ADR-0085 (#82). `fidelity.dropped_buttons` classifies a
message's `reply_markup` rows by TL class and label; `clone sync` records one
row per copied message that lost a keyboard on any transport but a native
forward, reports them in `sync.markup_dropped`, adds `markup_dropped_count` to
the plain row, and prints one stderr warning per affected message as it is
copied. Exit code unchanged. CONTRACT §11 now states the rule instead of the
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

**Review fix:** the independent review caught the report living only in the
tail of `sync_text`. A flood mid-run — the routine outcome on a large
protected channel, and the reason ADR-0083 exists — exits 5 with no result
document, while the messages already copied keep their saved mappings and are
never revisited. The warning would have vanished for exactly the copies that
lost their keyboards: the silent drop #82 opened on, rebuilt in a different
place. Now the first loss is announced when it is discovered, with a
reproducing test that floods after the first send and asserts stderr. Same
round: `docs/guide/clone.md` still documented 16 plain columns and a
`markup_dropped`-free JSON example (the docs gate does not check guide bodies,
so it stayed green), the MAP line for `fidelity.py` still said "media
capability classification", and the new behaviour had no test on the snapshot
transport or the comments leg — the comments case now also pins that an
autoforward anchor cannot double-count its post's keyboard.

**Next:** if Telegram ever opens `reply_markup` to user accounts, ADR-0085 is
the decision to revisit — the reporting shape is already there to degrade
from. Repair of already-cloned posts stays out of band until the owner asks.
**Review fix, round two:** the round-one fix was itself half-right. "Announce
the first loss" still reported 1 of N on the exit it was written for — a run
that floods after 250 losses printed one line naming one id, and the bound
that was supposed to keep stderr calm ("the complete list stays in the result
document") does not hold on the path where there is no result document. The
ADR said both things two paragraphs apart and neither of us noticed. Now every
loss prints as it happens. Second: `_markup_key` had shipped as a five-field
allowlist, and review produced the counterexample in one pass — a
`KeyboardButtonCopy` row with a different `copy_text` (a different wallet
address) compares equal. Rather than add a sixth field, both that key and
`_entities_key` became `to_dict()` comparisons; `_media_key` deliberately did
not, because media carries chat-scoped handles that differ between copies of
the same file. Worth carrying forward: a field allowlist over a growing TL
layer is a bug with a schedule.

The sibling gap the review found is closed too, on the owner's call (#183):
`_same_content` in the ADR-0050 re-forward path now compares the keyboard —
classes, labels, and payloads — so a mismatch declines the proven original and
the batch falls back to the Part A prefix, where the loss reports normally.
Worth noting the shape: the fix costs a native forward *only* in the case
where forwarding would have published someone else's buttons. The comparison
key stayed separate from `fidelity.dropped_buttons` on purpose — a report for
an operator and an identity for a comparison want different fields, and
merging them would have put button payloads into operator-facing output.
