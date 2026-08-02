# Governor phase 5 — the deadline becomes a hang detector

Part of #145. Prerequisite: phase 4. Spec: ADR-0072 decision 6, #139 matrix
rows **D1–D6**.

This is the phase that deletes code rather than adding it, and it is where
most of the existing test churn lands: ~28 tests across two files are
superseded outright.

## The problem being fixed

`--timeout` today means two incompatible things: "kill this if it hangs" and
"this whole job may not take longer than N". Once the governor sleeps
deliberately, the second meaning kills healthy runs — 25 history windows at
3 s is 75 s of *correct* behaviour, and the 60 s default would shoot it.

The existing answer was a hand-maintained list of exempt commands
(`cli.py:_default_timeout`, `_long_running`, `_deadline`). Every new
long-running command has to remember to join it. That list goes.

## Files

| File | Change |
|---|---|
| `src/tgcli/cli.py` | delete command special-casing in `_default_timeout`, `_long_running`, `_deadline` |
| `src/tgcli/governor/pacing.py` | expose total governed sleep so the deadline can discount it |
| `src/tgcli/clone/flood.py` | delete `SHORT_WAIT`, `WAIT_BUDGET`, `WaitBudget`, `FloodGate` |
| `src/tgcli/clone/cooldown.py` | delete `with_cooldown`; callers go through the governed seam |
| `src/tgcli/parser.py` | add the wall-clock cap flag |
| `tests/test_clone_cooldown.py` | rewritten or deleted (15 tests) |
| `tests/test_clone_media_cache.py` | drop `WaitBudget()` constructions (9 sites) |
| `tests/test_cli_lifecycle.py`, `tests/test_cli_export.py` | exemption-list tests replaced |

## Behaviour to implement

1. **`--timeout` counts only ungoverned time.** Time spent in a deliberate
   governed sleep does not count. Implement by having the deadline ask pacing
   how much it has slept, not by pausing a timer thread.
2. **No command-name special cases anywhere.** After this phase,
   `git grep -n '"export"' src/tgcli/cli.py` and equivalents for `clone`,
   `archive refresh`, `media` should find nothing in deadline logic. The
   replacement is not a shorter list — it is no list.
3. **Long commands take an explicit wall-clock cap.** Proposed flag:
   `--max-runtime <seconds>`. #139 left the name open; pick it here and record
   the choice.
4. **Exhausting the cap is a normal stop:** exit 0, `stop_reason:
   "wall_clock_cap"`, checkpoint advanced, resume pointer in JSON. Not exit 1,
   not exit 5.
5. **Sleep-vs-exit is decided by the remaining cap.** A flood whose
   `retry_after` fits the remaining budget is slept out; one that does not
   exits 5 immediately **without sleeping at all** — not even partially.

## Traps

- **`with_cooldown` carries real concurrency logic**, not just retries: the
  `FloodGate` parks sibling upload workers so they do not each sleep the same
  wait. Deleting it without an equivalent reintroduces a stampede. The
  governor's per-type reservation is cross-process and should cover it — prove
  that with a test before deleting, not after.
- **D5 must assert no sleep happened in the exit-5 case.** Asserting the exit
  code alone passes even if the code slept 14 of 15 seconds first.
- **Do not fold the cap into `--timeout`.** They are different things now:
  one detects a hang, the other bounds a job.

## Tests

| Row | Test |
|---|---|
| D1 | `--timeout 5` with a 3 s governed sleep → no timeout |
| D2 | walk `build_parser()`; assert no command-name branch survives in deadline logic |
| D3 | cap set low → stops mid-run, exit 0, `stop_reason`, checkpoint advanced |
| D4 | that stop is not exit 1 and not exit 5 |
| D5 | `retry_after=8` under a 10 s budget sleeps and succeeds; `retry_after=15` exits 5 with **zero** sleep calls |
| D6 | no-`--timeout` run with 75 s of governed sleep completes instead of being killed at 60 s |
| — | parallel upload workers do not each sleep the same wait (the `FloodGate` behaviour, re-proved) |

## Done when

- `git grep -n "SHORT_WAIT\|WAIT_BUDGET\|WaitBudget\|FloodGate\|with_cooldown"`
  returns nothing outside devlog/ADR history.
- `./scripts/gate.sh` green with the rewritten test files.
- The chosen cap flag name is recorded in this file and in the phase-7 CONTRACT
  list.
