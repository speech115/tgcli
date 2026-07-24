# wacli adoption: `tg store` + offline-first `tg doctor` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give tgcli a way to inspect and reap its own local state (spent
previews carrying message bodies, kept forever at `0644`), and make `tg doctor`
useful when the session is dead or the network is down. Scope and boundaries
are fixed by [ADR-0040](../../decisions/ADR-0040-wacli-review-adoption-scope.md).

**Architecture:** Two independently shippable slices, both **offline** (no
Telegram client, no network). Slice 1 adds a new `tg store` command
(`stats` read-only, `cleanup` deletes spent previews behind a `--confirm`
gate) plus a preview permission fix. Slice 2 splits `tg doctor` into
offline-by-default checks with live checks behind `--connect`. Nothing here
touches Telegram; the entire risk surface is local files.

**Tech Stack:** Python 3.12, argparse, pytest. No new dependencies. No Telethon
calls anywhere in this plan.

## Global Constraints

- **ADR-0040 is the approved scope; nothing beyond it.** In particular:
  - `cleanup` **never** touches `audit.jsonl` (the audit log) or `sessions/`.
    This is the load-bearing safety boundary — do not add a flag that widens it.
  - `cleanup` deletes only **spent previews** (`.used`) and **expired `.json`**
    (past the 5-minute `PREVIEW_TTL`, see `safety.py`). Live `.json` within TTL:
    never. `.pending`: protected (holds the ADR-0028 `random_id`), removed only
    when far past TTL **and** only under an explicit `--include-pending`.
  - **relic** directories (`mirrors/`, `mirror-lab/`, `labs/`, `probes/`) are
    *reported* by `stats`, never auto-deleted.
- **Default posture is report-only (ADR-0005 two-step).** Without `--confirm`,
  `cleanup` deletes nothing and prints what it *would* remove. `--confirm`
  performs the deletion. `store cleanup --confirm` under `--readonly` /
  `TGCLI_READONLY=1` is blocked with exit code 2 (it mutates local state);
  `store stats` is always allowed.
- **Contract discipline (AGENTS.md):** any task that changes CLI flags or JSON
  shapes updates `docs/CONTRACT.md` **in the same commit**. All JSON here is new
  (new commands / additive fields), allowed without a major bump (CONTRACT §3).
- stdout is contract data only (one JSON document under `--json`); progress,
  warnings, and the `--confirm` hint go to stderr (CONTRACT §2).
- **TDD per task:** failing test → minimal code → green → commit. Tests must not
  touch the real state root — drive everything through `TGCLI_STATE_DIR` (see
  `session.state_dir()`) pointed at a `tmp_path`.
- Gates before every commit: `uv run pytest -q`, `ruff check .`,
  `ruff format --check .`, `pyright`. Run at least `pytest -q` per step; the
  full four at each task's commit step.
- New command follows existing layering: `commands/store.py` owns logic +
  `to_rows`; `parser.py` owns argument definitions; `cli.py::_execute` owns
  dispatch. `store` needs **no config and no account** — dispatch it in
  `_execute` alongside the early offline returns (`clone status`), before
  `load_config()`.
- Do **not** touch `src/tgcli/clone/`.
- Each slice ends with a DEVLOG entry and is mergeable on its own. Version bump
  per ADR-0038 (patch) lands with the slice; **tagging/release is the owner's
  action**, not part of this plan.

---

## Slice 1 — `tg store` (Tasks 1–3)

### Task 1: `tg store stats` (read-only inventory)

**Files:**
- Create: `src/tgcli/commands/store.py`
- Modify: `src/tgcli/parser.py` (register `store` + `stats` subcommand)
- Modify: `src/tgcli/cli.py` (`_execute` dispatch, offline)
- Modify: `docs/CONTRACT.md` (§5 `store stats` shape; exit codes note)
- Test: `tests/test_commands_store.py`

**Interfaces:**
- Produces: `scan(root: Path, *, now: datetime | None = None) -> dict` — the
  single inventory function Task 2 reuses. Classifies every file under the
  state root into categories and returns counts + byte sizes. Categories:
  `previews` broken into `live` (`.json`, within TTL), `expired` (`.json`, past
  TTL), `spent` (`.used`), `pending` (`.pending`); `audit_log` (size only);
  `invocations` (size only); `sessions` (count + size); `clones`, `downloads`
  (size); `relics` (list of `{name, bytes}` for `mirrors|mirror-lab|labs|probes`
  that exist).
- Produces: `stats(root: Path) -> dict` and `stats_rows(data) -> list[tuple]`.
- TTL/expiry is read from each preview's stored `expires_at` (see
  `safety.create_preview`), not from mtime, so classification matches the
  safety layer exactly. A `.used`/`.pending` file's age for `--older-than`
  (Task 2) comes from its `expires_at` too; fall back to mtime if unreadable.

- [ ] **Step 1: Write the failing test.** In `tests/test_commands_store.py`,
  set `TGCLI_STATE_DIR` to a `tmp_path`, fabricate: two live `.json` previews
  (future `expires_at`), one expired `.json`, three `.used`, one `.pending`, a
  non-empty `audit.jsonl`, and a `labs/` dir with a file. Assert `scan(root)`
  returns the right per-category counts and that `relics` lists `labs`.
- [ ] **Step 2: Implement `scan` + `stats` + `stats_rows`** in
  `commands/store.py` until the test is green. `stats` is a thin wrapper over
  `scan`. Keep it pure (`root` arg), so tests never depend on the real HOME.
- [ ] **Step 3: Register the parser.** In `parser.py`, add
  `p_store = sub.add_parser("store", help="Inspect and clean local state", parents=[global_flags])`,
  then `store_sub = p_store.add_subparsers(dest="store_command", required=True)`
  and `store_sub.add_parser("stats", parents=[global_flags])`.
- [ ] **Step 4: Dispatch offline.** In `cli.py::_execute`, near the top with the
  other early returns (no `load_config()`, no account), handle
  `args.command == "store" and args.store_command == "stats"` →
  `store_cmd.stats(session.state_dir())`, return `(data, store_cmd.stats_rows(data))`.
  Import `store as store_cmd` and `session`.
- [ ] **Step 5: Document the shape.** Add the `store stats --json` object to
  `docs/CONTRACT.md` §5 and note `store` in §1. A CLI-level test asserting the
  `--json` document parses and carries the category keys.
- [ ] **Step 6: Gates + commit.** Full gate set green; commit
  `feat(store): tg store stats — read-only local-state inventory`.

### Task 2: `tg store cleanup` (reap spent previews, gated)

**Files:**
- Modify: `src/tgcli/commands/store.py`
- Modify: `src/tgcli/parser.py` (`cleanup` subcommand + flags)
- Modify: `src/tgcli/cli.py` (dispatch; `--readonly` gate on `--confirm`)
- Modify: `docs/CONTRACT.md` (`store cleanup` shape; exit code 2 case)
- Test: `tests/test_commands_store.py`

**Interfaces:**
- Produces: `cleanup(root, *, older_than: timedelta | None, include_pending: bool, confirm: bool, now=None) -> dict`.
  Selects deletable artefacts = `.used` + expired `.json` (+ `.pending` older
  than `PREVIEW_TTL` **only if** `include_pending`), optionally filtered to
  artefacts older than `older_than`. Returns
  `{"removed": [...], "would_remove": [...], "bytes": N, "confirmed": bool,
  "kept": {"audit_log": true, "sessions": true, "relics": [...]}}`. When
  `confirm` is false, `removed` is empty and `would_remove` is populated
  (dry-run); when true, it deletes and populates `removed`. **Never** enumerates
  `audit.jsonl`, `sessions/`, live `.json`, or relics into the deletable set —
  assert this explicitly in tests.

- [ ] **Step 1: Failing tests.** (a) dry-run: `cleanup(confirm=False)` reports
  the `.used` + expired `.json`, deletes nothing on disk, leaves `.pending`,
  live `.json`, `audit.jsonl`, `sessions/` untouched. (b) confirmed:
  `cleanup(confirm=True)` deletes exactly those files, returns byte count, and
  still leaves audit/sessions/pending/live in place. (c) `include_pending=True`
  with an old `.pending` removes it; a fresh `.pending` survives. (d)
  `older_than=timedelta(days=1)` keeps a just-spent preview.
- [ ] **Step 2: Implement `cleanup`** reusing `scan`'s classification. Deletion
  is `path.unlink()` guarded to the selected set only.
- [ ] **Step 3: Parser flags.** `p_cleanup = store_sub.add_parser("cleanup", parents=[global_flags])`
  with `--older-than` (duration; accept an int-days or a `Nd/Nh` form — match
  any existing duration parsing in the repo, else int days), `--include-pending`
  (store_true), `--confirm` (store_true).
- [ ] **Step 4: Dispatch + readonly gate.** In `_execute`, handle
  `store cleanup` offline. Before performing a **confirmed** cleanup, call the
  existing readonly guard (`safety.enforce_mutation_allowed(args.readonly)`);
  a blocked call raises `PolicyError` → exit 2 (verify against how `cli.py`
  already maps `PolicyError`). Dry-run (no `--confirm`) is never blocked.
- [ ] **Step 5: stderr hint.** When `--confirm` is absent, print to **stderr**
  a one-line hint: `N artefacts (X KB) would be removed; re-run with --confirm`.
  stdout stays the JSON/rows document only.
- [ ] **Step 6: CONTRACT + commit.** Document `store cleanup` shape and the
  exit-2-under-readonly case in `docs/CONTRACT.md`. Full gates; commit
  `feat(store): tg store cleanup — reap spent previews behind --confirm`.

### Task 3: Preview permission hardening (`0644 → 0600`)

**Files:**
- Modify: `src/tgcli/safety.py` (`create_preview` and the `.pending`/`.used`
  transitions write/chmod `0600`)
- Modify: `src/tgcli/commands/store.py` (`cleanup` chmods surviving preview
  files to `0600`; `stats` reports how many previews are world-readable)
- Test: `tests/test_safety.py`, `tests/test_commands_store.py`

**Interfaces:**
- New previews are created mode `0600` (matching `audit.jsonl`). `store cleanup`
  tightens any pre-existing `0644` survivor it does not delete.

- [ ] **Step 1: Failing test.** Assert a freshly created preview file has mode
  `0600` (mask `stat.S_IMODE`). Assert that after `cleanup`, a surviving live
  `.json` that started `0644` is now `0600`.
- [ ] **Step 2: Implement.** In `safety.create_preview`, `os.chmod` the written
  file to `0o600` (or open with a restrictive mode). Apply the same at the
  `.pending`/`.used` rename sites if they re-create modes. In `store.cleanup`,
  chmod survivors.
- [ ] **Step 3: Gates + commit.** `fix(safety): previews are 0600, not 0644`.
- [ ] **Step 4: Slice close.** DEVLOG entry (what shipped, the audit/sessions
  boundary held). Bump version per ADR-0038 (patch); leave the tag to the owner.

---

## Slice 2 — offline-first `tg doctor` (Task 4)

### Task 4: `tg doctor --connect` (offline by default)

**Files:**
- Modify: `src/tgcli/commands/doctor.py`
- Modify: `src/tgcli/parser.py` (`doctor` gains `--connect`)
- Modify: `src/tgcli/cli.py` (pass `args.connect`; keep the offline path
  client-free)
- Modify: `docs/CONTRACT.md` (`doctor` shape gains the offline checks + note
  `--connect`)
- Test: `tests/test_commands_doctor.py`

**Interfaces:**
- `check_account(account, *, connect: bool) -> dict` — always runs the local
  checks (config presence/validity, `session_file`, `lock_free`,
  `state_writable`, plus new: file permissions on previews/audit, `state_size`);
  runs the live `authorized` probe (opening `session.client`) **only** when
  `connect=True`. When `connect=False`, `authorized` is reported as `null`
  (unknown), not `false` — do not assert a negative you didn't test.
- `run(config, alias=None, *, connect=False) -> dict`.

- [ ] **Step 1: Failing test.** With a fabricated session file and
  `connect=False`, assert `run` completes **without opening any client** (patch
  `session.client` to raise if called) and returns `authorized: null` plus the
  local checks. With a missing/revoked session and `connect=False`, assert it
  still returns a full local report and `ok` reflects only local checks.
- [ ] **Step 2: Implement the split.** Refactor `check_account` so the live
  block is guarded by `connect`. Add the new local checks. Recompute `ok` from
  local checks when offline; include the live check in `ok` only when
  `connect=True`.
- [ ] **Step 3: Parser + dispatch.** Add `--connect` (store_true) to the
  `doctor` parser. In `cli.py`, pass `connect=args.connect` into
  `doctor_cmd.run`. The offline path must not be wrapped in a network timeout
  assumption — a `--connect` run keeps the existing `--timeout`.
- [ ] **Step 4: `to_rows` + CONTRACT.** Ensure `to_rows` renders `authorized:
  null` as e.g. `unknown`, not `fail`. Document the enriched `doctor` shape and
  `--connect` in `docs/CONTRACT.md`.
- [ ] **Step 5: Gates + commit.** `feat(doctor): offline by default, live checks behind --connect`.
- [ ] **Step 6: Slice close.** DEVLOG entry; note the ACCOUNTS-001 tie-in
  (a broken session now gets a real local diagnosis). Bump version per ADR-0038.

---

## Out of scope (recorded, do not build)

- `--events` NDJSON lifecycle stream — deferred to FEED-001 (ADR-0040 §3).
- `tg spec` — deferred; requires overturning ADR-0028 (ADR-0040 §4).
- Any trimming/rotation of `audit.jsonl` — explicitly rejected (ADR-0040
  Consequences). Do not add it "while we're here."
- Auto-deletion of relic directories — `stats` reports them; removal is manual.
