# ADR-0090: Failed FloodWait arm fails closed

Date: 2026-08-13
Status: accepted
Amends: [ADR-0072](ADR-0072-account-request-governor.md) (flood arm
write-failure posture)

## Context

ADR-0072 arms a per-type cooldown from the server's `retry_after` after a
`FloodWaitError`. `Ledger.arm_cooldown` returns `False` on SQLite errors
instead of raising. The gate ignored that return value, re-raised the
flood, and left the next process (or the next request on the same
ledger) with no cooldown — deepening the penalty. Thermos T02 / #206.

## Decision

1. `arm_from_flood` retries the durable write a few times. On persistent
   failure it keeps the server deadline in a process-local sticky map
   (`Ledger.remember_cooldown`) and returns `False`.
2. The governed `_call` seam raises `PolicyError` (exit 2) when arm
   returns `False`, instead of re-raising FloodWait alone.
3. `cooldown_deadline` / `active_cooldowns` honour the sticky map so the
   next same-type RPC in this process still refuses locally.

## Rejected alternatives

- Re-raise FloodWait only — fails open for the next request.
- Mark the whole ledger `degraded` on one arm failure — over-broad;
  pacing and other types remain usable.
- Block forever without sticky deadline — loses the server's
  `retry_after` for in-process refusal.

## Contract impact

Exit 2 (`BLOCKED`) when a server flood cannot be persisted as a cooldown.
Exit 5 still covers a successfully armed (or sticky) cooldown refusal.
`docs/CONTRACT.md` §4 notes the arm-persist failure path.
