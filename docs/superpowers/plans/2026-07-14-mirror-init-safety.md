# Mirror Init Safety Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `tg mirror init --commit` safe after ambiguous Telegram create
outcomes and persist account-level FloodWait cooldown before any live showcase.

**Architecture:** Extend the existing per-source `MirrorStore` with durable
creation state and retained ownership. Add a small account-scoped cooldown file
under `TGCLI_STATE_DIR/mirrors/cooldowns/`; command orchestration remains in
`commands/mirror.py`. No lab runner, background process, automatic deletion, or
new copy path is introduced.

**Tech Stack:** Python 3.12, Telethon 1.44, stdlib SQLite/JSON/datetime,
argparse, pytest with mocked Telegram clients.

## Global Constraints

- Existing `tg mirror init SOURCE [--commit]` behavior stays compatible.
- Retry syntax is exactly `tg mirror init SOURCE --commit --retry-create
  --confirm MIRROR_ID`; retry flags without `--commit` are exit 2.
- Persist `reconcile_required` before `CreateChannelRequest` dispatch.
- Zero exact-marker matches after a dispatched create never permits an
  automatic second create.
- One wrong-shape exact-title candidate or multiple exact-title candidates
  blocks without create, edit, or delete.
- FloodWait persists an account-level UTC `retry_not_before`; no mirror mutation
  for that account dispatches before it expires.
- Authorization atomically records destination id, `authorized`, creation state
  `authorized`, and retention class `user_owned_retained`.
- SIGINT, SIGTERM, cancellation, timeout, and review expiry never delete an
  authorized destination.
- State remains under `TGCLI_STATE_DIR/mirrors/`; stdout is contract data and
  progress/errors stay on stderr.
- Tests perform no live Telegram mutation and follow RED -> GREEN.

---

### Task 1: Durable creation state and account cooldown

**Files:**
- Modify: `src/tgcli/mirror/store.py`
- Modify: `tests/test_mirror_store.py`

**Interfaces:**
- `MirrorRecord.creation_marker: str | None`
- `MirrorRecord.creation_state: str`
- `MirrorRecord.create_attempted_at: str | None`
- `MirrorRecord.retention_class: str`
- `MirrorStore.mark_create_dispatched(marker: str, attempted_at: datetime) -> MirrorRecord`
- `MirrorStore.mark_create_blocked() -> MirrorRecord`
- `cooldown_deadline(account_user_id: int) -> datetime | None`
- `record_cooldown(account_user_id: int, retry_after: int, now: datetime | None = None) -> datetime`

- [ ] **Step 1: Write failing store tests**

Add tests proving: existing databases migrate without losing authorization or
mappings; dispatch persists marker/state/time; authorize writes
`user_owned_retained` atomically; blocked is durable; cooldown is account-scoped,
owner-only on disk, uses UTC, and never shortens an existing later deadline.

- [ ] **Step 2: Verify RED**

Run `uv run pytest tests/test_mirror_store.py -q`.

Expected: failures because the new fields and functions do not exist.

- [ ] **Step 3: Implement the minimal store migration**

Add columns to new schema and migrate old databases with `PRAGMA table_info`
plus `ALTER TABLE`. Allowed creation states are `planned`,
`reconcile_required`, `blocked`, and `authorized`; allowed retention classes
are `provisional` and `user_owned_retained`. Write cooldown JSON atomically via
a sibling temporary file, `fsync`, `os.replace`, and mode `0o600`. The cooldown
filename must hash the numeric account id rather than expose it.

- [ ] **Step 4: Verify GREEN**

Run the focused command from Step 2. Expected: all store tests pass.

- [ ] **Step 5: Commit exact files**

Run `safe-commit "Harden mirror creation state" src/tgcli/mirror/store.py tests/test_mirror_store.py`.

### Task 2: Ambiguous-create reconciliation and explicit retry

**Files:**
- Modify: `src/tgcli/commands/mirror.py`
- Modify: `src/tgcli/cli.py`
- Modify: `tests/test_cli_mirror_init.py`

**Interfaces:**
- `commit_init(tg, source, account_alias, *, retry_create=False, confirm=None) -> dict`
- `_find_marker_candidates(tg, marker) -> tuple[list[object], list[object]]`

- [ ] **Step 1: Write failing command tests**

Add these tests before implementation:

```text
test_init_persists_reconcile_required_before_create_dispatch
test_init_zero_match_after_ambiguous_create_never_creates_automatically
test_init_retry_create_requires_exact_mirror_id
test_init_exact_marker_with_wrong_shape_blocks_without_create
test_init_multiple_marker_matches_stays_blocked_without_delete
test_init_cancellation_leaves_reconcile_required
test_authorized_init_never_scans_or_creates_again
test_retry_flags_without_commit_are_blocked_before_session
```

Update the old public-marker test: an exact-title public or wrong-shape
candidate must block; it must not be treated as zero matches.

- [ ] **Step 2: Verify RED**

Run `uv run pytest tests/test_cli_mirror_init.py -q`.

Expected: the new safety tests fail on current automatic re-create behavior.

- [ ] **Step 3: Implement reconciliation**

Parse `--retry-create` and `--confirm`. Before the create request call
`mark_create_dispatched(marker, now)`. On `reconcile_required`, inspect all
exact-title dialogs: resume exactly one valid private creator-owned broadcast;
block on wrong shape or multiple candidates; on zero require both retry flag
and exact mirror id. Never delete a candidate. After destination id is stored,
all later runs resolve only that id and may idempotently restore its title.

- [ ] **Step 4: Verify GREEN**

Run the focused init tests. Expected: all pass.

- [ ] **Step 5: Commit exact files**

Run `safe-commit "Reconcile ambiguous mirror creation" src/tgcli/commands/mirror.py src/tgcli/cli.py tests/test_cli_mirror_init.py`.

### Task 3: Persist and enforce FloodWait cooldown

**Files:**
- Modify: `src/tgcli/commands/mirror.py`
- Modify: `tests/test_cli_mirror_init.py`

- [ ] **Step 1: Write failing cooldown tests**

Prove a create FloodWait records the deadline and keeps
`reconcile_required`; the next init mutation exits 5 without marker scan or
network write; an expired cooldown permits only the explicit confirmed retry;
another source under the same account observes the same cooldown.

- [ ] **Step 2: Verify RED**

Run `uv run pytest tests/test_cli_mirror_init.py -q`.

Expected: cooldown tests fail because no durable account gate exists.

- [ ] **Step 3: Implement cooldown enforcement**

After `_resolve` and before any mutation, read the account cooldown and raise
`RateLimitError` with `ceil(deadline-now)` when active. Catch only Telegram
`FloodWaitError` around mutations, persist its seconds, then re-raise so the
existing CLI exit-5 mapping remains canonical. Do not sleep or retry inside the
command.

- [ ] **Step 4: Verify GREEN and regression**

Run:

```text
uv run pytest tests/test_cli_mirror_init.py tests/test_mirror_store.py -q
uv run pytest -q
```

Expected: focused and full suites pass.

- [ ] **Step 5: Commit exact files**

Run `safe-commit "Persist mirror FloodWait cooldown" src/tgcli/commands/mirror.py tests/test_cli_mirror_init.py`.

### Task 4: Contract and closeout

**Files:**
- Modify: `docs/CONTRACT.md`
- Modify: `docs/MAP.md`
- Modify: `docs/DEVLOG.md`

- [ ] **Step 1: Document exact behavior**

Document retry flags, creation states, retained ownership, cooldown path,
exit-5 behavior, and the rule that no signal or review expiry deletes an
authorized destination. MAP must match actual store and command ownership.

- [ ] **Step 2: Append real evidence**

DEVLOG must contain exact RED failures, focused GREEN output, full suite,
coverage result, and `git diff --check` result. Do not claim live evidence.

- [ ] **Step 3: Run final gates**

Run:

```text
uv run pytest tests/test_mirror_store.py tests/test_cli_mirror_init.py tests/test_cli_mirror_sync.py -q
uv run pytest -q
uv run python scripts/check-coverage.py
git diff --check
uv run tg mirror init --help
```

Expected: all tests pass, coverage reports 23 namespaces, diff check is clean,
and help exposes `--retry-create` plus `--confirm`.

- [ ] **Step 4: Commit exact files**

Run `safe-commit "Document mirror init recovery" docs/CONTRACT.md docs/MAP.md docs/DEVLOG.md`.

## Plan Review

- The plan closes duplicate-create and FloodWait risks before any live
  showcase mutation.
- It preserves ADR-0014's lean per-source store and adds only one small
  account-scoped cooldown artifact.
- It does not implement media, comments, forums, watchers, deletion, or a
  second showcase copy engine.
- Native media, albums, and replies remain the immediately following slice.
