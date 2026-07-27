# Clone state on SQLite/WAL — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-clone JSON state file with a per-clone SQLite
database in WAL mode, killing the measured quadratic write path (ADR-0060:
168 MB written per 5k messages vs 0.1 MB) while keeping the per-message
durability guarantee. Scope and decisions are fixed by
[ADR-0060](../../decisions/ADR-0060-clone-state-sqlite-proposal.md)
(accepted 2026-07-27): **clone state only**, single reader, one-time JSON
import with `.imported` backup, explicit export command as the rollback
path.

**Architecture:** `src/tgcli/clone/state.py` keeps its public seam —
`CloneState`, `load()`, `save()`, `supersede()`, `clone_id()`, `path_for()`
signatures survive so no command module changes shape. A new
`src/tgcli/clone/statedb.py` owns the SQLite backend (schema, transactions,
import, export). `save()` stops rewriting the world: `CloneState` tracks
mutations since the last flush and `save()` commits exactly those in one
transaction. This is the first release of the SQLite → session-roles →
`tg changes` sequence (2026-07-27 owner session).

**Tech Stack:** Python 3.12, stdlib `sqlite3` (no new dependency), pytest,
Hypothesis (already in the dev deps). No Telethon calls anywhere in this
plan — the entire risk surface is local files.

## Global Constraints

- **ADR-0060 is the approved scope; nothing beyond it.** Config, previews,
  login state, audit, and every other small file stay JSON. No ORM, no
  general storage layer (YAGNI): one schema module is the whole abstraction.
- **Per-message durability is non-negotiable.** A crash between two
  `save()` calls loses at most one message's mapping — never a window. The
  mapping row and the cursor move in the **same transaction** (today they
  race a crash window; the transaction closes it — this is a hardening, and
  a test must pin it).
- **Fail-closed validation survives the port.** Everything
  `CloneState.from_dict` rejects today (invalid id maps, inconsistent
  forum/discussion/pin state, bad cooldowns) must be rejected when loading
  from SQLite too — same `PolicyError` texts where they exist. A corrupted
  database (failed `PRAGMA integrity_check`, unknown `user_version`) is a
  `PolicyError` naming the file, never a traceback (the 1.2.16 campaign
  class).
- **Migration is one-way and loud.** `load()` finding `<id>.json` without
  `<id>.db` imports inside a single transaction, renames the JSON to
  `<id>.json.imported`, and reports the import on stderr. `.imported` files
  are never auto-deleted (`tg store stats` reports them; ADR-0060).
  A clone with **both** `.db` and `.json` present is a `PolicyError`
  (ambiguous state — manual resolution), not a silent pick.
- **Contract discipline (AGENTS.md):** CONTRACT §11 gains the storage-format
  note and the new command in the same commits that land them. Integrator
  owns version/CHANGELOG (ADR-0058); this branch never touches them.
- stdout is contract data only; import/progress notes go to stderr.
- Run `./scripts/gate.sh` before every commit.

## Slice 1 — SQLite backend behind the existing seam

- [ ] **Schema + open/close (TDD).** `clone/statedb.py`: `connect(path)`
  applies `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`,
  `foreign_keys=ON`, `user_version=1`. Tables: `meta` (one row: every
  scalar `CloneState` field incl. `version`, cursors, cooldown, pin
  fields), `id_map(source INTEGER PRIMARY KEY, dest INTEGER UNIQUE)`,
  `discussion_id_map(...)`, `topic_map(...)`, `avatar_photo_ids(...)` —
  the `UNIQUE` constraints encode the duplicate-destination guard that
  `_valid_id_map` enforces today. Unknown `user_version` → `PolicyError`.
- [ ] **Dirty-tracking `save()` (TDD).** `CloneState` records mutations
  (new mappings, scalar changes) since the last flush; `save()` writes only
  those in one transaction. Assert with a write-counter test: recording one
  mapping then saving issues O(1) statements, not O(n).
- [ ] **`load()` from SQLite (TDD)** with the full fail-closed validation
  matrix ported: parametrize the existing `from_dict` rejection tests to run
  against a database seeded with each invalid shape.
- [ ] **`supersede()`** archives `.db` + WAL/SHM sidecars with the same
  timestamp-rename contract (never deletes); extend the existing tests.
- [ ] **Crash tests.** Kill (process-abort fixture) between transactions:
  reopened DB passes `integrity_check` and shows either the pre- or
  post-transaction state, never a torn one. Add a Hypothesis property test:
  any sequence of record/save/reload round-trips equals the in-memory state.

## Slice 2 — migration, status, export, store

- [ ] **One-time import (TDD).** v2 JSON → SQLite in one transaction; rename
  to `.json.imported`; stderr note. Tests: import happens exactly once;
  `.imported` content byte-equals the original; both-files-present →
  `PolicyError`; corrupted JSON still fails with today's message.
- [ ] **`tg clone export-state` (TDD).** New subcommand: prints the state as
  the v2 JSON document on stdout (exactly `to_dict()` shape), exit 0;
  unknown clone → exit 2. Readonly-safe (pure read). This is the permanent
  rollback/diagnostic path — document it as such.
- [ ] **`tg clone status`** reports `schema_version` and
  `integrity: "ok" | <error>` per clone instead of crashing on a bad DB.
  Adversarial tests: truncated DB, wrong `user_version`, missing file.
- [ ] **`tg store stats`** counts `.db`/`.db-wal`/`.db-shm` and `.imported`
  under `clones` (see `_dir_bytes` usage around
  `src/tgcli/commands/store.py:218`); `.imported` reported, never cleaned.
- [ ] **Architecture rows.** `scripts/check-architecture.py`: add
  `clone/statedb.py` to the write-path policy appropriately (SQLite writes
  its own files; the atomic-replace ban list at line ~109 keeps
  `clone/state.py` only if it still writes JSON — otherwise update the row
  and its `tests/test_check_architecture.py` mirror). Update ceilings only
  within the ADR-0058 grace band; report to integrator otherwise.
- [ ] **Docs:** CONTRACT §11 storage note + `export-state` section; MAP.md
  row for `statedb.py`; re-run `scripts/bench-clone-state.py` and record the
  measured before/after in the devlog.

## Slice 3 — live acceptance (release gate)

Owner present; disposable clone on a test account; results recorded in the
session devlog. **No tag before this slice is green.**

- [ ] Fresh `clone init` + `sync` of a small disposable channel → `.db`
  created, no `.json`.
- [ ] Interrupt a `sync` mid-window (Ctrl-C) → resume continues without
  duplicate posts; `status` shows consistent cursor/id_map.
- [ ] Point the build at a real pre-existing JSON clone state (copy it to a
  scratch `TGCLI_STATE_DIR` first): one-time import fires, `.imported`
  appears, `status` and `refresh` work, source JSON byte-preserved.
- [ ] `export-state` output re-validates through the old `from_dict`
  (rollback proof).
- [ ] Report gate + CI output; integrator merges, bumps version/CHANGELOG
  (ADR-0058), tags after acceptance.
