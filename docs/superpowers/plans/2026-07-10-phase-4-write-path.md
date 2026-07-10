# Phase 4: Safe Write Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add audited, preview-to-single-use-commit writes for `tg send` and raw `tg api --write` without allowing a safety-policy bypass.

**Architecture:** `safety.py` owns local preview persistence, the three pre-network mutation gates, and JSONL audit writes. `commands/send.py` is limited to resolving a preview target and replaying its stored target/text; `commands/api.py` classifies hard-denied and confirm-required raw methods. `cli.py` runs policy checks before config, session acquisition, or dispatch.

**Tech Stack:** Python 3.12, Telethon >=1.36, argparse, pytest + pytest-asyncio.

## Global Constraints

- The CLI syntax is `tg send CHAT TEXT --preview` and `tg send --commit PREVIEW_ID`; previews expire after 300 seconds.
- `--readonly`, `TGCLI_READONLY=1`, and `TGCLI_NO_SEND=1` block mutations with `PolicyError` (exit 2) before config/session/network work.
- Previews and audit data are under `TGCLI_STATE_DIR` (or `~/.local/state/tgcli`); stdout remains contract data only.
- Never run a real Telegram mutation in tests or manual verification.
- No daemon, MCP server, or state outside the configured tgcli state directory.

---

### Task 1: Local safety primitives

**Files:**
- Create: `src/tgcli/safety.py`, `tests/test_safety.py`

**Interfaces:** `enforce_mutation_allowed(readonly: bool) -> None`; `create_preview(payload: dict, now: datetime | None = None) -> dict`; `consume_preview(preview_id: str, now: datetime | None = None) -> dict`; `append_audit(action: str, account: str, details: dict) -> None`.

- [x] **Step 1: Write failing tests** for each kill switch, a stored 300-second expiry, single-use consumption, expired consumption, and one JSON object per audit line.
- [x] **Step 2: Verify RED** with `uv run pytest tests/test_safety.py -q`; expected failure: `ModuleNotFoundError: tgcli.safety`.
- [x] **Step 3: Implement the minimum**: exact `=1` environment gates; `p_`-prefixed random preview ids; JSON preview files in `state_dir()/previews`; consume a preview before returning it; append one JSONL object with UTC timestamp to `state_dir()/audit.jsonl`.
- [x] **Step 4: Verify GREEN** with `uv run pytest tests/test_safety.py -q`; expected all safety tests pass.

### Task 2: Preview and commit command module

**Files:**
- Create: `src/tgcli/commands/send.py`, `tests/test_cli_send.py`
- Modify: `tests/conftest.py`

**Interfaces:** `prepare(tg, chat: str, text: str) -> dict` returns the persisted preview envelope; `commit(tg, preview: dict) -> dict` calls `tg.send_message(preview["chat"], preview["text"])` and returns `{"preview_id", "message_id"}`; `to_rows(data) -> list[tuple]` freezes CLI rows.

- [x] **Step 1: Write failing CLI tests** showing preview resolves and records CHAT/TEXT without calling `send_message`, commit sends precisely the stored values, a missing/consumed preview exits 2, and `TGCLI_NO_SEND=1` prevents session acquisition for commit.
- [x] **Step 2: Verify RED** with `uv run pytest tests/test_cli_send.py -q`; expected parser failure because `send` does not exist.
- [x] **Step 3: Implement the minimum**: register `send CHAT TEXT --preview` and `send --commit PREVIEW_ID`; allow only one mode; resolve the preview target through the normal session, persist it with `safety.create_preview`, enforce safety before consuming/connecting on commit, audit the authorised commit attempt, and replay only the stored payload.
- [x] **Step 4: Verify GREEN** with `uv run pytest tests/test_cli_send.py -q`.

### Task 3: Raw API write gate

**Files:**
- Modify: `src/tgcli/commands/api.py`, `src/tgcli/cli.py`, `tests/test_cli_api.py`, `tests/test_cli_api_policy.py`

**Interfaces:** `is_hard_denied(name: str) -> bool`; `requires_confirmation(name: str) -> bool`; raw API writes call the existing `api.call` only after `enforce_mutation_allowed`, denylist, and typed-confirm checks.

- [x] **Step 1: Write failing tests** asserting an allowed `messages.sendMessage --write` reaches the dispatcher and appends audit, every kill switch prevents config/session work, a destructive request needs an exact `--confirm`, and `auth.logOut --write --confirm auth.logOut` is always exit 2 before config/session work.
- [x] **Step 2: Verify RED** with `uv run pytest tests/test_cli_api.py tests/test_cli_api_policy.py -q`; expected Phase-2 `--write is unavailable` failures.
- [x] **Step 3: Implement the minimum**: retain the exact read allowlist for calls without `--write`; accept known raw write methods except ADR-0008's permanent denylist; require exact confirmation for `delete*`, `reset*`, `leave*`, `block*`, `edit*Admin*`, and `edit*Banned*`; run the shared gate and append audit before dispatch.
- [x] **Step 4: Verify GREEN** with the same focused command.

### Task 4: Contract and regression proof

**Files:**
- Modify: `docs/CONTRACT.md`, `docs/MAP.md`, `docs/DEVLOG.md`

- [x] **Step 1: Document** exact send invocation, 300-second preview TTL, JSON envelopes, audit location, write policy, and permanent denylist.
- [x] **Step 2: Mark** `safety.py` and `commands/send.py` as done and `api.py` as Phase-4 complete in MAP.
- [x] **Step 3: Append** actual test commands/results to DEVLOG.
- [x] **Step 4: Run** `uv run pytest -q`; expected all tests pass with no real Telegram calls.

## Plan Review

- Spec coverage: Tasks 1–2 implement preview, single-use replay, safety gates, and send audit; Task 3 implements raw write, confirmation, denylist, and audit; Task 4 maintains contract and project documentation.
- Placeholder scan: no TODO/TBD items remain.
- Type consistency: CLI consumes the `dict` produced by `create_preview`; `send.commit` consumes exactly that mapping; API classification returns booleans used only by CLI policy logic.
