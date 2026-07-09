# Phase 2B: Read-Only Raw API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tg api` for fail-closed read-only TL calls while blocking every write path with exit 2.

**Architecture:** A dedicated `api.py` resolves a dotted Telethon function class, recursively builds only JSON-safe TL inputs, invokes the request through the established session, and serializes `to_dict()`. The CLI safety gate runs before parameter construction or any network call.

**Tech Stack:** Python 3.12, Telethon >=1.36, argparse, pytest + pytest-asyncio.

## Global Constraints

- ADR-0008 is binding: only method verbs `get*`, `search*`, `check*`, and `resolve*` are read-allowlisted.
- `--write` always raises `PolicyError("raw API writes are unavailable until phase 4")` in Phase 2.
- Never expose API hash, auth keys, access hashes, or arbitrary Python imports.
- `tg api` JSON is `{"method": "Namespace.method", "result": {...}}`; raw result fields are exempt from stable-schema guarantees.

---

### Task 1: Method classification and parser safety gate

**Files:**
- Create: `src/tgcli/commands/api.py`, `tests/test_cli_api_policy.py`
- Modify: `src/tgcli/cli.py`

- [ ] Write failing tests asserting `users.getFullUser` is allowed to reach the network dispatcher, `messages.sendMessage --write` exits 2, and `messages.sendMessage` without `--write` also exits 2.
- [ ] Run `.venv/bin/pytest tests/test_cli_api_policy.py -q`; expect `api` argparse failure.
- [ ] Implement `is_read_method(name)` by splitting the final method segment and checking only the four ADR verbs; add `api METHOD --params JSON [--write] [--confirm METHOD]` parser arguments; reject every non-read request before session acquisition.
- [ ] Run focused tests, then the full suite; commit as `Add fail-closed raw API policy gate`.

### Task 2: Safe request construction and result serialization

**Files:**
- Modify: `src/tgcli/commands/api.py`
- Create: `tests/test_api_conversion.py`

- [ ] Write failing tests for a valid `users.getFullUser` request, malformed JSON params producing `ConfigError`, an unknown method producing `NotFoundError`, and a fake request result whose `to_dict()` is emitted under `result`.
- [ ] Run `.venv/bin/pytest tests/test_api_conversion.py -q`; expect missing functions.
- [ ] Resolve only classes below `telethon.tl.functions` from a two-segment `Namespace.method`; reject other syntax. Convert JSON dictionaries with `_` to explicit allowed Telethon constructors, lists recursively, and base64-marked bytes; resolve `@username`/numeric peer strings through `get_input_entity` only for peer fields.
- [ ] Run focused tests and full suite; commit as `Add safe raw API request conversion`.

### Task 3: CLI integration and live proof

**Files:**
- Modify: `src/tgcli/cli.py`, `tests/live/test_live_smoke.py`, `docs/CONTRACT.md`, `docs/MAP.md`, `docs/DEVLOG.md`
- Create: `tests/test_cli_api.py`

- [ ] Write a failing CLI test asserting `tg api users.getFullUser --params '{"id":"@self"}' --json` emits the envelope, and a FloodWait from the fake client exits 5.
- [ ] Run `.venv/bin/pytest tests/test_cli_api.py -q`; expect dispatch failure.
- [ ] Route allowlisted calls through `session.client`; preserve the existing FloodWait mapper; add a gated live test for `users.getFullUser` and a non-network policy test for `messages.sendMessage --write` exit 2.
- [ ] Run full tests, then the gated live check. Update CONTRACT, MAP and DEVLOG with exact output and policy state.
- [ ] Commit as `Add read-only raw API passthrough`.
