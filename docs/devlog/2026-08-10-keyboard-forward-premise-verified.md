## 2026-08-10 — the keyboard premise, checked against Telegram

**Did:** recorded the live verification of ADR-0085's load-bearing premise in
the ADR itself. The owner forwarded a real post carrying eight URL buttons into
a clone destination: `reply_markup` on the copy equals the source's, the text
matches, `fwd_from` points at the original. Nothing in the code changed — this
is evidence catching up with a rule that already shipped in 2.0.6.

**Decided:** write down what the check does *not* cover instead of rounding it
up to "verified". The sample had no callback rows, and a callback is bound to
the message its bot attached it to, so a forward dropping that class is exactly
the plausible case. If it does, ADR-0085 under-reports it: a copy that lost
callback buttons on the forward path contributes nothing to `markup_dropped`,
because the collection exempts native forwards wholesale.

**Learned:** the second review round was right to call the premise unverifiable
by mocked tests, and the fix was not a better test — `test_clone_sync_does_not_
report_buttons_a_native_forward_keeps` can only ever prove that tgcli records
no row, never that Telegram re-renders the keyboard. A claim about the far side
of the API needs a live observation or an explicit note that it has none.
Cheap, once someone asks for it: one forward and two `tg api
channels.getMessages` calls.

**Next:** a live post with a callback row would close the remaining half. Until
then the gap is in the ADR, not in an assumption. Channel titles and message
ids from the check are deliberately not recorded here — the shape of the
evidence is what matters, and real chat content does not belong in the repo.
