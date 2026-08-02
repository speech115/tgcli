## 2026-08-02 — Governor phase 5: the deadline becomes a hang detector (Claude)

**Did:** implemented plan phase 5 of the ADR-0072 governor
(`docs/superpowers/plans/2026-08-02-governor-phase-5-deadline.md`): the
invocation deadline now counts only *ungoverned* time, the per-command
exemption lists are gone, long commands take an explicit `--max-runtime`
wall-clock cap, and the ADR-0052 foreground flood machinery
(`SHORT_WAIT`/`WAIT_BUDGET`/`WaitBudget`/`FloodGate`/`with_cooldown`) is
deleted. Matrix rows D1–D6 green; 4 new tests, ~25 superseded tests
rewritten or deleted.

**The deadline asks pacing how much it slept.** `_armed`'s SIGALRM handler
and `_run_with_deadline`'s wait loop both re-read `pacing.total_governed_sleep()`
on every wake and grant that much wall time back — a run pacing itself out
of a flood is not killed for doing the right thing (D1, D6). The old
`_default_timeout`/`_long_running`/`_deadline` exemption lists are deleted
entirely: clone init/sync/refresh, archive refresh, export, media and
`changes --wait` all take the uniform 60 s default now, and no command-name
branch survives in deadline logic (D2). `accounts login` keeps its CONTRACT
§10 QR default of 120 s — that is operator time, not work.

**`--max-runtime` is a normal stop, not an error.** The flag feeds
`pacing.reset_runtime(cap=...)`; `archive backfill` checks the remaining cap
before each new chat and stops with exit 0, `stop_reason:
"wall_clock_cap"`, `deferred` and a `resume` pointer (D3/D4). A flood whose
`retry_after` fits the remaining cap is slept out via `pacing.sleep_flood`;
one that does not exits 5 immediately with zero sleep calls (D5). Governed
sleep counts against the cap — it is wall clock, unlike `--timeout`.

**Deleted the ADR-0052 machinery.** `WaitBudget`, `FloodGate`,
`SHORT_WAIT` and `WAIT_BUDGET` are gone from `clone/flood.py`;
`with_cooldown` is gone from `clone/cooldown.py` and every caller goes
through the governed `_call` seam. The `FloodGate`'s "siblings park while
one sleeps the wait" behaviour is re-proved on the governor: a flood arms a
per-type cooldown, and every sibling request of that type refuses locally
with zero RPCs instead of sleeping the same wait again
(`test_siblings_do_not_issue_rpcs_while_a_flood_is_armed`). The archive
backfill foreground retry (`seconds <= SHORT_WAIT and budget.try_spend`)
became `pacing.sleep_flood(seconds)`.

**Test churn.** `test_clone_cooldown.py` (15 tests) deleted wholesale — it
tested only deleted machinery. `test_clone_media_cache.py` dropped the
`WaitBudget()` constructions. The clone-sync/init/refresh flood tests that
pinned `clone_state.cooldown_deadline()` / `flood.cooldown_deadline()`
asserts now pin the exit-5 path only — arming moved to the governor's
ledger and is covered by the governor unit tests (the CLI fakes do not
install the seam). The exemption-list lifecycle tests
(`clone init has no default timeout`, `export has no default timeout`) were
replaced by uniform-deadline tests.

**Note for phase 6:** the old `cooled_account`/`enforce_account` gate still
runs in front of clone/archive commands reading the ADR-0045 JSON record —
that record is no longer armed by anything and is retired in phase 6, where
the journal gains the new fields and the archive refresh exit-5→exit-0
contract break lands.

**Next:** phase 6 — journal fields (`retry_after`, request type, provenance,
stop reason, governed sleep, request count), `doctor` cooldown check,
scheduled wake exit 0 with deferred report, and retiring the ADR-0045/0052
storage (`clone/flood.py` account record, `cooled_account`).

## Review fixes (independent review, same session)

- **C1 (critical): active governed sleep is now discounted write-ahead.**
  `_note_sleep` moved *before* `await sleep` in both `pace_before_dispatch`
  and `sleep_flood`, so a flood-sleep longer than the remaining `--timeout`
  is not killed at the wall deadline. D1/D6 rewritten to advance real wall
  time past a shorter deadline; both now fail on the old post-sleep
  accounting.
- **C3 (critical): `changes --wait` keeps no implicit deadline.** The
  long-poll budget is its own deadline (CONTRACT §12), so `_default_timeout`
  returns None for `changes --wait`; `_run_with_deadline(None)` runs without
  a timer. Regression test drives `--wait 120` past the old 60 s default.
- **D2 test corrected**: `changes --wait` is no longer asserted at 60 s —
  it is exempt, exactly like `accounts login`.
- **`--max-runtime` validation**: non-positive values now exit 2
  (PolicyError), not a silent no-op.

### Decisions recorded after review (M3, m1)

- **Clone commands exit 5 on any flood — no foreground retry.** `clone
  sync/init/refresh` do not use `sleep_flood`; a flood arms the governor's
  per-type cooldown and the run exits 5 immediately, resumed by re-running.
  This deliberately replaces ADR-0052's ≤60 s foreground retry for clone:
  the governor's pacing makes floods rare, and an interactive `clone sync`
  has no schedule to wait out a wait for. The plan's "slept out if it fits
  the remaining cap" applies to `archive backfill` only.
- **`sleep_flood` without `--max-runtime` never sleeps.** With no wall-clock
  cap there is no budget to judge a wait against, so every flood exits 5.
  This is a deliberate, conservative choice (the old `SHORT_WAIT`/`WAIT_BUDGET`
  foreground retry is gone); an operator who wants waits slept out must pass
  `--max-runtime`. The integrator's CONTRACT edit should state this.

### Third review pass — owner blockers (deadline scope)

- **Blocker 1 fixed: long-running commands keep no implicit deadline.**
  `_default_timeout` now returns None for the CONTRACT §1 set (media,
  exports, `clone init|sync|refresh`, `archive refresh`) in addition to
  login/changes; the 60 s default is for short commands only. The deadline
  stays a hang detector (governed sleep exempt); long runs are bounded by
  explicit `--timeout`/`--max-runtime`. The phase plan's "one number for
  every command" was wrong — it would kill a 10k-message export mid-run —
  and the owner flagged it; the CONTRACT §1 list is now data
  (`_long_running_command`), not deadline logic.

### Fourth review pass — independent sub-agent sweep, all fixed

- **Fail-open on an unreadable ledger (major 1).** `pace_before_dispatch`
  no longer spins forever when the reservation loses AND the ledger cannot
  be read back (`newer is None`): it dispatches now — the pace degrades,
  the command does not hang. A long-running command without a default
  deadline would otherwise hang forever in degraded mode. Pinned by
  `test_a_lost_reservation_with_an_unreadable_ledger_dispatches` (broken
  connection underneath a real ledger).
- **CONTRACT §11 de-staled (major 2 + minors).** `clone init` keeps no
  implicit deadline like the rest of §1's list (the old "keeps the global
  60-second default" line contradicted the code and §1); `--max-runtime`
  is promised only for `clone sync` (refresh passes read no wall clock);
  stale ADR-0045 "account-scoped cooldown" citations now point at
  ADR-0072; "upload parts paced" is corrected (only download chunks pay a
  pre-emptive interval); the "whole pass" `--max-runtime` claim in §13 is
  the one check before dispatch; the legacy `retry_not_before` write
  claim is now "read back for old clones, never written anew"; the doctor
  JSON sample matches the real key order. Guides (`archive-refresh`,
  `clone`, `doctor`) updated to match — notably archive-refresh no longer
  promises a default 60-second `--timeout`.
- **Known protocol limit recorded in ADR-0072**: two processes reading
  "nothing reserved" before either claims can each dispatch sub-millisecond
  apart; bounded by the scheduler, self-healing, deliberately without row
  locks.
- **Test hygiene**: D6's docstring no longer overclaims write-ahead
  (D1 alone catches that regression); two 1 ms real-time tests moved to
  the monkeypatched `wall_clock_remaining` pattern; the G5 test now walks
  the whole `tg api` path (`api.call` → `client(...)` → seam) instead of a
  synthetic `_call`; the journal test uses `sleep_flood` instead of the
  private `_note_sleep`; `set_cooldown` is marked legacy/test-only;
  `session_user_id` URI-encodes the session path (aliases with spaces or
  `?`/`#` no longer mis-parse).
