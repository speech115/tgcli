# ADR-0017: Clone supersedes mirror

Status: accepted (2026-07-15).

Supersedes the implementation architecture of ADR-0014. Carries forward the
destination-retention policy of ADR-0015 and the live fidelity rules of
ADR-0016, applied to the new feature unchanged.

## Context

Mirror reached live-proven parity (text, media, albums, replies, protected
sources, idempotent reruns) but its implementation is disproportionate:
1,793 production lines in two files — the size of the entire rest of the core
(1,729) — plus 3,650 lines of mocked tests, for a two-subcommand surface.
Root causes: parallel infrastructure instead of core reuse (its own
confirmation flow instead of `safety.py` preview→commit; a per-account
mutation lock redundant with `session.py`'s invocation-wide flock; its own
cooldown store), ~500 lines of zero-duplicate crash guarantees inappropriate
for an owner-operated CLI, ~250 lines of SQLite migrations for its own
development history, and no read-only `status` window.

## Decision

Rewrite the feature as `tg clone` per
[the clone design spec](../superpowers/specs/2026-07-15-clone-design.md):

- One JSON state file per clone; no SQLite, no migrations — an unknown state
  version fails closed with a clear error.
- Channel creation goes through the shared `safety.py` preview→commit gate;
  all custom confirmation flags are removed.
- No clone-owned locks; `session.py`'s per-account flock already serializes
  tg processes.
- Tail-verification crash model: state saved after each confirmed batch; a
  hard crash duplicates at most one batch, detected on the next run. This
  replaces the random_id / prepare-confirm / exact-envelope machinery.
- Both transports (native forward, protected download/reupload) and the
  ADR-0016 transport-selection rule transplant verbatim from mirror.
- Unsupported message kinds are skipped and reported (`skipped_unsupported`),
  never fatal — a deliberate deviation from mirror's fail-closed gate, which
  made any channel with a poll mid-history permanently unsyncable.
- `tg clone status` ships from day one.
- Complexity budgets are part of the contract of the work: `commands/clone.py`
  ≤ 400 lines, `clone/state.py` ≤ 150, mocked tests ≤ ~1,200.

## Execution and repo hygiene

- Work happens on `feature/clone` with a PR to `main`; Claude implements,
  the plan is self-contained so Codex can take over any task.
- Mirror code remained in the tree, frozen, as the transplant donor until clone
  passed live acceptance (re-run of the Stage-2 demo pairs with visual approval
  and an idempotent rerun); Task 9 then deleted it.
- Lab branches are preserved as tags `archive/mirror-r1-controlled-lab` and
  `archive/mirror-aggregate-checkpoints`; all mirror branches are deleted.
  Superseded mirror plans/specs carry a banner pointing here.

## Consequences

- The feature surface users script against is `tg clone ...`; `tg mirror ...`
  disappeared after live acceptance and CONTRACT.md §11 now documents clone.
- Existing mirror SQLite state is not migrated; demo pairs are re-inited as
  clones and demo destination channels are retained (ADR-0015).
- A hard crash can leave one duplicated batch in the destination; tail
  verification reports it and the owner resolves it manually. Accepted
  trade-off for removing ~500 lines of guarantee machinery.
