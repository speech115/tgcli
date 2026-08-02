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
