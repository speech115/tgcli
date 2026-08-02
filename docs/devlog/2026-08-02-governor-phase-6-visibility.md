## 2026-08-02 — Governor phase 6: journal, doctor, scheduled wake; retire ADR-0045/0052 (Claude)

**Did:** implemented plan phase 6 of the ADR-0072 governor
(`docs/superpowers/plans/2026-08-02-governor-phase-6-visibility.md`): the
governor's decisions are now visible in the journal and `doctor`, a
scheduled pass waking into a partial cooldown exits 0 with a deferred
report, and the ADR-0045/0052 storage and pre-flight gates are deleted.
Matrix rows G3–G5 (as covered by the seam unit tests), L5–L13 green; the
old `clone/flood.py` module and `test_clone_flood.py` are gone, rewritten
against the ledger.

**Journal carries the governor's stop.** `invocations.jsonl` gains
`retry_after`, `request_type`, `provenance` (`server` |
`account_cooldown` | `resolve_phone_cooldown`), `stop_reason`
(`breadth_budget_exhausted` | `wall_clock_cap` | `cooldown_deferred`),
`governed_sleep_ms` and `request_count`. The seam records the stop via
`pacing.note_stop` when it refuses or arms (probe path included);
`backfill`/`refresh` record their normal stops via `stop_reason`; the
journal rule stands — no message text, no chat refs, no raw API
parameters, a request *type* is not a chat reference. `cli.main` reads
`pacing.last_stop()` and the accumulated counters in its `finally`.

**`doctor` reports cooldowns without connecting.** New checks
`governor_cooldowns` (per-type deadlines from the ledger, keyed on the
session file's own user id read directly — Telethon persists the self-user
as entity id 0 whose access_hash is the user id) and `governor_degraded`.
`doctor` stays the one command usable precisely when everything else
refuses; a cooldown is reportable state, not a failure, so `ok` stays
true and the run exits 0 (G4, L5).

**Scheduled wake under a partial cooldown exits 0.** `archive refresh`
checks the ledger for cooling sync types before dispatching; if any are
hot it defers sync, runs the free local transcription, reports
`stop_reason: "cooldown_deferred"` plus `deferred: ["sync"]`, and exits 0
(L12) — the contract break, previously exit 5 on every wake. The alert
fires once, at arming (the seam's stderr line when a flood arms); the
second and later wakes are silent-but-successful (L13).

**ADR-0045/0052 retired.** `clone/flood.py` deleted whole; `clone/cooldown.py`
keeps only the per-clone `retry_not_before` gate and the governed `mutate`
seam. `cooled_account`/`enforce_account`/`arm_account` and the
`account_flood` preview field are gone from the archive and clone command
surfaces — the governor's per-type gate in the `_call` seam is the
account-wide protection now, and it refuses locally with zero RPCs. Both
ADR headers and index rows read plain superseded; the `MAX_COOLDOWN_S`
clamp moved into `clone/state.py` beside its only remaining use.

**Test churn.** `test_clone_flood.py` rewritten from the JSON record to
the ledger (roundtrip, expiry, clamp, fail-open, latest-arm-wins).
`test_clone_cooldown.py` stayed deleted (phase 5). The
`clone_state.cooldown_deadline()`/`flood.cooldown_deadline()` asserts and
the pre-flight account-gate tests (three clone-sync, one clone-refresh,
one clone-init, one archive) were dropped or repointed at the seam's
local refusal; roster tests now assert the ledger stays empty (roster
RPCs are not governed). Archive refresh unit tests needed a
`sync_data is not None` assert after the deferred early-return.

**Next:** phase 7 — contract, release, guides: the six CONTRACT edits
drafted on #141 reconciled against what shipped, the 2.0.0 version call
for the integrator, operator guides for a cooling account, and flipping
`docs/MAP.md`'s governor rows to `[done]`.

## Review fixes (independent review, same session)

- **M1: journal flood-fields only on flood-related exits.** `cli.main` now
  writes `retry_after`/`request_type`/`provenance` only when the run ended
  non-zero; a flood that was slept out and survived carries none of them.
  Two new tests cover survived-vs-refused.
- **M2: cooldown-wake check happens before `get_me`.** `archive refresh`
  reads the bound user from the store and consults the ledger before any
  RPC, so a `users.GetUsersRequest` cooldown also defers instead of
  exiting 5. `sync_types_cooling` now covers every type sync actually
  sends (changes poll, catch-ups, entity resolution, media).
- **M4: dead `cooldown.arm()` deleted** — nothing armed the per-clone
  `retry_not_before` since `with_cooldown` went away; the gate now only
  reads legacy records.
- **M6: `docs/MAP.md` matches reality** — the deleted `flood.py` row is
  gone and `cooldown.py` is described as the per-clone gate.
