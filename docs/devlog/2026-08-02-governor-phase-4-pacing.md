## 2026-08-02 — Governor phase 4: pacing intervals and the breadth budget (Claude)

**Did:** implemented plan phase 4 of the ADR-0072 governor
(`docs/superpowers/plans/2026-08-02-governor-phase-4-pacing.md`). This is
the phase the whole effort exists for: everything before it stops the tool
making a bad situation worse; this is the part that stops the bad situation
happening. Matrix rows P1–P9, C1, C5 green; 13 new tests, no test sleeps in
real time (injected clock + recording sleep).

**What shipped.** `governor/pacing.py` sleeps out the start-to-start floor
*and reserves before dispatch* (the reservation stamps `now + wait`, the
moment the request will actually leave — ADR-0072 decision 3's trap, the one
the #140 canary accidentally violated by sleeping after return). Per-unit
classes adjust the charge: BY_ID pays one 10 s gap per request (600 ids do
not pay two), MEDIA pays 3 s per *file* (offset 0 opens the gap; continuation
chunks owe nothing). The seam calls pacing for every governed non-probe
request, and history reads touch their peer against the rolling
100-peers/24 h breadth budget before dispatch.

**Command-level normal stop.** `archive backfill` now stops *normally* when
the breadth budget is exhausted: exit 0, `stop_reason:
"breadth_budget_exhausted"`, `deferred` count, and a `resume` pointer to the
next unprocessed chat — not exit 5, not an exception. The 791-dialog
backfill that produced the incident is now inherently multi-day; the tool
stops pretending it is one operation that can be retried harder.

**Decided — resolve_phone stays as its own file lock.** The phase plan's
"thin wrapper or deleted" option is blocked by its own trap: `resolve_phone`'s
tests pin the flock+file implementation (concurrency test patches
`Path.exists` on the file path), and the phase contract says those tests pass
unchanged. It remains the pre-flight refuse gate for `contacts.resolvePhone`;
the general mechanism (pacing) covers the same request type through the
seam. `resolve_phone`'s own `RESOLVE_PHONE` class and 3 s interval in the
registry make the two consistent.

**Learned — the first request of a run never sleeps.** `last_reserved` is
`None` until something reserves; pacing treats that as "no pace owed", which
is correct: the interval is a floor between requests, and a run's first
request has no predecessor. Tests that expected `[3.0]` after two history
reads were right; a test that expected the first read to sleep was wrong.

**Noted for phase 5** (deadline as hang detector): paced runs now sleep a
lot of deliberate time, so `--timeout` must stop counting governed sleep
against it — the phase that deletes `_default_timeout`/`_long_running` and
retires `SHORT_WAIT`/`WAIT_BUDGET`/`FloodGate`/`with_cooldown`.

**Next:** phase 5 — `--max-runtime` wall-clock cap, deadline discounting
governed sleep, and deleting the old clone flood machinery.
