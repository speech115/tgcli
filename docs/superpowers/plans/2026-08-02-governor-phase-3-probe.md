# Governor phase 3 — the self-verifying probe

Part of #145. Prerequisite: phases 0–2 (merged, `0307e1e`). Spec: ADR-0072
decision 1, #139 matrix rows **G6, G7, G8, C2**.

## What this phase is for

A recorded cooldown deadline is a guess about the future. Telegram may lift a
limit early, or extend it. Without a probe the tool either trusts a stale
record and refuses work it could do, or ignores the record and hammers a live
penalty — the exact behaviour that turned a flood into a 21.5-hour ban.

The probe is one request, sent once per confirmed deadline, that asks the
server what is actually true.

**This is not a retry.** If the probe fails, the record is rewritten from the
server's answer and nothing else is sent.

## Files

| File | Change |
|---|---|
| `src/tgcli/governor/probe.py` | new — the decision of whether to probe and what happens after |
| `src/tgcli/governor/gate.py` | call into it from `refuse_if_cooling` |
| `tests/test_governor_probe.py` | new |

The ledger already has everything needed (`probe_spent`, `spend_probe`,
`clear_cooldown`, `arm_cooldown`). Do not add columns.

## Behaviour to implement

1. **When a gated request finds an active cooldown**, decide: refuse, or probe?
   Probe when **both** hold:
   - at least `PROBE_FRACTION` (0.5) of the recorded wait has elapsed. Compute
     from `armed_at` and `deadline` in the cooldowns row — both are stored.
   - `probe_spent` is 0.
2. **Spend before sending.** Call `ledger.spend_probe(...)` first. It returns
   `False` if another process already claimed it — then refuse normally,
   without sending. This ordering is the entire crash-safety story: a process
   killed between the mark and the send must leave the probe spent.
3. **Send the original request** as the probe. Do not synthesise a cheaper
   one — a different request type would answer a different question.
4. **On success:** `clear_cooldown(...)`, return the result. The caller never
   learns a cooldown existed.
5. **On a flood:** `arm_cooldown(...)` with the server's fresh `retry_after`.
   Because `arm_cooldown` resets `probe_spent` to 0, the new deadline earns a
   new probe — that is intended and is what bounds the cost at one request per
   *confirmed* deadline.

## Traps

- **Do not re-probe inside one invocation.** After a failed probe the caller
  must get the `RateLimitError`, not a second attempt.
- **`armed_at` may be missing or garbage** on a hand-edited row. Treat an
  unparseable `armed_at` as "not yet 50%" — refuse rather than probe. Failing
  toward not-sending is the safe direction here.
- **The probe must respect pacing once phase 4 lands.** Leave a comment saying
  so; do not implement pacing here.

## Tests

| Row | Test |
|---|---|
| G6 | probe succeeds → record cleared, command proceeds, exactly one RPC sent, and it is the original request type |
| G7 | probe floods with a *different* `seconds` → new deadline reflects the server value, not the old one |
| G8 | two ledger connections, same record: only one `spend_probe` wins; the loser refuses and sends nothing |
| C2 | mark spent, then simulate a crash before send; next invocation refuses and does not probe again |
| — | before 50% elapsed: refuses, zero RPCs, probe still unspent |
| — | failed probe does not retry within the same call |
| — | unparseable `armed_at` refuses rather than probing |

## Done when

- All seven tests pass and `./scripts/gate.sh` is green.
- No new ledger columns.
- `git grep -n "PROBE_FRACTION"` shows the constant defined once, in
  `probe.py`, with ADR-0072 decision 3's 50% cited in a comment.
