## 2026-08-02 — Governor implementation, phases 0–1 (Claude)

**Did:** opened the ADR-0072 implementation slice (#145) with a seven-phase
plan (`docs/superpowers/plans/2026-08-02-account-request-governor.md`) and
landed the first two phases: the request-type registry with its seam pins, and
the persisted ledger. 38 new tests. No behaviour change yet — nothing consults
the ledger, and no client is wrapped.

**Decided — the cooldown key is derived, the paced class is looked up.** These
are two different keys and conflating them would have built a hole into the
gate. `request_key()` is computed from the request's own type
(`"<namespace>.<TypeName>"`), so every request tgcli can ever issue gets its
own cooldown record — including types nobody enumerated and whatever an
operator sends through `tg api`. `classify()` is a table lookup that decides
only the *pacing interval*, because ADR-0072 decision 3 states its defaults per
class. An unlisted type therefore has no pre-emptive pacing but full cooldown
coverage. The alternative — an exhaustive type table — would be stale the first
time Telethon adds a constructor, and staleness there would silently disable
the gate rather than merely the pace.

**Decided — namespaced keys.** `messages.GetMessagesRequest` and
`channels.GetMessagesRequest` are distinct methods to Telegram and now to us;
same for the two `ReadHistoryRequest`s. A bare type name would have merged
them.

**Decided — failing open means a working empty ledger, not an exception.**
First cut of `Ledger.open()` let a corrupt file raise, and the test asserted
the raise. That is failing *closed*: matrix row L3 wants the command to proceed
as if nothing were armed. It now falls back to a private in-memory ledger —
reads answer "nothing is cooling", writes go nowhere — and sets `degraded` so
`doctor` can report that the governor is not persisting. Writes return `False`
rather than raising for the same reason: losing a pacing reservation degrades
the pace, and that is not worth aborting a command the user asked for.

**Learned — the seam pin has to check parameter *order*, not just presence.**
Telethon 1.44's `_call(self, sender, request, ordered=False,
flood_sleep_threshold=None)` is forwarded positionally, so a future version
that swapped the first two parameters would still pass a presence check and
then govern the wrong object. `verify_seam()` pins the leading pair and lets
trailing keyword-only extras drift. It runs at client construction rather than
first RPC, because a governor that fails open on its first request is
indistinguishable from no governor — which is exactly the shape of the incident
this ADR answers.

**Also recorded** in the ledger, deliberately rather than inherited: a
`busy_timeout` pragma (#137 found none set anywhere today), a read-time clamp
on absurd deadlines mirroring `clone/flood.py`'s `MAX_COOLDOWN_S`, a
reservation clamp for a stepped-back clock (the failure `resolve_phone.py`
already guards), and per-peer durable breadth rows so a killed process does not
hand back budget it really spent.

**Next:** phase 2 wraps `_call` and sets `flood_sleep_threshold=0` on all
clients — the first phase that changes behaviour. The ADR-0045/0052 mechanisms
stay live beside the governor until phase 6 retires them, so no phase leaves
the account less protected than it found it.
