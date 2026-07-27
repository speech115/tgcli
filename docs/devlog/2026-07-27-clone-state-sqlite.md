## 2026-07-27 — Clone state on SQLite/WAL (ADR-0060) (Cursor Grok)

**Did:** Implemented the first of the three FEED-stack releases from
PR #91 / plan `docs/superpowers/plans/2026-07-27-clone-state-sqlite.md`.
`clone/statedb.py` owns the WAL schema (`user_version=1`, UNIQUE map
tables); `clone/state.py` keeps the public seam with dirty-tracked
`save()` (O(1) statements per new mapping). One-time JSON→SQLite import
renames to `.json.imported`; both formats present → PolicyError.
`tg clone export-state` prints the v2 JSON document; `clone status`
reports `schema_version` + `integrity`; `store stats` breaks out
`.db`/WAL/SHM/`.imported`. CONTRACT §11 + MAP + guide updated. Live
acceptance (slice 3) remains owner-gated before version bump/tag.

**Decided:** single SQLite reader only (ADR-0060); no dual-format
runtime; `path_for` now returns `.db`; rollback is `export-state`.

**Learned:** UNIQUE on map `dest` already blocks the duplicate-destination
class at write time; fail-closed `from_dict` still runs on load for
meta-level corruption the schema cannot express. Re-ran
`scripts/bench-clone-state.py` in this environment: JSON 1000 msgs →
0.47 s / 6.4 MB; SQLite 1000 → 0.02 s / ~0 MB, 5000 → 0.11 s / 0.1 MB
(same order-of-magnitude gap as ADR-0060's CI numbers).

**Next:** owner live acceptance on a disposable clone; then integrator
bumps version/CHANGELOG; then session-roles (ADR-0062).
