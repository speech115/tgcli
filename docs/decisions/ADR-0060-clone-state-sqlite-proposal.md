# ADR-0060: Clone state on SQLite/WAL — measured proposal

Date: 2026-07-26
Status: accepted (2026-07-27, owner decisions recorded below)

## Context

Audit finding bf-19 (2026-07-26, confirmed, deliberately not patched
blind): `clone/state.py::save()` rewrites the entire state file — the
whole `id_map` — after every copied message, through the fsync'd atomic
replace in `tgcli/atomic.py`. Total disk I/O over a clone is therefore
quadratic in message count. Per-message saving itself is correct and must
stay: it is what makes a crash lose at most one message instead of a
window.

`scripts/bench-clone-state.py` measures today's exact write path (real
`CloneState.to_dict()`, real `atomic.replace_text`, fsync included)
against a SQLite/WAL prototype (`journal_mode=WAL`,
`synchronous=NORMAL`, one transaction per message: INSERT into `id_map`
+ cursor update). Measured 2026-07-26 in the CI container:

| backend | msgs   | wall s | MB written | resume load ms |
|---------|-------:|-------:|-----------:|---------------:|
| json    |  1 000 |   2.52 |        6.4 |            0.9 |
| json    |  5 000 |  15.20 |      167.9 |            3.5 |
| sqlite  |  1 000 |   0.06 |       ~0.0 |            0.8 |
| sqlite  |  5 000 |   0.24 |        0.1 |            2.8 |
| sqlite  | 10 000 |   0.51 |        0.1 |            5.5 |
| sqlite  | 50 000 |   2.46 |        0.6 |           27.9 |

The JSON growth is quadratic as predicted (5× messages → 26× bytes).
Extrapolated to a 50 000-message clone, the JSON path writes ~17 GB and
spends ~25 minutes on state I/O alone; SQLite writes under a megabyte and
matches at 50k the wall time JSON needs for 1k. (The SQLite "MB written"
column is final on-disk size; WAL checkpoint churn is not counted, so it
understates somewhat — the gap is three orders of magnitude either way.)

## Proposal

Move **clone state only** (`id_map`, discussion/topic maps, cursors,
cooldowns, pin fields) to a per-clone SQLite database in WAL mode. Beyond
killing the quadratic I/O it buys: transactions across
mapping+cursor (today two fields race a crash window), a `UNIQUE`
duplicate guard, safe concurrent `clone status` reads while a sync runs,
and indexed lookups for `refresh`/`status` filters. Config, previews,
login state, and every other small file stay JSON — this is not a
storage rewrite.

Required by the migration, non-negotiable:

- versioned schema (`PRAGMA user_version`), automatic one-time import of
  the existing JSON state, with the JSON file kept as `.imported` backup;
- a `clone status` check that reports schema version and integrity
  (`PRAGMA integrity_check`) instead of crashing;
- a rollback path (export back to JSON) while both readers exist;
- crash tests between transactions (the campaign's crash-window lens);
- `sqlite3` is stdlib — no new dependency.

## Owner decisions (2026-07-27)

Accepted with the rollback path pinned down:

- **Single reader.** The runtime reads SQLite only. Migration is a
  one-time automatic import on first `load()` of a v2 JSON state; there
  is no transitional dual-format reader ("while both readers exist"
  above is resolved as: they never coexist in one binary).
- **Rollback is an explicit export command** (`tg clone export-state`,
  exact name fixed in the implementation plan): prints the state as the
  v2 JSON document on stdout. Downgrade = export + previous binary. The
  command stays permanently as a diagnostic tool.
- **`.imported` backups are never auto-deleted.** They are user data;
  `tg store stats` reports them, removal is a manual decision
  (consistent with the `tg store cleanup` posture, ADR-0040).
- Ships as its own plan, branch, and release (first of the
  SQLite → session-roles → `tg changes` sequence, 2026-07-27 session);
  the release is tagged only after live acceptance on a disposable
  clone.

## Rejected

- **Save-per-batch instead of per-message** (cheap fix): reduces I/O ~50×
  but widens the crash window to a whole batch — re-copying a window into
  a real channel is exactly the duplicate-post class clone exists to
  avoid. Rejected on safety, not effort.
- **A general storage layer / ORM:** one table and a meta table need no
  abstraction (YAGNI, AGENTS.md).
- **Doing nothing:** correct until the first multi-thousand-message
  clone; the owner's real clones are already near 1k, where JSON burns
  2.5 s and 6 MB per thousand messages and grows quadratically from
  there.

## Consequences

- `clone/state.py` keeps its public seam (`load`/`save`/`from_dict`
  equivalents) so command code does not change shape; the file format
  behind it changes, an explicit CONTRACT §11 note and a release.
- The state directory gains `.db`/`.db-wal`/`.db-shm` files; `tg store`
  and the architecture write_text ban need their rows updated.
- Implementation is its own scoped plan with live acceptance on a
  disposable clone before any real one.
