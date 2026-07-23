# Data Plumbing & Inbox Ergonomics Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD. Steps use checkbox syntax.

**Goal:** Ship ADR-0032: mutual-chats, dialog archive/mute, incremental export, bulk media download, read-only `tg batch`.

**Architecture:** Five independently shippable slices on one branch / one PR (five commits). Reads extend identity/media/export; inbox mutations copy the pin seam; batch is a thin sequential dispatcher over existing command functions.

**Tech Stack:** Python 3.12, Telethon 1.44, argparse, pytest. No new dependencies.

## Global Constraints

- ADR-0026/0032 scope only; grilled decisions in ADR-0032 are law.
- Update CONTRACT.md in the same commit as CLI/JSON changes.
- TDD: failing test → minimal code → green → docs → commit.
- Gates: `python3 -m pytest -q`, `python3 -m ruff check .`, `python3 -m ruff format --check .`, `python3 -m pyright`.
- stdout = contract data; stderr = progress/warnings.
- No daemons; no preview for archive/mute; no doctor in batch; caps of 100 for batch ops and bulk downloads.

## Slice order

1. mutual-chats → 2. archive/mute → 3. export incremental → 4. bulk media → 5. batch RO

---

## Slice 1 — `tg mutual-chats`

**Files:** `src/tgcli/commands/identity.py`, `src/tgcli/cli.py`, `docs/CONTRACT.md`, `SKILL.md`, `docs/PROPOSALS.md`, `tests/test_cli_mutual_chats.py`, `tests/conftest.py`

- [ ] Failing tests: common chats list; empty list ok; not-found exit 4; FakeClient records GetCommonChatsRequest
- [ ] Implement `mutual_chats(tg, ref)` + CLI + CONTRACT
- [ ] Gates + commit: `Add tg mutual-chats command`

## Slice 2 — dialog archive / mute

**Files:** `src/tgcli/commands/dialog.py`, `cli.py`, CONTRACT, tests, conftest FakeClient handlers

- [ ] Tests: archive/unarchive audit+TL; mute requires --until|--forever; unmute; readonly gate
- [ ] Implement + CONTRACT
- [ ] Commit: `Add dialog archive and mute commands`

## Slice 3 — incremental export

**Files:** `export.py`, `cli.py`, CONTRACT, `tests/test_commands_export.py` (+ CLI tests)

- [ ] Tests: after-id filters; append without cursor → exit 2; resume from last id; corrupt resume → exit 1
- [ ] Implement + CONTRACT
- [ ] Commit: `Add incremental export messages flags`

## Slice 4 — bulk media download

**Files:** `media.py`, `cli.py`, CONTRACT, media tests

- [ ] Tests: message-ids bulk; filter mode limit; >100 → exit 2; failed[] → nonzero exit
- [ ] Implement + CONTRACT
- [ ] Commit: `Add bulk media download filters`

## Slice 5 — `tg batch`

**Files:** `commands/batch.py` (new), `cli.py`, CONTRACT, MAP, SKILL, batch tests

- [ ] Tests: multi-op success; one failure → nonzero exit + full JSONL; fail-fast; >100 ops → exit 2; doctor/mutation rejected
- [ ] Implement + CONTRACT + MAP
- [ ] Commit: `Add read-only tg batch command`

Each slice appends DEVLOG and marks PROPOSALS rows shipped where applicable.
