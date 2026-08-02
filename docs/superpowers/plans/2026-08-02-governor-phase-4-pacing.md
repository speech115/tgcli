# Governor phase 4 — pacing intervals and the breadth budget

Part of #145. Prerequisite: phase 3. Spec: ADR-0072 decision 3, #139 matrix
rows **P1–P9, C1, C5**.

**This is the phase the whole effort exists for.** Everything before it stops
the tool making a bad situation worse. This is the part that stops the bad
situation happening. Nothing shipped so far prevents a flood.

## Files

| File | Change |
|---|---|
| `src/tgcli/governor/pacing.py` | new — sleep-before-dispatch and the breadth budget |
| `src/tgcli/governor/gate.py` | reserve and sleep before `await original(...)` |
| `src/tgcli/resolve_phone.py` | becomes a thin wrapper over the general mechanism, or is deleted with its callers repointed |
| `src/tgcli/governor/registry.py` | no change — intervals already live there |
| `tests/test_governor_pacing.py` | new |
| `tests/test_resolve_phone*.py` | must keep passing unchanged (see traps) |

## Behaviour to implement

### Pacing

Before every governed dispatch, for a request whose class has an interval:

1. Read `last_reserved(account, key)`; run `clamp_reservation` first so a
   stepped-back clock cannot wedge the type.
2. `wait = interval - (now - last_reserved)`. If `wait > 0`, sleep it.
3. **Reserve `now + wait` — the moment the request will actually leave —
   before dispatching.**

**Step 3 is the one thing most likely to be got wrong, and ADR-0072 decision 3
spells it out because of that.** The interval is *start-to-start*. If you
stamp the reservation after the request returns, a 1.8 s request turns a 3 s
interval into 4.8 s — the canary in #140 accidentally measured exactly this,
which is why the ADR now says it out loud. The `_call` seam pulls you the
wrong way: flood handling already happens after the call, so stamping there
feels natural. Do not.

Where a request's own latency already exceeds the interval, no sleep is owed.
The interval is a floor on spacing, not an added delay.

### Per-unit classes

- `BY_ID`: charge one interval per `BY_ID_BATCH` (300) ids in the request, not
  per request. A 600-id call owes one gap of 10 s, not two.
- `MEDIA`: charge per file, not per chunk. A large file is many
  `upload.GetFileRequest` calls; they must not each pay 3 s.

### Breadth budget

- A **history read** touching a peer calls `touch_peer` before dispatch.
- Before starting work on a *new* peer, check `breadth_remaining`. At zero,
  stop — **normally**.
- A normal stop is: **exit 0**, checkpoint intact, and JSON carrying
  `stop_reason: "breadth_budget_exhausted"` plus a resume pointer. Not exit 5,
  not an exception.

## Traps

- **`resolve_phone` must not regress.** Its existing tests are the contract.
  Generalise the mechanism, keep the behaviour. If the tests need editing,
  something is wrong.
- **Tests must not really sleep.** Inject the clock and the sleep function.
  A test suite that sleeps 3 s per paced request is unusable.
- **Do not pace inside the probe's own send** in a way that makes a probe wait
  out its own cooldown. Probe first, pace normally afterwards.
- **P9 is the regression test that matters most.** It reconstructs the
  incident: `--limit 1000`, and it must assert the governor's own interval
  store is what fires — not Telethon's `wait_time`. A test that passes because
  Telethon happened to throttle proves nothing.

## Tests

| Row | Test |
|---|---|
| P1 | two history reads < 3 s apart → second sleeps to the floor |
| P1b | **start-to-start**: with a 2 s request latency and a 3 s interval, gap between *dispatches* is 3 s, not 5 s |
| P2 | 600 ids → one 10 s gap, chunked at 300 |
| P3 | 3 files → 3 s between files, not between chunks |
| P4 | two dialog pages → 3 s |
| P5 | two sends back to back → no sleep |
| P6 | `resolve` twice within 3 s behaves exactly as today |
| P7 | 95 peers already in window → run touches at most 5 more |
| P8 | budget hits 100 → exit 0, `stop_reason`, checkpoint present |
| P9 | `--limit 1000` paces, and the governor's store is what fires |
| C1 | two ledger connections share one pacing clock |
| C5 | killed after 10 of 20 peers → remaining budget is 90 |

## Done when

- All of the above pass; `./scripts/gate.sh` green.
- No test sleeps in real time.
- `resolve_phone`'s existing tests pass untouched.
- A reviewer can point at the line that reserves *before* dispatch.
