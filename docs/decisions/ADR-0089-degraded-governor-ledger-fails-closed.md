# ADR-0089: Degraded governor ledger fails closed

Date: 2026-08-13
Status: accepted (owner request via thermos audit P0 / GitHub #205)
Amends: [ADR-0072](ADR-0072-account-request-governor.md) ledger open
fallback — authenticated governed traffic no longer dispatches when the
ledger cannot persist protection. The non-raising `Ledger.open()` path and
`doctor --connect` exemption remain.

## Context

ADR-0072 L3 opened a corrupt or missing `governor.db` as an in-memory
ledger marked `degraded=True`, then **failed open**: reads answered
“nothing is cooling” and authenticated RPCs proceeded unprotected.
`tg doctor` reported `governor_degraded: true` but kept `ok: true`, so
operators could miss the loss of the account-wide hedge. The 2026-08-13
thermos audit (Wave 3) ranked this as P0: concurrent archive/jobs traffic
on a broken ledger recreates the unpaced bulk pattern ADR-0072 was meant
to stop.

Failing closed on `Ledger.open()` itself would also break `doctor`’s
ability to diagnose the problem. The seam that must refuse is the
governed `_call` path after an account id exists.

## Decision

1. **`Ledger.open()` still returns a degraded in-memory store** when the
   on-disk file cannot be opened. `doctor` keeps reading it.
2. **`gate.refuse_if_degraded`** raises `PolicyError` (exit 2 / `BLOCKED`)
   before any authenticated governed RPC when `ledger.degraded` is true.
   Pre-auth traffic (`_self_id` is `None`) and `doctor --connect`
   (`govern=False`) stay ungated.
3. **`tg doctor` treats `governor_degraded: true` as unhealthy** —
   per-account and top-level `ok: false`. Cooldowns alone still do not
   fail the check.

Operators repair by fixing or removing `governor.db` under
`TGCLI_STATE_DIR` (message text in the `PolicyError`).

## Rejected alternatives

- **Fail closed inside `Ledger.open()`:** wedges `doctor` and every
  offline reader; no diagnosis path without hand-editing docs.
- **Stderr warning only, keep dispatching:** does not stop FloodWait
  deepening; the audit’s P0 was unprotected RPC volume.
- **Map to exit 5 / `FLOOD_WAIT`:** misleading — there is no server
  `retry_after`. Exit 2 / `BLOCKED` matches other local policy gates.
- **Environment override to restore fail-open:** reintroduces the silent
  hole; repair the ledger instead.

## Contract impact

CONTRACT §5.1: `governor_degraded: true` sets `ok: false`; governed
commands refuse with exit 2 when the ledger is unavailable. Patch release
on merge (integrator bumps version / CHANGELOG). Ships with reproducing
gate + doctor tests.
