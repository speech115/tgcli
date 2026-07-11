# Mirror Read-Only Capability Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, privacy-safe Telethon research probe that classifies existing protected-channel messages, proves full-byte access with SHA-256 where applicable, and writes a capability report without performing any Telegram mutation.

**Architecture:** A single focused library module owns message classification, full-byte hashing, report aggregation, and atomic report persistence. A standalone script uses existing tgcli configuration/session lifecycle to run the probe; it is deliberately not added to the public `tg` CLI contract yet. Gotd, native copy, destination publishing, ledger, watcher, and laboratory fixture creation are deferred until Telethon evidence shows they are needed.

**Tech Stack:** Python 3.12, pinned Telethon 1.44, stdlib `argparse`, `asyncio`, `dataclasses`, `hashlib`, `json`, and pytest/pytest-asyncio.

## Global Constraints

- R0 is strictly read-only: no forward, send, upload, vote, purchase, edit, delete, pin, join, leave, or mark-read call.
- Use only the existing configured account and `session.client(account)` lifecycle; never copy, reauthorize, or modify another session.
- Do not implement gotd until R0 produces a reproducible Telethon failure for a required type.
- Do not create the permanent protected laboratory in R0; that is a separately confirmed R1 mutation plan.
- Do not add `tg mirror probe` to `cli.py` or CONTRACT.md during research; keep the surface at `scripts/mirror_probe.py`.
- stdout is one JSON report; progress and controlled errors go to stderr.
- Reports contain no message text, chat title, username, phone, sender identity, Telegram filename, raw TL object, or API credentials.
- Source identity is a short SHA-256 fingerprint of account user id plus canonical peer id; raw peer id is not emitted.
- Message ids may be stored as local sample locators because they are not useful without the redacted peer; they never appear in DEVLOG.
- Full-byte `pass` requires consuming the entire Telethon download stream and recording byte count plus SHA-256. Sampling is `inconclusive`, never `pass`.
- Paid media is inspected only when already revealed; the probe never purchases it.
- Stories are classified `unsupported` without fetching.
- Unknown/new-layer objects are `unsupported` or `inconclusive`, never silently skipped.
- Existing user changes in the dirty worktree are preserved and never staged by broad path patterns.

---

## File Map

- `src/tgcli/mirror_probe.py` — pure classification, Telethon byte probe, report aggregation, redaction, atomic JSON persistence.
- `scripts/mirror_probe.py` — standalone argparse/async entrypoint using existing config and session modules.
- `tests/test_mirror_probe.py` — unit and async tests for classification, hashing, aggregation, privacy, and failure states.
- `tests/test_mirror_probe_script.py` — script argument/output/error contract with a mocked session.
- `docs/DEVLOG.md` — one privacy-safe R0 evidence entry after live runs.

---

### Task 1: Frozen report model and message classifier

**Files:**

- Create: `src/tgcli/mirror_probe.py`
- Create: `tests/test_mirror_probe.py`

**Interfaces:**

- Produces: `classify_message(message) -> str`
- Produces: `empty_capability(kind: str, sample_id: int) -> dict`
- Produces: `runtime_metadata() -> dict`
- Later tasks consume the exact capability kinds and result shapes defined here.

- [ ] **Step 1: Write failing classification tests**

Use real pinned Telethon constructors for representative media and document
attributes so the tests fail if the installed TL layer changes.

```python
# tests/test_mirror_probe.py
from types import SimpleNamespace as NS

from telethon.tl import types

from tgcli.mirror_probe import classify_message, empty_capability


def message(media=None, *, text="", grouped_id=None, reply_to=None, noforwards=True):
    return NS(
        id=42,
        media=media,
        message=text,
        entities=[],
        grouped_id=grouped_id,
        reply_to=reply_to,
        noforwards=noforwards,
        action=None,
    )


def test_classifies_core_message_families():
    assert classify_message(message(text="hello")) == "text"
    assert classify_message(message(types.MessageMediaPhoto(photo=None))) == "photo"
    assert classify_message(message(types.MessageMediaPoll(poll=None, results=None))) == "poll"
    assert classify_message(message(types.MessageMediaUnsupported())) == "unsupported"


def test_classifies_document_subtypes_from_attributes():
    voice = NS(
        mime_type="audio/ogg",
        attributes=[types.DocumentAttributeAudio(duration=1, voice=True)],
    )
    round_video = NS(
        mime_type="video/mp4",
        attributes=[
            types.DocumentAttributeVideo(
                duration=1, w=320, h=320, round_message=True, supports_streaming=True
            )
        ],
    )
    assert classify_message(message(types.MessageMediaDocument(document=voice))) == "voice"
    assert classify_message(message(types.MessageMediaDocument(document=round_video))) == "video_note"


def test_empty_capability_starts_unproven():
    assert empty_capability("video", 42) == {
        "kind": "video",
        "sample_message_id": 42,
        "decode": "pass",
        "telethon_bytes": "not_applicable",
        "bytes": None,
        "sha256": None,
        "error": None,
    }
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run:

```bash
.venv/bin/pytest tests/test_mirror_probe.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named
'tgcli.mirror_probe'`.

- [ ] **Step 3: Implement the frozen classifier and result shape**

```python
# src/tgcli/mirror_probe.py
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import telethon
from telethon.tl import alltlobjects, types


NON_BYTE_KINDS = {
    "text", "webpage", "poll", "todo", "contact", "geo", "geo_live",
    "venue", "dice", "giveaway", "giveaway_results", "invoice", "game",
    "paid_media_preview", "paid_media_revealed", "service", "story", "empty",
    "unsupported",
}


def _document_kind(document) -> str:
    attributes = list(getattr(document, "attributes", ()) or ())
    for attribute in attributes:
        if isinstance(attribute, types.DocumentAttributeSticker):
            return "sticker"
        if isinstance(attribute, types.DocumentAttributeAnimated):
            return "animation"
        if isinstance(attribute, types.DocumentAttributeAudio):
            return "voice" if attribute.voice else "audio"
        if isinstance(attribute, types.DocumentAttributeVideo):
            if attribute.round_message:
                return "video_note"
            return "video"
    return "document"


def classify_message(message) -> str:
    if getattr(message, "action", None) is not None:
        return "service"
    media = getattr(message, "media", None)
    if media is None or isinstance(media, types.MessageMediaEmpty):
        return "text" if getattr(message, "message", "") else "empty"
    if isinstance(media, types.MessageMediaPhoto):
        return "photo"
    if isinstance(media, types.MessageMediaDocument):
        return _document_kind(media.document)
    mapping = {
        types.MessageMediaWebPage: "webpage",
        types.MessageMediaPoll: "poll",
        types.MessageMediaToDo: "todo",
        types.MessageMediaContact: "contact",
        types.MessageMediaGeo: "geo",
        types.MessageMediaGeoLive: "geo_live",
        types.MessageMediaVenue: "venue",
        types.MessageMediaDice: "dice",
        types.MessageMediaGiveaway: "giveaway",
        types.MessageMediaGiveawayResults: "giveaway_results",
        types.MessageMediaInvoice: "invoice",
        types.MessageMediaGame: "game",
        types.MessageMediaStory: "story",
        types.MessageMediaUnsupported: "unsupported",
    }
    if isinstance(media, types.MessageMediaPaidMedia):
        revealed = bool(media.extended_media) and all(
            type(item).__name__ == "MessageExtendedMedia"
            for item in media.extended_media
        )
        return "paid_media_revealed" if revealed else "paid_media_preview"
    for media_type, kind in mapping.items():
        if isinstance(media, media_type):
            return kind
    return "unsupported"


def empty_capability(kind: str, sample_id: int) -> dict:
    return {
        "kind": kind,
        "sample_message_id": sample_id,
        "decode": "pass",
        "telethon_bytes": "not_applicable" if kind in NON_BYTE_KINDS else "not_tested",
        "bytes": None,
        "sha256": None,
        "error": None,
    }


def runtime_metadata() -> dict:
    return {
        "telethon": telethon.__version__,
        "telegram_layer": alltlobjects.LAYER,
        "probed_at": datetime.now(UTC).isoformat(),
    }
```

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_mirror_probe.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit Task 1 files**

```bash
safe-commit "Add mirror probe classifier" src/tgcli/mirror_probe.py tests/test_mirror_probe.py
```

---

### Task 2: Full-byte Telethon integrity probe

**Files:**

- Modify: `src/tgcli/mirror_probe.py`
- Modify: `tests/test_mirror_probe.py`

**Interfaces:**

- Consumes: `classify_message(message) -> str`, `empty_capability(...) -> dict`
- Produces: `probe_message(tg, message) -> dict`
- Byte-capable `pass` means the complete stream reached EOF and returned at least
  one byte; partial or zero-byte streams cannot pass.

- [ ] **Step 1: Write failing async tests for complete, zero-byte, and interrupted streams**

```python
# append to tests/test_mirror_probe.py
import hashlib
import pytest

from tgcli.mirror_probe import probe_message


class DownloadFake:
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error

    async def iter_download(self, media, request_size=None):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error


@pytest.mark.asyncio
async def test_probe_message_hashes_the_complete_stream():
    payload = [b"abc", b"def"]
    result = await probe_message(
        DownloadFake(payload),
        message(types.MessageMediaPhoto(photo=NS(id=1))),
    )
    assert result["telethon_bytes"] == "pass"
    assert result["bytes"] == 6
    assert result["sha256"] == hashlib.sha256(b"abcdef").hexdigest()


@pytest.mark.asyncio
async def test_probe_message_rejects_zero_bytes():
    result = await probe_message(
        DownloadFake([]),
        message(types.MessageMediaPhoto(photo=NS(id=1))),
    )
    assert result["telethon_bytes"] == "fail"
    assert result["error"] == "zero_bytes"


@pytest.mark.asyncio
async def test_probe_message_marks_interruption_inconclusive():
    result = await probe_message(
        DownloadFake([b"partial"], ConnectionError("offline")),
        message(types.MessageMediaPhoto(photo=NS(id=1))),
    )
    assert result["telethon_bytes"] == "inconclusive"
    assert result["bytes"] == 7
    assert result["sha256"] is None
    assert result["error"] == "ConnectionError"
```

- [ ] **Step 2: Run the new tests and verify missing-function failure**

Run: `.venv/bin/pytest tests/test_mirror_probe.py -q`

Expected: import fails because `probe_message` is not defined.

- [ ] **Step 3: Implement full-stream hashing without persistent media files**

```python
# append to src/tgcli/mirror_probe.py
async def probe_message(tg, message) -> dict:
    kind = classify_message(message)
    result = empty_capability(kind, message.id)
    if result["telethon_bytes"] == "not_applicable":
        if kind == "story":
            result["decode"] = "unsupported"
            result["error"] = "stories_excluded"
        elif kind == "unsupported":
            result["decode"] = "unsupported"
            result["error"] = type(getattr(message, "media", None)).__name__
        return result

    digest = hashlib.sha256()
    byte_count = 0
    try:
        async for chunk in tg.iter_download(message.media, request_size=512 * 1024):
            data = bytes(chunk)
            digest.update(data)
            byte_count += len(data)
    except Exception as exc:
        result.update(
            telethon_bytes="inconclusive",
            bytes=byte_count,
            sha256=None,
            error=type(exc).__name__,
        )
        return result

    if byte_count == 0:
        result.update(telethon_bytes="fail", bytes=0, error="zero_bytes")
        return result

    result.update(
        telethon_bytes="pass",
        bytes=byte_count,
        sha256=digest.hexdigest(),
    )
    return result
```

- [ ] **Step 4: Run focused and regression tests**

Run:

```bash
.venv/bin/pytest tests/test_mirror_probe.py -q
.venv/bin/pytest -q
```

Expected: focused tests pass; full suite remains green.

- [ ] **Step 5: Commit the integrity probe**

```bash
safe-commit "Probe complete Telegram media bytes" src/tgcli/mirror_probe.py tests/test_mirror_probe.py
```

---

### Task 3: Chat aggregation, privacy, and atomic report persistence

**Files:**

- Modify: `src/tgcli/mirror_probe.py`
- Modify: `tests/test_mirror_probe.py`

**Interfaces:**

- Consumes: `probe_message(tg, message) -> dict`
- Produces: `probe_chat(tg, chat: str, account_user_id: int, *, role: str,
  limit: int, samples_per_kind: int = 3) -> dict`
- Produces: `write_report(path: Path, report: dict) -> None`

- [ ] **Step 1: Write failing aggregation/privacy tests**

```python
# append to tests/test_mirror_probe.py
import json

from tgcli.mirror_probe import probe_chat, write_report


class ChatFake(DownloadFake):
    def __init__(self, messages, entity):
        super().__init__([b"photo"])
        self.messages = messages
        self.entity = entity

    async def get_entity(self, chat):
        return self.entity

    async def iter_messages(self, entity, limit=None):
        for item in self.messages[:limit]:
            yield item


@pytest.mark.asyncio
async def test_probe_chat_aggregates_samples_per_kind_and_redacts_identity():
    source = ChatFake(
        [
            message(text="secret"),
            message(types.MessageMediaPhoto(photo=NS(id=1))),
            message(types.MessageMediaPhoto(photo=NS(id=2))),
        ],
        NS(id=999, title="Private title", username="private_name", noforwards=True),
    )
    report = await probe_chat(
        source,
        "Private title",
        123,
        role="owned",
        limit=100,
        samples_per_kind=3,
    )
    encoded = json.dumps(report)
    assert report["source"]["protected"] is True
    assert report["source"]["role"] == "owned"
    assert len(report["source"]["fingerprint"]) == 16
    assert {item["kind"] for item in report["capabilities"]} == {"text", "photo"}
    photo = next(item for item in report["capabilities"] if item["kind"] == "photo")
    assert photo["sample_count"] == 2
    assert photo["coverage"] == "limited"
    assert photo["telethon_bytes"] == "pass"
    assert "Private title" not in encoded
    assert "private_name" not in encoded
    assert "secret" not in encoded
    assert "999" not in encoded


def test_write_report_is_atomic(tmp_path):
    destination = tmp_path / "probe.json"
    write_report(destination, {"probe_version": 1})
    assert json.loads(destination.read_text()) == {"probe_version": 1}
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Run tests and verify missing-function failure**

Run: `.venv/bin/pytest tests/test_mirror_probe.py -q`

Expected: import fails for `probe_chat` or `write_report`.

- [ ] **Step 3: Implement bounded multi-sample aggregation and atomic JSON output**

```python
# append to src/tgcli/mirror_probe.py
def _source_fingerprint(account_user_id: int, peer_id: int) -> str:
    value = f"{account_user_id}:{peer_id}".encode()
    return hashlib.sha256(value).hexdigest()[:16]


def _aggregate_kind(kind: str, rows: list[dict], target: int) -> dict:
    states = {row["telethon_bytes"] for row in rows}
    if states == {"pass"}:
        result = "pass"
    elif len(states) == 1:
        result = states.pop()
    else:
        result = "inconclusive"
    return {
        "kind": kind,
        "sample_count": len(rows),
        "coverage": "complete" if len(rows) >= target else "limited",
        "telethon_bytes": result,
        "samples": rows,
    }


async def probe_chat(
    tg,
    chat: str,
    account_user_id: int,
    *,
    role: str,
    limit: int,
    samples_per_kind: int = 3,
) -> dict:
    if role not in {"owned", "subscriber", "lab"}:
        raise ValueError(f"invalid probe role: {role}")
    entity = await tg.get_entity(chat)
    samples: dict[str, list[dict]] = {}
    protected = bool(getattr(entity, "noforwards", False))
    scanned = 0
    async for message in tg.iter_messages(entity, limit=limit):
        scanned += 1
        protected = protected or bool(getattr(message, "noforwards", False))
        kind = classify_message(message)
        rows = samples.setdefault(kind, [])
        if len(rows) < samples_per_kind:
            rows.append(await probe_message(tg, message))

    return {
        "probe_version": 1,
        "runtime": runtime_metadata(),
        "source": {
            "fingerprint": _source_fingerprint(account_user_id, entity.id),
            "protected": protected,
            "role": role,
            "scanned": scanned,
        },
        "capabilities": [
            _aggregate_kind(kind, samples[kind], samples_per_kind)
            for kind in sorted(samples)
        ],
    }


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
```

- [ ] **Step 4: Add a negative privacy fixture and run tests**

```python
# append to tests/test_mirror_probe.py
@pytest.mark.asyncio
async def test_probe_report_does_not_leak_private_payload_fields():
    sentinel = "DO_NOT_LEAK_7f4d2a"
    document = NS(
        mime_type="application/octet-stream",
        attributes=[types.DocumentAttributeFilename(file_name=f"{sentinel}.bin")],
    )
    private_message = message(
        types.MessageMediaDocument(document=document), text=sentinel
    )
    private_message.sender = NS(first_name=sentinel, last_name=sentinel)
    source = ChatFake(
        [private_message],
        NS(id=999, title=sentinel, username=sentinel, noforwards=True),
    )
    report = await probe_chat(
        source,
        sentinel,
        123,
        role="subscriber",
        limit=100,
        samples_per_kind=3,
    )
    assert sentinel not in json.dumps(report, ensure_ascii=False)
```

Run:

```bash
.venv/bin/pytest tests/test_mirror_probe.py -q
.venv/bin/pytest -q
```

Expected: all tests pass and the privacy sentinel is absent.

- [ ] **Step 5: Commit aggregation/report behavior**

```bash
safe-commit "Aggregate privacy-safe mirror probe reports" src/tgcli/mirror_probe.py tests/test_mirror_probe.py
```

---

### Task 4: Standalone read-only probe script

**Files:**

- Create: `scripts/mirror_probe.py`
- Create: `tests/test_mirror_probe_script.py`

**Interfaces:**

- Consumes: `config.load_config`, `config.resolve_account`, `session.client`,
  `probe_chat`, and `write_report`.
- Produces command: `scripts/mirror_probe.py CHAT --account ALIAS --role
  owned|subscriber|lab --limit N --samples-per-kind N --output PATH`.
- stdout: one report JSON; stderr: progress/error only; exit 0 success, 1
  unexpected, 3 config/auth/session, 4 chat not found.

- [ ] **Step 1: Write failing argument and output tests**

```python
# tests/test_mirror_probe_script.py
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mirror_probe.py"


def test_help_describes_read_only_contract():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "read-only" in result.stdout.lower()
    assert "--output" in result.stdout


def test_output_is_required():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "example",
            "--account", "main",
            "--role", "owned",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--output" in result.stderr
```

- [ ] **Step 2: Run and verify missing-script failure**

Run: `.venv/bin/pytest tests/test_mirror_probe_script.py -q`

Expected: tests fail because `scripts/mirror_probe.py` does not exist.

- [ ] **Step 3: Implement the standalone entrypoint**

```python
#!/usr/bin/env python3
"""Read-only Telegram capability probe; performs no Telegram mutation."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from tgcli import config, session
from tgcli.errors import ConfigError, NotFoundError
from tgcli.mirror_probe import probe_chat, write_report


async def run(args) -> dict:
    account = config.resolve_account(config.load_config(), args.account)
    async with session.client(account) as tg:
        me = await tg.get_me()
        return await probe_chat(
            tg,
            args.chat,
            me.id,
            role=args.role,
            limit=args.limit,
            samples_per_kind=args.samples_per_kind,
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chat")
    parser.add_argument("--account", default=None)
    parser.add_argument("--role", required=True, choices=("owned", "subscriber", "lab"))
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--samples-per-kind", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    if args.samples_per_kind < 1:
        parser.error("--samples-per-kind must be positive")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        report = asyncio.run(run(args))
        write_report(Path(args.output), report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except ConfigError as exc:
        print(f"probe: {exc}", file=sys.stderr)
        return 3
    except (NotFoundError, ValueError):
        print("probe: chat not found or invalid", file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"probe: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

Do not add mutation flags or a gotd option.

- [ ] **Step 4: Add an in-process mocked success test**

```python
# append to tests/test_mirror_probe_script.py
import importlib.util


def load_script():
    spec = importlib.util.spec_from_file_location("mirror_probe_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_writes_the_same_redacted_report_to_stdout_and_file(
    tmp_path, monkeypatch, capsys
):
    module = load_script()
    report = {
        "probe_version": 1,
        "runtime": {"telethon": "test", "telegram_layer": 1},
        "source": {
            "fingerprint": "0123456789abcdef",
            "protected": True,
            "role": "owned",
        },
        "capabilities": [],
    }

    async def fake_run(args):
        assert args.chat == "private-source"
        assert args.account == "main"
        return report

    monkeypatch.setattr(module, "run", fake_run)
    destination = tmp_path / "report.json"
    exit_code = module.main(
        [
            "private-source",
            "--account", "main",
            "--role", "owned",
            "--limit", "20",
            "--samples-per-kind", "3",
            "--output", str(destination),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == report
    assert json.loads(destination.read_text()) == report
```

The library-level fake in `tests/test_mirror_probe.py` remains responsible for
asserting that only `get_me`, `get_entity`, `iter_messages`, and `iter_download`
are needed. The script test must not invent a second fake Telegram protocol.

Run:

```bash
.venv/bin/pytest tests/test_mirror_probe_script.py -q
.venv/bin/pytest -q
```

Expected: script tests and full suite pass.

- [ ] **Step 5: Commit the standalone script**

```bash
safe-commit "Add standalone read-only mirror probe" scripts/mirror_probe.py tests/test_mirror_probe_script.py
```

---

### Task 5: Live R0 evidence on two protected account roles

**Files:**

- Modify: `docs/DEVLOG.md`
- Runtime only, never commit: two JSON reports under a private temporary or
  `TGCLI_STATE_DIR/probes/` path.

**Interfaces:**

- Consumes: standalone script from Task 4.
- Produces: privacy-safe aggregate verdict in DEVLOG and private JSON reports for
  the next research decision.

- [ ] **Step 1: Set private runtime inputs without writing names to repo files**

```bash
export OWNED_PROTECTED_CHAT='<operator-owned protected source>'
export SUBSCRIBER_PROTECTED_CHAT='<protected source where main is subscriber>'
export PROBE_DIR="${TGCLI_STATE_DIR:-$HOME/.local/state/tgcli}/probes/r0-2026-07-11"
mkdir -p "$PROBE_DIR"
python3 - <<'PY'
import json, os
from pathlib import Path
audit = Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser() / "audit.jsonl"
state = {
    "exists": audit.exists(),
    "size": audit.stat().st_size if audit.exists() else 0,
    "mtime_ns": audit.stat().st_mtime_ns if audit.exists() else None,
}
(Path(os.environ["PROBE_DIR"]) / "audit.before.json").write_text(json.dumps(state))
PY
```

Expected: variables exist only in the current shell; no chat name is added to
tracked files.

- [ ] **Step 2: Run the owned-source read-only probe**

```bash
TGCLI_LIVE_SMOKE=1 .venv/bin/python scripts/mirror_probe.py \
  "$OWNED_PROTECTED_CHAT" --account main --role owned --limit 5000 \
  --output "$PROBE_DIR/owned.json" \
  >"$PROBE_DIR/owned.stdout.json" 2>"$PROBE_DIR/owned.stderr.log"
```

Expected: exit 0; report says `protected: true`; stderr contains no message text
or raw exception traceback.

- [ ] **Step 3: Run the ordinary-subscriber read-only probe**

```bash
TGCLI_LIVE_SMOKE=1 .venv/bin/python scripts/mirror_probe.py \
  "$SUBSCRIBER_PROTECTED_CHAT" --account main --role subscriber --limit 5000 \
  --output "$PROBE_DIR/subscriber.json" \
  >"$PROBE_DIR/subscriber.stdout.json" 2>"$PROBE_DIR/subscriber.stderr.log"
```

Expected: exit 0; report says `protected: true`; no mutation audit entry is
created by either run.

- [ ] **Step 4: Validate report privacy and internal consistency**

Run this local validator:

```bash
python3 - <<'PY'
import json, os, re
from pathlib import Path

root = Path(os.environ["PROBE_DIR"])
owned = json.loads((root / "owned.json").read_text())
owned_stdout = json.loads((root / "owned.stdout.json").read_text())
subscriber = json.loads((root / "subscriber.json").read_text())
subscriber_stdout = json.loads((root / "subscriber.stdout.json").read_text())
assert owned == owned_stdout
assert subscriber == subscriber_stdout
assert owned["source"]["fingerprint"] != subscriber["source"]["fingerprint"]
assert owned["source"]["protected"] is True
assert subscriber["source"]["protected"] is True

allowed = {"pass", "fail", "not_applicable", "inconclusive", "unsupported"}
sha256 = re.compile(r"[0-9a-f]{64}")
for report in (owned, subscriber):
    kinds = [row["kind"] for row in report["capabilities"]]
    assert len(kinds) == len(set(kinds))
    for row in report["capabilities"]:
        assert row["telethon_bytes"] in allowed
        assert 1 <= row["sample_count"] <= 3
        assert row["coverage"] in {"complete", "limited"}
        for sample in row["samples"]:
            state = sample["telethon_bytes"]
            if state == "pass":
                assert sample["bytes"] > 0
                assert sha256.fullmatch(sample["sha256"])
            elif state in {"fail", "inconclusive"}:
                assert sample["sha256"] is None

encoded = json.dumps([owned, subscriber], ensure_ascii=False)
assert os.environ["OWNED_PROTECTED_CHAT"] not in encoded
assert os.environ["SUBSCRIBER_PROTECTED_CHAT"] not in encoded

audit = Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser() / "audit.jsonl"
after = {
    "exists": audit.exists(),
    "size": audit.stat().st_size if audit.exists() else 0,
    "mtime_ns": audit.stat().st_mtime_ns if audit.exists() else None,
}
before = json.loads((root / "audit.before.json").read_text())
assert after == before, (before, after)
print("R0 reports valid and audit unchanged")
PY
```

If the validator fails, fix the probe through TDD and rerun both sources. Do not
interpret invalid reports manually.

- [ ] **Step 5: Record a privacy-safe R0 summary in DEVLOG**

Record only:

```text
- runtime versions;
- owned/subscriber role labels;
- protected flag;
- scanned count;
- discovered kinds and per-kind sample counts/coverage;
- Telethon pass/fail/not-applicable/inconclusive counts;
- exact failing kinds and controlled error classes;
- confirmation that audit.jsonl was unchanged.
```

Do not record source names, peer ids, message ids, message text, filenames,
sender data, or hashes.

- [ ] **Step 6: Run the closeout gate**

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/check-coverage.py
git diff --check
```

Expected: full suite green, coverage reports 23 namespaces, diff check clean.

- [ ] **Step 7: Commit only the evidence note**

```bash
safe-commit "Record protected mirror probe evidence" docs/DEVLOG.md
```

---

## R0 Decision Gate

Stop after Task 5. Do not start mirror implementation.

Classify the next step from evidence:

1. **Telethon passes every discovered required byte sample for both account
   roles:** do not build gotd yet. Write the R1 controlled-lab plan to fill
   missing or limited-coverage P0 fixtures and test native copy/reupload fidelity.
2. **Telethon reproducibly fails required byte types while the message decodes:**
   write a separate gotd-helper probe plan limited to those exact types and
   source roles. A failure is reproducible only after the same targeted message
   fails in two fresh probe processes with the same controlled error. Do not give
   gotd update, publishing, or mirror ownership.
3. **A type is not found:** keep it `not_found`; create it later in the controlled
   laboratory. Do not infer pass or failure.
4. **Reports are inconclusive or privacy validation fails:** repair R0 and rerun;
   no architecture decision is allowed.
5. **The subscriber source behaves differently from the owned source:** use the
   subscriber result as the protected-support boundary; owner-only success is not
   sufficient for product support.

## R0 Acceptance

- The probe performs only the permitted read calls and full-byte streams.
- Both protected account roles produce valid privacy-safe reports.
- Every discovered kind is represented exactly once.
- Every byte `pass` includes full byte count and SHA-256; incomplete streams do
  not pass.
- No chat/message/private filename/text/sender identity appears in outputs or
  DEVLOG.
- `audit.jsonl` is unchanged.
- No gotd, mirror ledger, destination, watcher, publisher, laboratory mutation,
  or public CLI contract has been added.
- The next plan is selected from the explicit R0 Decision Gate, not from prior
  assumptions.
