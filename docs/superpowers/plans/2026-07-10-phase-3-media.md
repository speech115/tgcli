# Phase 3 Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Download Telegram message media through Telethon with safe paths,
resumable single-stream transfer, and opt-in parallel chunk transfer.

**Architecture:** `commands/media.py` owns source parsing, entity/message
resolution, filename/state handling, and transfer orchestration. `cli.py`
only parses `tg media download` and routes it through the existing session
context; all progress remains on stderr. Single-stream transfer is the
reliable default and resumes its append-only `.part` file. `--parallel N`
uses independent offset/stride iterators for a clean transfer and writes by
offset.

**Tech Stack:** Python 3.12, Telethon 1.44, pytest, pytest-asyncio.

## Global Constraints

- No TDLib, daemon, takeout session, or new dependency (ADR-0009).
- Commands use `session.client(account)`; command modules never print.
- stdout follows CONTRACT.md; progress uses `output.note()` through the CLI
  callback only.
- Final output paths are never overwritten; partial state lives only under
  `TGCLI_STATE_DIR/downloads/`.
- Production behavior begins only after a failing test demonstrates it.

---

### Task 1: Source and path contracts

**Files:**
- Create: `src/tgcli/commands/media.py`
- Create: `tests/test_commands_media.py`

**Interfaces:**
- Produces: `MediaSource(chat: str | None, message_id: int, private_channel_id: int | None)`;
  `parse_source(source: str, message_id: int | None) -> MediaSource`;
  `safe_filename(name: str | None, message_id: int) -> str`;
  `destination_for(name: str, requested: str | None) -> Path`.

- [x] **Step 1: Write failing tests for accepted sources and unsafe filenames.**

```python
def test_parse_source_accepts_private_link():
    assert parse_source("https://t.me/c/3817664407/878", None) == MediaSource(
        chat=None, message_id=878, private_channel_id=3817664407
    )

def test_safe_filename_cannot_escape_downloads():
    assert safe_filename("../../a\\tb.mp4", 42) == "a b.mp4"
```

- [x] **Step 2: Run the focused test and verify it fails because the module does not exist.**

Run: `.venv/bin/pytest tests/test_commands_media.py -q`
Expected: FAIL during collection with `No module named 'tgcli.commands.media'`.

- [x] **Step 3: Implement link parsing and path validation.**

```python
PRIVATE_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)/?$")
PUBLIC_LINK = re.compile(r"(?:https?://)?t\.me/([^/]+)/([1-9]\d*)/?$")

def destination_for(name: str, requested: str | None) -> Path:
    path = Path(requested).expanduser() if requested else Path("~/Downloads").expanduser() / name
    if path.exists():
        raise PolicyError(f"output path already exists: {path}")
    return path
```

- [x] **Step 4: Run the focused test and verify it passes.**

Run: `.venv/bin/pytest tests/test_commands_media.py -q`
Expected: PASS.

- [x] **Step 5: Commit the isolated parser contract.**

Run: `safe-commit "Add media source parsing" src/tgcli/commands/media.py tests/test_commands_media.py`

### Task 2: Resolve messages and private links

**Files:**
- Modify: `src/tgcli/commands/media.py`
- Modify: `tests/test_commands_media.py`

**Interfaces:**
- Consumes: `MediaSource` from Task 1.
- Produces: `resolve_message(tg, source, account_alias) -> tuple[entity, message]`.

- [x] **Step 1: Write failing tests for public lookup, dialog-backed private lookup, and no media.**

```python
async def test_private_link_scans_dialogs_before_missing_access():
    with pytest.raises(NotFoundError, match="account 'main' lacks access"):
        await resolve_message(FakeTelegram(), parse_source("t.me/c/7/8", None), "main")

async def test_resolve_message_rejects_message_without_media():
    with pytest.raises(NotFoundError, match="downloadable media"):
        await resolve_message(fake, MediaSource("@chan", 42, None), "main")
```

- [x] **Step 2: Run the focused resolution tests and verify they fail.**

Run: `.venv/bin/pytest tests/test_commands_media.py -q`
Expected: FAIL with missing `resolve_message`.

- [x] **Step 3: Implement source-aware resolution.**

```python
async def _resolve_private_entity(tg, channel_id: int, account_alias: str):
    async for dialog in tg.iter_dialogs():
        if getattr(dialog.entity, "id", None) == channel_id:
            return dialog.entity
    raise NotFoundError(f"private channel {channel_id} not found; account {account_alias!r} lacks access")

async def resolve_message(tg, source, account_alias):
    entity = await _resolve_private_entity(tg, source.private_channel_id, account_alias) if source.private_channel_id else await tg.get_entity(source.chat)
    message = await tg.get_messages(entity, ids=source.message_id)
    if message is None or not getattr(message, "media", None):
        raise NotFoundError(f"downloadable media not found: {source.message_id}")
    return entity, message
```

- [x] **Step 4: Run the focused resolution tests and verify they pass.**

Run: `.venv/bin/pytest tests/test_commands_media.py -q`
Expected: PASS.

- [x] **Step 5: Commit resolution behavior.**

Run: `safe-commit "Resolve media message links" src/tgcli/commands/media.py tests/test_commands_media.py`

### Task 3: Resumable single-stream download

**Files:**
- Modify: `src/tgcli/commands/media.py`
- Modify: `tests/test_commands_media.py`

**Interfaces:**
- Produces: `download_media(tg, source, account_alias, output=None, parallel=1, progress=None) -> dict`.
- Result shape: `{"source": str, "path": str, "bytes": int, "resumed": bool, "parallel": int}`.

- [x] **Step 1: Write failing tests for a fresh transfer, state-backed resume, and a collision.**

```python
async def test_download_resumes_from_existing_part(tmp_path, fake):
    part = tmp_path / "state" / "downloads" / "key.part"
    part.parent.mkdir(parents=True); part.write_bytes(b"old")
    result = await download_media(fake, source, "main", output=str(tmp_path / "out.bin"))
    assert fake.iter_download_calls[0]["offset"] == 3
    assert result["resumed"] is True

async def test_download_refuses_existing_final_path(tmp_path, fake):
    target = tmp_path / "out.bin"; target.write_bytes(b"done")
    with pytest.raises(PolicyError, match="already exists"):
        await download_media(fake, source, "main", output=str(target))
```

- [x] **Step 2: Run the focused transfer tests and verify they fail.**

Run: `.venv/bin/pytest tests/test_commands_media.py -q`
Expected: FAIL with missing `download_media`.

- [x] **Step 3: Implement state, serial `iter_download`, and atomic finalisation.**

```python
async for chunk in tg.iter_download(message.media, offset=offset, request_size=512 * 1024):
    handle.write(bytes(chunk))
    save_state(state_path, source, destination, handle.tell())
    if progress:
        progress(handle.tell(), total)
os.replace(part_path, destination)
state_path.unlink(missing_ok=True)
```

- [x] **Step 4: Run transfer tests and then the full suite.**

Run: `.venv/bin/pytest tests/test_commands_media.py -q && .venv/bin/pytest -q`
Expected: PASS.

- [x] **Step 5: Commit the resumable serial engine.**

Run: `safe-commit "Add resumable media downloads" src/tgcli/commands/media.py tests/test_commands_media.py`

### Task 4: CLI integration and revoked-session translation

**Files:**
- Modify: `src/tgcli/cli.py`
- Modify: `src/tgcli/commands/media.py`
- Modify: `src/tgcli/session.py`
- Create: `tests/test_cli_media.py`
- Modify: `tests/test_session.py`

**Interfaces:**
- Consumes: `download_media(...)` from Task 3.
- Produces: `tg media download <source> [message_id] [--output PATH] [--parallel N]`.

- [x] **Step 1: Write failing CLI tests for JSON output, stderr-only progress, and session revocation.**

```python
def test_media_download_json_uses_command_result(config_env, monkeypatch, capsys):
    monkeypatch.setattr(media_cmd, "download_media", AsyncMock(return_value=RESULT))
    assert main(["--json", "media", "download", "@chan", "42"]) == 0
    assert json.loads(capsys.readouterr().out) == RESULT

def test_session_revoked_exits_3(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, RevokedClient())
    assert main(["--json", "media", "download", "@chan", "42"]) == 3
```

- [x] **Step 2: Run the CLI tests and verify they fail because `media` is unknown.**

Run: `.venv/bin/pytest tests/test_cli_media.py -q`
Expected: FAIL with argparse error or missing media dispatch.

- [x] **Step 3: Add nested parser and routing, with progress written to stderr.**

```python
p_media = sub.add_parser("media", parents=[global_flags])
media_sub = p_media.add_subparsers(dest="media_command", required=True)
p_download = media_sub.add_parser("download", parents=[global_flags])
p_download.add_argument("source")
p_download.add_argument("message_id", nargs="?", type=int)
p_download.add_argument("--output")
p_download.add_argument("--parallel", type=int, default=1)
```

- [x] **Step 4: Translate `SessionRevokedError` in `session.client` to `ConfigError` and run the CLI tests.**

Run: `.venv/bin/pytest tests/test_cli_media.py -q`
Expected: PASS with no progress on stdout.

- [x] **Step 5: Commit CLI behavior.**

Run: `safe-commit "Add media download command" src/tgcli/cli.py src/tgcli/commands/media.py src/tgcli/session.py tests/test_cli_media.py tests/test_session.py`

### Task 5: Parallel transfer and documentation

**Files:**
- Modify: `src/tgcli/commands/media.py`
- Modify: `tests/test_commands_media.py`
- Modify: `docs/CONTRACT.md`
- Modify: `docs/MAP.md`
- Modify: `docs/DEVLOG.md`

**Interfaces:**
- `--parallel N` accepts positive integers; `N > 1` requires a fresh download
  and writes fixed 512 KiB chunks with bounded worker count.

- [x] **Step 1: Write a failing test proving `--parallel 2` requests disjoint offsets and rejects resume.**

```python
async def test_parallel_download_uses_disjoint_offsets(tmp_path, fake):
    await download_media(fake, source, "main", output=str(tmp_path / "out.bin"), parallel=2)
    assert {call["offset"] for call in fake.iter_download_calls} == {0, 512 * 1024}
```

- [x] **Step 2: Run the focused test and verify it fails.**

Run: `.venv/bin/pytest tests/test_commands_media.py -q`
Expected: FAIL because parallel transfer is not implemented.

- [x] **Step 3: Implement bounded offset/stride workers, then update the command contract and project map.**

```python
async def worker(index: int):
    async for chunk in tg.iter_download(message.media, offset=index * CHUNK, stride=parallel * CHUNK, request_size=CHUNK):
        os.pwrite(fd, bytes(chunk), offset)
        offset += parallel * CHUNK
await asyncio.gather(*(worker(index) for index in range(parallel)))
```

- [x] **Step 4: Run all unit checks and a CLI smoke.**

Run: `.venv/bin/pytest -q && .venv/bin/tg --help`
Expected: PASS; help lists `media`.

- [x] **Step 5: Commit the completed phase artifacts.**

Run: `safe-commit "Complete Phase 3 media downloads" src/tgcli/commands/media.py tests/test_commands_media.py docs/CONTRACT.md docs/MAP.md docs/DEVLOG.md`
