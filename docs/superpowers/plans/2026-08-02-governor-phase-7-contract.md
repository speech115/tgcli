# Governor phase 7 — contract, release, guides

Part of #145. Prerequisite: phase 6. Spec: #141's drafted CONTRACT edits.

This phase writes down what phases 2–6 actually did. It comes last on purpose:
`docs/CONTRACT.md` is the public promise of the CLI, and #141's own rule ships
contract text in the same commit as the behaviour. Writing it earlier would
have described a governor that did not exist.

## Files

| File | Change |
|---|---|
| `docs/CONTRACT.md` | six edits, below |
| `CHANGELOG.md` | one entry |
| `pyproject.toml`, `src/tgcli/__init__.py`, `docs/CONTRACT.md` version line | bump together |
| `docs/guide/*.md` | operator guidance for a cooling account |
| `SKILL.md` | same, short form |
| `docs/devlog/` | session entry |

## The six CONTRACT edits

Drafted verbatim in
<https://github.com/speech115/tgcli/issues/141#issuecomment-5151175045>.
**Do not copy them blindly** — phase 6 fixed the literal journal field names
that were placeholders there. Reconcile against what shipped.

1. §1 `--timeout` row — deadline is a hang detector; governed sleep is exempt;
   long commands carry an explicit wall-clock cap whose exhaustion is exit 0.
2. §4 exit code 5 row — reserved for "nothing in this invocation can proceed";
   a scheduled pass under a partial cooldown exits 0 instead.
3. §9 invocation journal — the six new fields, with the names phase 6 chose.
4. §5.1 `doctor` — prose plus the `--json` sample gains the cooldown check.
5. Clone-sync and archive-backfill prose — replace `SHORT_WAIT`/`WAIT_BUDGET`
   descriptions with pacing intervals, the wall-clock cap, and the windowed
   breadth budget. Recite ADR-0072 where the mechanism changed; ADR-0045/0052
   citations stay only where the substance did not.
6. Version line.

## Release

**This is a breaking change.** A scheduled `archive refresh` that used to exit
5 under a cooldown now exits 0 with a deferred report — for the identical
trigger. Anything polling exit 5 as "needs to wait" silently changes meaning.

ADR-0038 rule 2 makes a contract break major, so `2.0.0` is the recommendation.
ADR-0058 rule 1 gives the version call to the integrator at merge, not to the
implementing branch. Do not pick it unilaterally; flag it in the PR.

Version drift between `pyproject.toml` and `src/tgcli/__init__.py` was already
reconciled to 1.2.25, so the CHANGELOG compare link builds from `v1.2.25`.

## Guides

The operator-facing change is that "the account is cooling" is no longer one
state. Cover:

- the cooldown is **per request type** — some commands still work while others
  refuse;
- `tg doctor` now says so directly, instead of the operator discovering it by
  running something that fails;
- a scheduled pass under a cooldown is a **success** that deferred work, not a
  failure;
- exhausting the breadth budget or the wall-clock cap is a normal stop with a
  resume pointer, not an error to retry harder.

Pages were not inventoried; `docs/guide/` has 26 task pages. The candidates are
whichever cover `archive refresh` scheduling and flood handling.

## Traps

- **The docs gate counts guide pages.** Adding one means updating `docs/MAP.md`
  in the same commit.
- **Do not let the CHANGELOG describe intentions.** It describes what shipped.
  If a phase dropped something, the entry says so.
- **`docs/MAP.md`'s `governor/` rows say `[wip]`** — flip to `[done]` here.

## Done when

- `./scripts/gate.sh` green, including the release-bookkeeping checks.
- #145 closable, and #146 (the job-model map) unblocked.
- The ADR's "Evidence from #140" section still honestly says the numbers carry
  one live demonstration and both assumptions remain open. Shipping the code
  does not make them true.
