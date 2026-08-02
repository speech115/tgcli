## 2026-08-02 — Governor implementation, phases 0–2 (Claude)

**Did:** opened the ADR-0072 implementation slice (#145) with an eight-phase
plan (`docs/superpowers/plans/2026-08-02-account-request-governor.md`) and
landed the first three: the request-type registry with its seam pins, the
persisted ledger, and the governed `_call` wrapper. 52 new tests.

**This one changes behaviour.** Every client now carries
`flood_sleep_threshold=0`, not just the mutation-safe ones, and every request
passes the gate: a cooling request type refuses locally with exit 5 and zero
RPCs, and a flood arms the cooldown for exactly the type that drew it. The
ADR-0045/0052 mechanisms stay live beside the governor until phase 6 retires
them, so the account is strictly better protected than before, never worse.

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

**Learned — instance-patching `_call` was not enough, and the docstring said
otherwise.** ADR-0072 decision 2 rejects the public `__call__` as the seam
precisely because media downloads and CDN redirects bypass it. Phase 2's first
cut patched `_call` on the client instance and claimed that covered both.
Review pushed on it and the claim was false for the branch that matters:
Telethon's `_get_cdn_client` builds a **brand-new client** with
`self.__class__(...)` (`telegrambaseclient.py`), which never passes through
`session._make_client`. It would have fetched CDN file bytes with no wrapper
*and* Telethon's default sleeper still absorbing floods — the exact failure the
ADR chose this seam to avoid, reintroduced inside the fix for it.

The factory is now wrapped too, and the child inherits the parent's ledger and
account id. That second part is not incidental: a fresh client has no
`_self_id`, so without inheritance it would land in the deliberately-ungated
authorization window and skip the gate entirely. The plan had permitted this
row (matrix S6) to ship as a documented gap; it did not need to.

**Also learned — which flood families are ours.** `FloodPremiumWaitError` now
arms alongside `FloodWaitError`. `SlowModeWaitError` deliberately does not:
Telethon marks it chat-specific, and ADR-0072 decision 1 excludes the peer from
the key on purpose, so arming from a per-chat limit would refuse every other
chat for a restriction that never applied to them.

**Next:** phase 3 adds the self-verifying probe, then phase 4 the pacing
intervals and breadth budget — the first point at which the start-to-start
reservation decision 3 now spells out actually gets exercised.
