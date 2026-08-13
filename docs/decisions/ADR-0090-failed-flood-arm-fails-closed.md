# ADR-0090: Failed FloodWait arm fails closed

Date: 2026-08-13
Status: accepted
Amends: [ADR-0072](ADR-0072-account-request-governor.md) (flood arm
write-failure posture)

## Context

ADR-0072 arms a per-type cooldown from the server's `retry_after` after a
`FloodWaitError`. `Ledger.arm_cooldown` returns `False` on SQLite errors
instead of raising. The gate ignored that return value, re-raised the
flood, and left the next request on the same ledger with no cooldown —
deepening the penalty. Thermos T02 / #206.

An earlier draft of this decision replaced the re-raised FloodWait with
`PolicyError`. Independent review of PR #243 showed that ~20 call sites
narrowly catch `FloodWaitError` (and deliberately swallow other
exceptions as soft degradation). Swapping the exception type made arm
persist failure invisible to those sites — silent swallows in
`clone/reupload` and `export`, and terminal-fail instead of requeue in
`jobs/runner`. Complexity reset (ADR-0074): keep FloodWait as the
outbound exception; fail closed via the sticky in-process deadline.

## Decision

1. `arm_from_flood` retries the durable write a few times. On persistent
   failure it keeps the server deadline (and `armed_at`) in a
   process-local sticky map (`Ledger.remember_cooldown`) and returns
   `False`.
2. The governed `_call` seam still re-raises the live `FloodWaitError`
   so existing flood handlers, jobs requeue, and exit-5 journal fields
   keep working. The sticky map is what closes the fail-open hole for
   the rest of this process.
3. `cooldown_deadline` / `cooldown_armed_at` / `active_cooldowns` honour
   the sticky map so the next same-type RPC in this process refuses
   locally (`RateLimitError` / exit 5). Cross-process durability still
   requires a successful SQLite arm — sticky state does not survive a
   new process (tgcli is one-shot).

## Rejected alternatives

- Raise `PolicyError` instead of FloodWait — breaks sibling
  FloodWait-only handlers (silent degrade / wrong jobs routing).
- Mark the whole ledger `degraded` on one arm failure — over-broad.
- Block forever without sticky deadline — loses the server's
  `retry_after` for in-process refusal.

## Contract impact

No new exit code. Exit 5 covers both a successfully armed cooldown and
a sticky in-process refusal after a failed durable arm. Stderr notes
`(cooldown not persisted)` when the sticky path is used.
