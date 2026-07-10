# Phase 5 Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe, streaming exports of dialog messages to JSONL and channel
subscribers to CSV, including takeout retry guidance.

**Architecture:** `commands/export.py` owns entity resolution, projection and
atomic file writing; it never writes stdout. `cli.py` parses an `export`
command group, invokes the command module through the existing locked
`session.client` lifecycle, and emits a normal completion summary. Message
iteration uses a Telethon takeout proxy; subscriber iteration stays on the
normal authorized client.

**Tech Stack:** Python 3.12, Telethon, stdlib `csv`/`json`/`tempfile`, pytest.

## Global Constraints

- Work only in `codex/phase-5-export`; no daemon, TDLib, background process,
  state outside the documented tgcli paths, push, or PR.
- `--output PATH` is required; JSONL/CSV go to that file atomically, while
  stdout remains contract data and stderr remains diagnostics/errors.
- Commands use `session.client(account)` for lifecycle and must stream data;
  they must not accumulate an export in memory.
- Production code follows a witnessed red-green TDD cycle; `pytest -q` must
  pass before the final local commit.

## Execution Status (2026-07-10)

- Complete: local implementation, contract, CLI smoke, a 10k-message takeout
  simulation, and live export of public `@msk7days` on account `main`.
- Live evidence: `14,296` JSONL records written to
  `/Users/sereja/Downloads/tgcli-phase5-msk7days-2026-07-10.jsonl`, ascending
  from message id `1` to `16058`, without FloodWait failures.

---

### Task 1: Freeze the export contract and command surface

**Files:**
- Create: `tests/test_cli_export.py`
- Modify: `src/tgcli/cli.py`
- Modify: `docs/CONTRACT.md`

**Interfaces:**
- Consumes: `main(argv)` and global output flags from `src/tgcli/cli.py`.
- Produces: `tg export messages <chat> --output PATH [--limit N]` and
  `tg export subscribers <channel> --output PATH [--limit N]`, each returning
  `{"export": {"kind", "format", "path", "count", "dialog"}}` on
  `--json`.

- [ ] **Step 1: Write the failing parser and output tests**

```python
def test_export_messages_requires_output(config_env, capsys):
    assert main(["export", "messages", "@chan"]) == 1
    assert "--output" in capsys.readouterr().err

def test_export_messages_json_returns_completion_summary(
    config_env, monkeypatch, tmp_path, capsys
):
    fake = make_export_fake()
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"
    assert main(["--json", "export", "messages", "@chan", "--output", str(destination)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "export": {"kind": "messages", "format": "jsonl",
                   "path": str(destination), "count": 2,
                   "dialog": {"id": -1001234, "name": "Channel"}}
    }
```

- [ ] **Step 2: Run the focused tests to verify RED**

Run: `.venv/bin/pytest tests/test_cli_export.py -q`

Expected: FAIL because the `export` parser group and dispatcher do not exist.

- [ ] **Step 3: Add only the parser and dispatch shape necessary for the tests**

```python
p_export = sub.add_parser("export", help="Export Telegram data", parents=[global_flags])
export_sub = p_export.add_subparsers(dest="export_kind", required=True)
for kind, target in (("messages", "chat"), ("subscribers", "channel")):
    command = export_sub.add_parser(kind, parents=[global_flags])
    command.add_argument(target)
    command.add_argument("--output", required=True, type=Path)
    command.add_argument("--limit", type=int)
```

Dispatch to `export_cmd.export_messages(...)` or
`export_cmd.export_subscribers(...)`, returning its summary and one TSV row.

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: `.venv/bin/pytest tests/test_cli_export.py -q`

Expected: PASS after Task 2 supplies the command module.

- [ ] **Step 5: Document the frozen CLI and completion shapes**

Add a phase-5 section to `docs/CONTRACT.md` that names both invocations,
required `--output`, JSONL message object shape (the existing `read` message
projection, one object per line), CSV subscriber columns, completion JSON, TSV
column order, atomic write behaviour, and exit-5 retry fields.

### Task 2: Stream message export through Telethon takeout

**Files:**
- Create: `src/tgcli/commands/export.py`
- Modify: `tests/test_cli_export.py`

**Interfaces:**
- Consumes: a connected Telethon-compatible client, chat string, `Path`, and
  optional integer limit.
- Produces: `async def export_messages(tg, chat: str, destination: Path,
  limit: int | None) -> dict`.

- [ ] **Step 1: Write failing behaviour tests**

```python
def test_export_messages_writes_oldest_first_jsonl_via_takeout(...):
    ...
    assert fake.takeout_calls == [{"chats": True, "megagroups": True, "channels": True}]
    assert [json.loads(line)["id"] for line in destination.read_text().splitlines()] == [1, 2]

def test_export_messages_unknown_dialog_exits_4(...):
    assert main(["--json", "export", "messages", "@ghost", "--output", str(destination)]) == 4

def test_export_messages_does_not_replace_destination_when_iteration_fails(...):
    destination.write_text("previous\n")
    ...
    assert main(["export", "messages", "@chan", "--output", str(destination)]) == 1
    assert destination.read_text() == "previous\n"
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_cli_export.py -q`

Expected: FAIL because `tgcli.commands.export` does not exist.

- [ ] **Step 3: Write the minimum streaming implementation**

```python
async def export_messages(tg, chat, destination, limit=None):
    entity = await _resolve_entity(tg, chat)
    with _atomic_text_destination(destination) as handle:
        async with tg.takeout(chats=True, megagroups=True, channels=True) as takeout:
            async for message in takeout.iter_messages(entity, limit=limit, reverse=True):
                handle.write(json.dumps(message_to_dict(message), ensure_ascii=False) + "\n")
                count += 1
    return _summary("messages", "jsonl", destination, count, entity, chat)
```

`_atomic_text_destination` writes a sibling temporary file and uses
`os.replace` only after the iterator completes; on any exception it deletes
the temporary file and preserves the prior destination. Convert `ValueError`
from `get_entity` into `NotFoundError`.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest tests/test_cli_export.py -q`

Expected: PASS for message export and no partial replacement.

### Task 3: Export subscribers as a quoted CSV stream

**Files:**
- Modify: `src/tgcli/commands/export.py`
- Modify: `tests/test_cli_export.py`

**Interfaces:**
- Produces: `async def export_subscribers(tg, channel: str, destination: Path,
  limit: int | None) -> dict` and frozen header
  `id,username,first_name,last_name,phone,is_bot`.

- [ ] **Step 1: Write failing subscriber tests**

```python
def test_export_subscribers_writes_header_and_quoted_rows(...):
    ...
    assert list(csv.DictReader(destination.open())) == [{
        "id": "7", "username": "alice", "first_name": "Alice, Jr.",
        "last_name": "", "phone": "", "is_bot": "False"
    }]

def test_export_subscribers_empty_channel_keeps_only_header(...):
    ...
    assert destination.read_text().splitlines() == ["id,username,first_name,last_name,phone,is_bot"]
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_cli_export.py -q`

Expected: FAIL because subscriber export is unhandled.

- [ ] **Step 3: Implement only the CSV stream**

Use `csv.DictWriter(..., fieldnames=SUBSCRIBER_COLUMNS, lineterminator="\n")`
inside `_atomic_text_destination`; write the header before iterating
`tg.iter_participants(entity, limit=limit)`. Project only documented fields,
normalising missing values to empty strings and `is_bot` to a boolean string.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest tests/test_cli_export.py -q`

Expected: PASS with stable header, quoting, and empty result coverage.

### Task 4: Map takeout delay and file failures to the public contract

**Files:**
- Modify: `src/tgcli/cli.py`
- Modify: `src/tgcli/commands/export.py`
- Modify: `tests/test_cli_export.py`

**Interfaces:**
- Consumes: `telethon.errors.TakeoutInitDelayError(seconds)` and `OSError`.
- Produces: exit code 5, `FLOOD_WAIT`, `retry_after=<seconds>`, and a message
  directing the user to retry after that delay for takeout delay; ordinary file
  errors return code 1 as `RUNTIME` without traceback.

- [ ] **Step 1: Write failing error tests**

```python
def test_takeout_delay_is_a_retryable_exit_5(...):
    fake.takeout_error = errors.TakeoutInitDelayError(request=None, capture=90)
    assert main(["--json", "export", "messages", "@chan", "--output", str(destination)]) == 5
    assert json.loads(capsys.readouterr().err)["error"] == {
        "code": "FLOOD_WAIT", "message": "takeout is unavailable for 90s; retry after 90s",
        "retry_after": 90
    }
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_cli_export.py::test_takeout_delay_is_a_retryable_exit_5 -q`

Expected: FAIL because delay is not currently translated.

- [ ] **Step 3: Implement the narrow translations**

In `_run_network`, catch `TakeoutInitDelayError` before the existing
`FloodWaitError` branch and raise `RateLimitError` with its `seconds`. In the
export command, catch `OSError` at destination creation/replacement and raise
`TgcliError(f"cannot write export to {destination}: {exc}")` after temporary
cleanup.

- [ ] **Step 4: Run GREEN and the complete suite**

Run: `.venv/bin/pytest tests/test_cli_export.py -q && .venv/bin/pytest -q`

Expected: all export tests and the full suite pass.

### Task 5: Phase documentation, evidence, and local commit

**Files:**
- Modify: `docs/MAP.md`
- Modify: `docs/DEVLOG.md`
- Modify: `docs/CONTRACT.md`

- [ ] **Step 1: Update documentation after the implementation is green**

Mark `src/tgcli/commands/export.py` as `[done]` in `docs/MAP.md`; append the
required dated DEVLOG entry with actual tests, live-gate result, and decision
on output contract. Keep the contract edit from Task 1 in the same change.

- [ ] **Step 2: Run final local verification**

Run: `.venv/bin/pytest -q && git status --short`

Expected: all tests pass; only intentional phase-5 files are modified.

- [ ] **Step 3: Run the live acceptance only when its safe inputs exist**

Run: `tg --account <authorized-account> export messages <designated-10k-dialog> --output <approved-local-path> --json`

Expected: success summary with `count >= 10000` and no FloodWait failure. If
the authorised account or designated non-sensitive dialog is absent, do not
guess; record it as the only live-gate blocker and stop after the commit.

- [ ] **Step 4: Make one local commit**

Run: `safe-commit "Add Telegram export commands" <exact phase-5 paths>`

Expected: one commit on `codex/phase-5-export`, clean worktree. Do not push or
create a PR.

## Self-Review

- Spec coverage: Tasks 1–4 cover both commands, takeout, JSONL/CSV, delay,
  10k-safe streaming and error cases; Task 5 covers MAP, DEVLOG, contract,
  verification and the requested commit.
- Placeholder scan: no `TODO`/`TBD` implementation instructions remain.
- Type consistency: both command functions return the same completion-summary
  dict consumed by `cli.py`; all paths are `pathlib.Path` from argparse.
