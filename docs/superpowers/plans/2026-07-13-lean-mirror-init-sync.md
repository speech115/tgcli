# Lean Mirror Init and Text Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task by task.

**Goal:** Ship the first useful mirror vertical slice: preview or initialize a
private broadcast-channel destination, then backfill source text posts without
duplicates after a restart.

**Architecture:** `commands/mirror.py` owns contract-shaped command results and
the small Telethon facade. `mirror/store.py` owns one SQLite database per
resolved source and commits stable random ids before dispatch. `cli.py` applies
the existing safety gates before mirror mutations and uses the normal account
session. No watcher, media renderer, linked comments, or laboratory matrix is
part of this plan.

**Tech Stack:** Python 3.12, Telethon 1.44, stdlib SQLite, argparse, pytest with
mocked Telegram clients.

## Global Constraints

- Syntax is exactly `tg mirror init SOURCE [--commit]` and
  `tg mirror sync SOURCE` for this slice.
- `init` without `--commit` resolves and reports a plan but makes no Telegram
  mutation. `init --commit` creates one private broadcast channel and persists
  authorization. `sync` works only for an authorized initialized mirror.
- Mirror identity is derived from `(account_user_id, canonical_source_peer_id)`;
  message keys are `(source_peer_id, source_message_id)`.
- Persist a stable signed 64-bit Telegram `random_id` before every native copy.
  A retry reuses it. Mapping and high-water advance in one transaction only
  after Telegram confirms the destination message id.
- `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block `init --commit`
  and `sync` before session acquisition. Every authorized mutation writes the
  existing fail-closed audit before network dispatch.
- State stays under `TGCLI_STATE_DIR/mirrors/`; stdout contains only contract
  data. Tests and verification never mutate live Telegram.
- Do not implement media, comments, watch, deletion audits, forum topics,
  memberships, role replication, or generalized lab abstractions in this plan.

---

### Task 1: Durable lean mirror store

**Files:**
- Create: `src/tgcli/mirror/__init__.py`
- Create: `src/tgcli/mirror/store.py`
- Create: `tests/test_mirror_store.py`

**Interfaces:**
- `mirror_id(account_user_id: int, source_peer_id: int) -> str`
- `MirrorStore.create(account_user_id: int, source_peer_id: int, source_title: str) -> MirrorRecord`
- `MirrorStore.authorize(destination_peer_id: int) -> MirrorRecord`
- `MirrorStore.prepare_copy(source_message_id: int, random_id: int | None = None) -> CopyOperation`
- `MirrorStore.confirm_copy(source_message_id: int, destination_message_id: int) -> CopyOperation`
- `MirrorStore.pending_copies() -> list[CopyOperation]`
- `MirrorStore.last_confirmed_message_id() -> int`

- [ ] Write tests proving stable identity, state placement, create/reopen,
  authorization, unique peer-scoped operations, persisted random-id reuse, and
  atomic mapping/high-water confirmation.
- [ ] Verify RED with `uv run pytest tests/test_mirror_store.py -q`; expected
  import failure because `tgcli.mirror.store` does not exist.
- [ ] Implement only the schema and operations needed by those tests. Use SQLite
  constraints, transactions, and signed 64-bit random ids.
- [ ] Verify GREEN with the same focused command.

### Task 2: CLI init preview and commit

**Files:**
- Create: `src/tgcli/commands/mirror.py`
- Create: `tests/test_cli_mirror_init.py`
- Modify: `src/tgcli/cli.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- `preview_init(tg, source: str, account_alias: str) -> dict`
- `commit_init(tg, source: str, account_alias: str) -> dict`
- `to_rows(data: dict) -> list[tuple]`

- [ ] Write CLI tests proving preview makes no Telegram mutation, commit creates
  exactly one private broadcast channel, repeated commit resumes the stored
  destination rather than creating another, an ambiguous create reconciles one
  exact private creator-owned temporary marker match and refuses multiple
  matches, the authorized destination receives the source title, and each kill
  switch blocks before config/session/network work.
- [ ] Verify RED with `uv run pytest tests/test_cli_mirror_init.py -q`; expected
  parser failure because `mirror` is not registered.
- [ ] Register `mirror init SOURCE [--commit]`. Resolve the source and current
  user, create/reopen the store, call Telethon `channels.CreateChannelRequest`
  only when no destination is recorded, then persist destination authorization.
  Before retrying an unconfirmed creation, search an exact private
  creator-owned temporary title marker derived from the mirror id: resume one,
  create on zero, and stop on multiple. After authorization, idempotently change
  the visible title to the source title. Append the existing audit before each
  mutation. Do not create a discussion group.
- [ ] Verify GREEN with the same focused command.

### Task 3: Idempotent text sync

**Files:**
- Modify: `src/tgcli/commands/mirror.py`
- Create: `tests/test_cli_mirror_sync.py`
- Modify: `src/tgcli/cli.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- `sync_text(tg, source: str, account_alias: str) -> dict`

- [ ] Write tests with three chronological text messages proving native
  `messages.ForwardMessagesRequest(drop_author=True)` order, persisted random
  ids before dispatch, confirmed source/destination mappings, restart with zero
  duplicates, and replay of one previously unconfirmed operation with the same
  random id.
- [ ] Verify RED with `uv run pytest tests/test_cli_mirror_sync.py -q`.
- [ ] Register `mirror sync SOURCE`. Require an authorized destination, iterate
  source history oldest-first after the confirmed high-water, prepare each
  operation, append audit, native-copy one item, and confirm it before the next.
  Reject non-text media explicitly for the later media slice rather than silently
  marking it copied.
- [ ] Verify GREEN with the same focused command.

### Task 4: Contract, map, and regression gate

**Files:**
- Modify: `docs/CONTRACT.md`
- Modify: `docs/MAP.md`
- Modify: `docs/DEVLOG.md`
- Modify: `docs/FEATURES.md` only if current behavior changes its truth

- [ ] Document the exact two commands, JSON/plain result shapes, local state,
  safety behavior, idempotency guarantee, and explicit first-slice exclusions.
- [ ] Mark only implemented modules and commands as done in MAP; later mirror
  slices remain planned.
- [ ] Append real RED/GREEN/full-suite commands and results to DEVLOG.
- [ ] Run `uv run pytest tests/test_mirror_store.py tests/test_cli_mirror_init.py
  tests/test_cli_mirror_sync.py -q`, `uv run pytest -q`, `uv run python
  scripts/check-coverage.py`, `git diff --check`, and `tg mirror --help` without
  connecting to Telegram.

## Plan Review

- The plan delivers one end-to-end restart-safe text backfill, not a horizontal
  framework for every future Telegram topology.
- Safety, state placement, stdout behavior, and idempotency have explicit tests.
- Media, comments, forums, watch, and deletion semantics remain named future
  slices and cannot be mistaken for implemented behavior.
