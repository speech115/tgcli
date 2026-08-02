## 2026-08-02 — Governor phase 3: the self-verifying probe (Claude)

**Did:** implemented plan phase 3 of the ADR-0072 governor
(`docs/superpowers/plans/2026-08-02-governor-phase-3-probe.md`): a cooling
request type that has waited through half its recorded deadline now earns one
probe request, spent write-ahead, that asks the server what is actually true.
Seven new tests, matrix rows G6, G7, G8, C2 green.

**How it works.** `probe.probe_due()` opens a 50%-elapsed window computed from
the row's own `armed_at` + `deadline` (the ledger gained a `cooldown_armed_at`
reader — the deadline alone cannot say how much of the wait is over).
`probe.claim_if_due()` spends *before* the request leaves, so a crash between
claim and send leaves the probe spent (C2), and the atomic
`UPDATE ... WHERE probe_spent = 0` means two racing connections settle on one
winner (G8). A successful probe settles (clears) the record and the caller
never learns a cooldown existed (G6); a flood re-arms from the server's fresh
`retry_after`, which resets `probe_spent` so the new deadline earns a new
probe — cost bounded at one request per *confirmed* deadline (G7).

**Decided — the probe is not a retry, and the same-call trap is structural.**
After a failed probe the record re-armed from the server, so a second request
in the same invocation finds the 50% window closed and refuses with
`RateLimitError` instead of probing again — no extra bookkeeping, the window
itself is the guard.

**Decided — unparseable `armed_at` fails toward refusal.** A hand-edited row
reads as "not yet 50%": sending into a live penalty is worse than waiting out
a stale record.

**Noted for phase 4** (in `probe.py`'s docstring): the probe goes out through
the same `_call` seam, so the start-to-start pacing reservation lands on it
automatically; nothing to add here.

**Next:** phase 4 — pacing intervals and the 100-peers/24h breadth budget
(`docs/superpowers/plans/2026-08-02-governor-phase-4-pacing.md`), the first
point where the start-to-start reservation decision 3 mandates actually gets
exercised. This phase still does not *prevent* a flood; it stops the tool
from guessing into one.
