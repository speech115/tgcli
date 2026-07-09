# Phase 2A: Read Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add dedicated read-only `search`, `count`, `latest`, `info`, and `message` commands to tgcli.

**Architecture:** Keep Telegram conversion in focused command modules. `cli.py` owns parsing and dispatch only; command modules return a JSON dict plus TSV rows. Every lookup resolves its chat once through `TelegramClient.get_entity`; a missing entity or missing message becomes `NotFoundError`.

**Tech Stack:** Python 3.12, Telethon >=1.36, argparse, pytest + pytest-asyncio.

## Global Constraints

- stdout carries contract data only; diagnostics and errors use stderr.
- `--json` emits one JSON document; `--plain` emits frozen TSV columns; default is readable text.
- `TGCLI_CONFIG` and `TGCLI_STATE_DIR` keep unit tests out of user state.
- `FloodWaitError` maps to `RateLimitError` (exit 5); missing chats/messages map to exit 4.
- All commands are read-only; no command writes Telegram or local audit state.

---

### Task 1: Shared message projection and exact-message lookup

**Files:**
- Modify: `src/tgcli/commands/read.py`
- Create: `tests/test_commands_read.py`

**Interfaces:** Produce `message_to_dict(message) -> dict` and `fetch_message(tg, chat: str, message_id: int) -> dict`; the latter returns `{"dialog": {"id", "name"}, "message": dict}` or raises `NotFoundError`.

- [ ] Write failing tests for a projected message and `None` from `get_messages`.
- [ ] Run `.venv/bin/pytest tests/test_commands_read.py -q`; expect import/attribute failure.
- [ ] Implement `message_to_dict` once and make `fetch_messages` reuse it; call `await tg.get_messages(entity, ids=message_id)` and raise `NotFoundError(f"message not found: {message_id}")` for `None`.
- [ ] Run the focused test; expect `2 passed`.
- [ ] Commit `src/tgcli/commands/read.py tests/test_commands_read.py` as `Add reusable message projection and exact lookup`.

### Task 2: Search, latest, and message CLI commands

**Files:**
- Create: `src/tgcli/commands/search.py`, `tests/test_cli_search.py`, `tests/test_cli_message.py`
- Modify: `src/tgcli/cli.py`, `tests/conftest.py`

**Interfaces:** `search.fetch_search(tg, chat, query, limit=20) -> dict` returns `{"dialog": ..., "query": query, "messages": [...]}`; `search.fetch_latest(tg, chat) -> dict` returns the same dialog plus one `message`; `search.to_rows(data)` freezes `(id, date, from_name, text)`.

- [ ] Add failing CLI tests: `tg search @chan needle --json` returns only fake messages yielded by `iter_messages(entity, search="needle", limit=20)`; `tg latest @chan --json` returns the first message; `tg message @chan 42 --json` returns id 42 and a missing id exits 4.
- [ ] Run `.venv/bin/pytest tests/test_cli_search.py tests/test_cli_message.py -q`; expect argparse failures for all three commands.
- [ ] Implement the module and register parsers exactly as `search CHAT QUERY [--limit N]`, `latest CHAT`, `message CHAT MESSAGE_ID`; route them through the existing session context.
- [ ] Run the focused tests, then `.venv/bin/pytest -q`; expect all tests green.
- [ ] Commit the five exact paths as `Add search latest and message read commands`.

### Task 3: Dialog metadata and message count

**Files:**
- Create: `src/tgcli/commands/info.py`, `tests/test_cli_info.py`, `tests/test_cli_count.py`
- Modify: `src/tgcli/cli.py`, `tests/conftest.py`

**Interfaces:** `info.fetch_info(tg, chat) -> dict` returns `{"id", "name", "kind", "username"}`; `info.fetch_count(tg, chat) -> dict` returns `{"dialog": ..., "count": int}`. Count uses `await tg.get_messages(entity, limit=0)` and its `.total` field.

- [ ] Add failing tests for a channel-shaped fake entity, a user-shaped entity, and a fake total of 73; assert count TSV is exactly `73\n`.
- [ ] Run `.venv/bin/pytest tests/test_cli_info.py tests/test_cli_count.py -q`; expect parser failures.
- [ ] Implement projections without exposing access hashes or raw Telethon objects; register `info CHAT` and `count CHAT`.
- [ ] Run focused tests and the full suite; expect no regressions.
- [ ] Commit exact paths as `Add dialog info and count commands`.

### Task 4: Contract, live checks, and documentation

**Files:**
- Modify: `tests/live/test_live_smoke.py`, `docs/CONTRACT.md`, `docs/MAP.md`, `docs/DEVLOG.md`

- [ ] Add gated live tests for `info me`, `latest me`, `count me`, and a bounded `search me` that only asserts valid JSON shape, never a fixed message count.
- [ ] Run `.venv/bin/pytest -q`; run the live tests only with `TGCLI_LIVE_SMOKE=1` and an authorized `main` config.
- [ ] Document the JSON/TSV shapes and mark the new command modules done in MAP; append the actual commands and outputs to DEVLOG.
- [ ] Commit exact paths as `Document Phase 2 read parity checks`.
