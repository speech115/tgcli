# Mirror R1 Controlled-Lab Probe Implementation Plan

**Status:** implemented and first run on 2026-07-11; initial live acceptance
was red/inconclusive and requires fixture, verdict, and safety repairs before
this plan can be accepted.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the capability-matrix gaps that R0's real channels could not
cover, and prove copy fidelity for both ADR-0013 §10 transports (native
`forwardMessages(drop_author=True)` and Telethon download/reupload) against
disposable owned lab channels, before any M1 production code.

**Architecture:** A new pure library module (`mirror_lab`) owns the lab
manifest, the frozen fixture matrix, and verdict/fidelity comparison; it reuses
R0's `mirror_probe` classifier and report writer. A standalone script
drives explicit phases (`create`, `seed`, `probe`, `copy-native`,
`copy-reupload`, `verdict`, `teardown`); every mutation targets only channels
recorded in the local lab manifest, checks kill switches, and appends an audit
record. The initial plan treated R0's classifier as frozen; the 2026-07-12
repair corrected its attribute-order bug and bumped `probe_version` to 2.

**Tech Stack:** Python 3.12, pinned Telethon 1.44 (layer 227), stdlib
`argparse`, `asyncio`, `dataclasses`, `hashlib`, `json`, `struct`, `zlib`,
pytest/pytest-asyncio.

## Global Constraints

- Mutations are allowed only against channels created by this probe and
  recorded in the lab manifest. `assert_lab_peer` runs before every mutation;
  mutating any non-manifest peer raises `PolicyError`.
- Every mutation phase calls `safety.enforce_mutation_allowed(readonly=False)`
  first (honors `TGCLI_READONLY=1` and `TGCLI_NO_SEND=1`) and appends one
  `safety.append_audit` record before dispatch (ADR-0011 fail-closed order).
- Lab channels carry the title marker prefix `tgcli-r1-lab`; teardown refuses
  to delete any channel whose live title lost the marker.
- No mirror ledger, watcher, destination-publishing production code, and no
  `tg mirror` CLI surface: the script stays at `scripts/mirror_lab.py`
  (same research status as `scripts/mirror_probe.py`).
- Version-1 R0 reports remain byte-access evidence. Any post-repair report uses
  probe schema 2; subtype labels are not compared across schema versions.
- Reports and DEVLOG stay privacy-safe exactly as in R0: no usernames, phone
  numbers, message text, or raw TL dumps. Lab peer ids live only in the local
  manifest (never committed; `TGCLI_STATE_DIR` is outside the repo).
- Downloaded media lives only under a per-run temporary workdir and is removed
  after the copy phase; no media bytes enter the repo.
- A kind that cannot be authored in the lab is an explicit `excluded` row with
  a reason, and a kind the server rejects at seeding is an explicit
  `blocked:<ErrorClass>` result — never a silent skip (ADR-0013 §1).
- Exit codes follow the existing contract: 0 ok, 2 policy blocked, 3 config,
  4 invalid input/not found, 5 FloodWait, 1 unexpected.
- Tests never touch the network; live runs are a separate, explicitly invoked
  acceptance task.

## What R0 left open (input evidence)

From DEVLOG "R0 protected-content probe evidence (2026-07-11)":

- byte-kind gaps: `animation` was found in no real channel; the owned role is
  missing byte proof for `audio`, `document`, `sticker`, `voice`.
- kinds `not_found` anywhere: `todo`, `contact`, `geo`, `geo_live`, `venue`,
  `dice`, `giveaway`, `giveaway_results`, `invoice`, `game`,
  `paid_media_preview`, `paid_media_revealed`.
- copy fidelity of both §10 transports is untested, including the expected
  `CHAT_FORWARDS_RESTRICTED` rejection of native forward on a protected
  source.

## File Map

- `src/tgcli/mirror_lab.py` — manifest model, lab-peer policy guard, payload
  generators, frozen fixture matrix, verdict and transport-fidelity
  comparison, async provisioning/seeding/copy/teardown engines.
- `scripts/mirror_lab.py` — standalone argparse/async entrypoint with explicit
  phase subcommands, reusing `tgcli.config` and `tgcli.session`.
- `tests/test_mirror_lab.py` — pure-function and async-engine tests with fake
  Telethon clients.
- `tests/test_mirror_lab_script.py` — script argument/exit-code/output
  contract with a mocked session.
- `docs/DEVLOG.md` — one privacy-safe R1 evidence entry after live runs.

---

### Task 1: Lab manifest model and lab-peer policy guard

**Files:**

- Create: `src/tgcli/mirror_lab.py`
- Create: `tests/test_mirror_lab.py`

**Interfaces:**

- Produces: `LAB_MARKER: str`, `MANIFEST_VERSION: int`,
  `CHANNEL_ROLES: tuple[str, ...]`
- Produces: `new_manifest(account_user_id: int) -> dict`
- Produces: `load_manifest(path: Path) -> dict` (raises `ValueError` on bad
  version/shape), `save_manifest(path: Path, manifest: dict) -> None`
- Produces: `record_channel(manifest, role: str, peer_id: int, title: str)`,
  `record_seed(manifest, role: str, kind: str, message_ids: list[int])`,
  `lab_peer_ids(manifest) -> set[int]`, `assert_lab_peer(manifest, peer_id)`,
  `seeded_ids(manifest, role) -> dict[str, list[int]]`

- [ ] **Step 1: Write failing manifest tests**

```python
# tests/test_mirror_lab.py
import pytest

from tgcli.errors import PolicyError
from tgcli.mirror_lab import (
    CHANNEL_ROLES,
    LAB_MARKER,
    assert_lab_peer,
    lab_peer_ids,
    load_manifest,
    new_manifest,
    record_channel,
    record_seed,
    save_manifest,
    seeded_ids,
)


def test_new_manifest_shape():
    manifest = new_manifest(account_user_id=42)
    assert manifest["manifest_version"] == 1
    assert manifest["account_user_id"] == 42
    assert manifest["channels"] == {}
    assert manifest["seeded"] == {}
    assert manifest["created_at"]


def test_manifest_round_trip(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, f"{LAB_MARKER} open_source x")
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_load_manifest_rejects_unknown_version(tmp_path):
    path = tmp_path / "lab.json"
    path.write_text('{"manifest_version": 99}')
    with pytest.raises(ValueError):
        load_manifest(path)


def test_record_channel_requires_known_role_and_marker():
    manifest = new_manifest(7)
    with pytest.raises(ValueError):
        record_channel(manifest, "mystery", 100, f"{LAB_MARKER} x")
    with pytest.raises(PolicyError):
        record_channel(manifest, "open_source", 100, "innocent channel")


def test_assert_lab_peer_blocks_foreign_peers():
    manifest = new_manifest(7)
    record_channel(manifest, "dest_native", 200, f"{LAB_MARKER} dest_native x")
    assert lab_peer_ids(manifest) == {200}
    assert_lab_peer(manifest, 200)
    with pytest.raises(PolicyError):
        assert_lab_peer(manifest, 999)


def test_channel_roles_are_frozen():
    assert CHANNEL_ROLES == (
        "protected_source",
        "open_source",
        "dest_native",
        "dest_reupload",
    )


def test_record_and_read_seeds():
    manifest = new_manifest(7)
    record_seed(manifest, "open_source", "photo", [11])
    record_seed(manifest, "open_source", "album", [12, 13])
    assert seeded_ids(manifest, "open_source") == {
        "album": [12, 13],
        "photo": [11],
    }
    assert seeded_ids(manifest, "protected_source") == {}
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tgcli.mirror_lab'`

- [ ] **Step 3: Implement the manifest model**

```python
# src/tgcli/mirror_lab.py
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tgcli.errors import PolicyError
from tgcli.mirror_probe import write_report

LAB_MARKER = "tgcli-r1-lab"
MANIFEST_VERSION = 1
CHANNEL_ROLES = (
    "protected_source",
    "open_source",
    "dest_native",
    "dest_reupload",
)


def new_manifest(account_user_id: int) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "account_user_id": account_user_id,
        "created_at": datetime.now(UTC).isoformat(),
        "channels": {},
        "seeded": {},
    }


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError("unsupported lab manifest")
    for key in ("account_user_id", "channels", "seeded"):
        if key not in data:
            raise ValueError(f"lab manifest missing {key!r}")
    return data


def save_manifest(path: Path, manifest: dict) -> None:
    write_report(path, manifest)


def record_channel(manifest: dict, role: str, peer_id: int, title: str) -> None:
    if role not in CHANNEL_ROLES:
        raise ValueError(f"unknown lab channel role: {role}")
    if not title.startswith(LAB_MARKER):
        raise PolicyError(f"channel title lacks lab marker: {title!r}")
    manifest["channels"][role] = {"peer_id": peer_id, "title": title}


def lab_peer_ids(manifest: dict) -> set[int]:
    return {entry["peer_id"] for entry in manifest["channels"].values()}


def assert_lab_peer(manifest: dict, peer_id: int) -> None:
    if peer_id not in lab_peer_ids(manifest):
        raise PolicyError(f"peer {peer_id} is not a lab channel; refusing mutation")


def record_seed(manifest: dict, role: str, kind: str, message_ids: list[int]) -> None:
    manifest["seeded"].setdefault(role, {})[kind] = list(message_ids)


def seeded_ids(manifest: dict, role: str) -> dict[str, list[int]]:
    return dict(manifest["seeded"].get(role, {}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q` then `.venv/bin/pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
safe-commit "Add mirror lab manifest and peer guard" src/tgcli/mirror_lab.py tests/test_mirror_lab.py
```

---

### Task 2: Frozen fixture matrix and payload generators

**Files:**

- Modify: `src/tgcli/mirror_lab.py`
- Modify: `tests/test_mirror_lab.py`

**Interfaces:**

- Produces: `build_png(color: tuple[int, int, int]) -> bytes`,
  `deterministic_bytes(seed: str, size: int) -> bytes`
- Produces: `ByteFixture` dataclass with fields `kind, filename, payload,
  attributes, force_document, reupload_fidelity`
- Produces: `BYTE_FIXTURES: dict[str, ByteFixture]`,
  `NON_BYTE_LAB_KINDS: tuple[str, ...]`, `non_byte_media() -> dict[str, object]`,
  `EXCLUDED_KINDS: dict[str, str]`, `COVERED_BY_R0: frozenset[str]`,
  `ALBUM_COLORS: tuple[tuple[int, int, int], ...]`,
  `planned_kinds() -> list[str]`, `pending_kinds(manifest, role) -> list[str]`

- [ ] **Step 1: Write failing fixture tests**

```python
# append to tests/test_mirror_lab.py
from telethon.tl import types

from tgcli.mirror_probe import NON_BYTE_KINDS, classify_message
from tgcli.mirror_lab import (
    BYTE_FIXTURES,
    COVERED_BY_R0,
    EXCLUDED_KINDS,
    NON_BYTE_LAB_KINDS,
    build_png,
    deterministic_bytes,
    non_byte_media,
    pending_kinds,
    planned_kinds,
)


def test_png_payload_is_valid_signature_and_deterministic():
    payload = build_png((255, 0, 0))
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert payload == build_png((255, 0, 0))
    assert payload != build_png((0, 255, 0))


def test_deterministic_bytes_are_stable_and_sized():
    blob = deterministic_bytes("video", 1_600_000)
    assert len(blob) == 1_600_000
    assert blob == deterministic_bytes("video", 1_600_000)
    assert blob[:64] != deterministic_bytes("audio", 64)


def test_byte_fixture_matrix_covers_every_byte_kind():
    assert sorted(BYTE_FIXTURES) == [
        "animation", "audio", "document", "photo",
        "sticker", "video", "video_note", "voice",
    ]
    for kind, fixture in BYTE_FIXTURES.items():
        assert fixture.kind == kind
        assert fixture.payload()  # non-empty bytes
        assert isinstance(fixture.attributes(), list)
        assert fixture.reupload_fidelity in {"exact", "reencoded"}
    assert BYTE_FIXTURES["photo"].reupload_fidelity == "reencoded"
    assert BYTE_FIXTURES["document"].force_document is True


def test_video_fixture_crosses_download_chunk_boundary():
    assert len(BYTE_FIXTURES["video"].payload()) > 512 * 1024


def test_non_byte_media_constructs_pinned_layer_objects():
    media = non_byte_media()
    assert sorted(media) == sorted(NON_BYTE_LAB_KINDS)
    assert isinstance(media["poll"], types.InputMediaPoll)
    assert isinstance(media["todo"], types.InputMediaTodo)
    assert isinstance(media["contact"], types.InputMediaContact)
    assert isinstance(media["geo"], types.InputMediaGeoPoint)
    assert isinstance(media["geo_live"], types.InputMediaGeoLive)
    assert isinstance(media["venue"], types.InputMediaVenue)
    assert isinstance(media["dice"], types.InputMediaDice)


def test_every_probe_kind_is_planned_excluded_or_covered():
    all_kinds = set(NON_BYTE_KINDS) | set(BYTE_FIXTURES)
    planned = set(planned_kinds())
    accounted = planned | set(EXCLUDED_KINDS) | COVERED_BY_R0
    assert all_kinds <= accounted
    assert not (set(EXCLUDED_KINDS) & planned)


def test_fixture_attributes_classify_back_to_expected_kind():
    from types import SimpleNamespace as NS

    for kind in ("voice", "video_note", "animation", "sticker", "audio", "video"):
        fixture = BYTE_FIXTURES[kind]
        document = NS(
            mime_type="application/octet-stream",
            attributes=[
                a for a in fixture.attributes()
                if not isinstance(a, types.DocumentAttributeFilename)
            ],
        )
        message = NS(
            id=1, action=None, message="",
            media=types.MessageMediaDocument(document=document),
        )
        assert classify_message(message) == kind


def test_pending_kinds_shrink_as_seeds_are_recorded():
    manifest = new_manifest(7)
    assert pending_kinds(manifest, "open_source") == planned_kinds()
    record_seed(manifest, "open_source", "photo", [11])
    assert "photo" not in pending_kinds(manifest, "open_source")
    assert "photo" in pending_kinds(manifest, "protected_source")
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q`
Expected: FAIL with `ImportError` (`build_png` not defined)

- [ ] **Step 3: Implement generators and the frozen matrix**

```python
# append to src/tgcli/mirror_lab.py
import hashlib
import struct
import zlib
from dataclasses import dataclass
from typing import Callable

from telethon.tl import types


def build_png(color: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        block = tag + data
        return struct.pack(">I", len(data)) + block + struct.pack(
            ">I", zlib.crc32(block)
        )

    width = height = 4
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(color) * width
    body = zlib.compress(row * height)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", body)
        + chunk(b"IEND", b"")
    )


def deterministic_bytes(seed: str, size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        out.extend(hashlib.sha256(f"{seed}:{counter}".encode()).digest())
        counter += 1
    return bytes(out[:size])


@dataclass(frozen=True)
class ByteFixture:
    kind: str
    filename: str
    payload: Callable[[], bytes]
    attributes: Callable[[], list]
    force_document: bool
    reupload_fidelity: str  # "exact" | "reencoded"


BYTE_FIXTURES: dict[str, ByteFixture] = {
    "photo": ByteFixture(
        kind="photo",
        filename="lab-photo.png",
        payload=lambda: build_png((0, 0, 255)),
        attributes=lambda: [],
        force_document=False,
        reupload_fidelity="reencoded",
    ),
    "document": ByteFixture(
        kind="document",
        filename="lab-document.txt",
        payload=lambda: deterministic_bytes("document", 16_384),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-document.txt"),
        ],
        force_document=True,
        reupload_fidelity="exact",
    ),
    "audio": ByteFixture(
        kind="audio",
        filename="lab-audio.mp3",
        payload=lambda: deterministic_bytes("audio", 32_768),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-audio.mp3"),
            types.DocumentAttributeAudio(
                duration=3, title="Lab audio", performer="tgcli"
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
    ),
    "voice": ByteFixture(
        kind="voice",
        filename="lab-voice.ogg",
        payload=lambda: deterministic_bytes("voice", 8_192),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-voice.ogg"),
            types.DocumentAttributeAudio(duration=2, voice=True),
        ],
        force_document=False,
        reupload_fidelity="exact",
    ),
    "video": ByteFixture(
        kind="video",
        filename="lab-video.mp4",
        payload=lambda: deterministic_bytes("video", 1_600_000),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-video.mp4"),
            types.DocumentAttributeVideo(
                duration=2, w=64, h=64, supports_streaming=True
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
    ),
    "video_note": ByteFixture(
        kind="video_note",
        filename="lab-note.mp4",
        payload=lambda: deterministic_bytes("video_note", 24_576),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-note.mp4"),
            types.DocumentAttributeVideo(
                duration=2, w=240, h=240, round_message=True
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
    ),
    "animation": ByteFixture(
        kind="animation",
        filename="lab-animation.mp4",
        payload=lambda: deterministic_bytes("animation", 20_480),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-animation.mp4"),
            types.DocumentAttributeAnimated(),
            types.DocumentAttributeVideo(duration=1, w=32, h=32),
        ],
        force_document=False,
        reupload_fidelity="exact",
    ),
    "sticker": ByteFixture(
        kind="sticker",
        filename="lab-sticker.webp",
        payload=lambda: deterministic_bytes("sticker", 4_096),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-sticker.webp"),
            types.DocumentAttributeSticker(
                alt="🙂", stickerset=types.InputStickerSetEmpty()
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
    ),
}

NON_BYTE_LAB_KINDS = (
    "contact", "dice", "geo", "geo_live", "poll", "todo", "venue",
)

EXCLUDED_KINDS: dict[str, str] = {
    "game": "requires a bot-owned game",
    "giveaway": "requires a Premium boost purchase with real cost",
    "giveaway_results": "exists only after a finished giveaway",
    "invoice": "requires a payment-enabled bot",
    "paid_media_preview": "requires a monetization-enabled channel",
    "paid_media_revealed": "requires a monetization-enabled channel",
    "story": "excluded by ADR-0013 fidelity target; channel stories need boosts",
}

COVERED_BY_R0 = frozenset({"text", "webpage", "service", "empty", "unsupported"})

ALBUM_COLORS = ((255, 0, 0), (0, 255, 0))


def non_byte_media() -> dict[str, object]:
    def geo_point() -> types.InputGeoPoint:
        return types.InputGeoPoint(lat=59.93, long=30.31)

    return {
        "poll": types.InputMediaPoll(
            poll=types.Poll(
                id=0,
                question=types.TextWithEntities(text="lab poll", entities=[]),
                answers=[
                    types.PollAnswer(
                        text=types.TextWithEntities(text="A", entities=[]),
                        option=b"0",
                    ),
                    types.PollAnswer(
                        text=types.TextWithEntities(text="B", entities=[]),
                        option=b"1",
                    ),
                ],
            )
        ),
        "todo": types.InputMediaTodo(
            todo=types.TodoList(
                title=types.TextWithEntities(text="lab todo", entities=[]),
                list=[
                    types.TodoItem(
                        id=1,
                        title=types.TextWithEntities(text="item", entities=[]),
                    )
                ],
            )
        ),
        "contact": types.InputMediaContact(
            phone_number="+10000000000",
            first_name="Lab",
            last_name="Fixture",
            vcard="",
        ),
        "geo": types.InputMediaGeoPoint(geo_point=geo_point()),
        "geo_live": types.InputMediaGeoLive(geo_point=geo_point(), period=900),
        "venue": types.InputMediaVenue(
            geo_point=geo_point(),
            title="Lab venue",
            address="Lab address",
            provider="lab",
            venue_id="lab-1",
            venue_type="lab",
        ),
        "dice": types.InputMediaDice(emoticon="🎲"),
    }


def planned_kinds() -> list[str]:
    return ["text", *sorted(BYTE_FIXTURES), *NON_BYTE_LAB_KINDS, "album"]


def pending_kinds(manifest: dict, role: str) -> list[str]:
    done = set(manifest["seeded"].get(role, {}))
    return [kind for kind in planned_kinds() if kind not in done]
```

Note: if any constructor keyword differs on the pinned layer (most likely
`TodoList`/`TodoItem`), fix the factory against
`.venv/bin/python -c "import inspect, telethon.tl.types as t; print(inspect.signature(t.TodoList))"`
until `test_non_byte_media_constructs_pinned_layer_objects` passes. Do not
delete a kind from the matrix to make the test pass; only adjust constructor
arguments.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q` then `.venv/bin/pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
safe-commit "Add mirror lab fixture matrix" src/tgcli/mirror_lab.py tests/test_mirror_lab.py
```

---

### Task 3: Lab verdict and transport-fidelity comparison

**Files:**

- Modify: `src/tgcli/mirror_lab.py`
- Modify: `tests/test_mirror_lab.py`

**Interfaces:**

- Consumes: R0 report shape from `mirror_probe.probe_chat` (fields
  `capabilities[].kind`, `.telethon_bytes`, `.samples[].sha256`,
  `.samples[].decode`).
- Produces: `lab_verdict(report: dict, manifest: dict, role: str) -> dict`
  with keys `verdict` (`"green"|"red"`), `missing`, `failing`, `excluded`,
  `covered_by_r0`.
- Produces: `compare_transport(source_report: dict, dest_report: dict, *,
  transport: str) -> dict` with keys `transport`, `verdict`, `rows[]`
  (`kind`, `expectation`, `result`).

- [ ] **Step 1: Write failing verdict/fidelity tests**

```python
# append to tests/test_mirror_lab.py
from tgcli.mirror_lab import compare_transport, lab_verdict


def capability(kind, *, shas=(), bytes_state="pass", decode="pass"):
    return {
        "kind": kind,
        "sample_count": max(len(shas), 1),
        "coverage": "complete",
        "telethon_bytes": bytes_state,
        "samples": [
            {"kind": kind, "decode": decode, "telethon_bytes": bytes_state,
             "sha256": sha, "bytes": 1 if sha else None, "error": None}
            for sha in (shas or (None,))
        ],
    }


def report(*capabilities):
    return {"capabilities": list(capabilities)}


def seeded_manifest():
    manifest = new_manifest(7)
    record_seed(manifest, "protected_source", "photo", [1])
    record_seed(manifest, "protected_source", "poll", [2])
    record_seed(manifest, "protected_source", "album", [3, 4])
    return manifest


def test_lab_verdict_green_when_all_seeded_kinds_pass():
    result = lab_verdict(
        report(
            capability("photo", shas=("a",)),
            capability("poll", bytes_state="not_applicable"),
        ),
        seeded_manifest(),
        "protected_source",
    )
    assert result["verdict"] == "green"
    assert result["missing"] == [] and result["failing"] == []
    assert "giveaway" in result["excluded"]


def test_lab_verdict_red_on_missing_or_failing_kind():
    missing = lab_verdict(
        report(capability("photo", shas=("a",))),
        seeded_manifest(),
        "protected_source",
    )
    assert missing["verdict"] == "red" and missing["missing"] == ["poll"]

    failing = lab_verdict(
        report(
            capability("photo", shas=(), bytes_state="fail"),
            capability("poll", bytes_state="not_applicable"),
        ),
        seeded_manifest(),
        "protected_source",
    )
    assert failing["verdict"] == "red" and failing["failing"] == ["photo"]


def test_compare_transport_native_requires_exact_hashes():
    source = report(capability("video", shas=("v1",)), capability("photo", shas=("p1",)))
    dest_ok = report(capability("video", shas=("v1",)), capability("photo", shas=("p1",)))
    dest_bad = report(capability("video", shas=("zz",)), capability("photo", shas=("p1",)))

    ok = compare_transport(source, dest_ok, transport="native")
    assert ok["verdict"] == "green"
    assert {row["result"] for row in ok["rows"]} == {"pass"}

    bad = compare_transport(source, dest_bad, transport="native")
    assert bad["verdict"] == "red"
    video_row = next(r for r in bad["rows"] if r["kind"] == "video")
    assert video_row["result"] == "fail" and video_row["expectation"] == "exact"


def test_compare_transport_reupload_allows_photo_reencode():
    source = report(capability("photo", shas=("p1",)), capability("video", shas=("v1",)))
    dest = report(capability("photo", shas=("different",)), capability("video", shas=("v1",)))
    result = compare_transport(source, dest, transport="reupload")
    assert result["verdict"] == "green"
    photo_row = next(r for r in result["rows"] if r["kind"] == "photo")
    assert photo_row["expectation"] == "reencoded" and photo_row["result"] == "pass"


def test_compare_transport_flags_missing_dest_kind():
    source = report(capability("video", shas=("v1",)))
    result = compare_transport(source, report(), transport="native")
    assert result["verdict"] == "red"
    assert result["rows"][0]["result"] == "missing"
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q`
Expected: FAIL with `ImportError` (`lab_verdict` not defined)

- [ ] **Step 3: Implement verdict and comparison**

```python
# append to src/tgcli/mirror_lab.py
from tgcli.mirror_probe import NON_BYTE_KINDS


def lab_verdict(report: dict, manifest: dict, role: str) -> dict:
    seeded = manifest["seeded"].get(role, {})
    expected = {kind for kind in seeded if kind != "album"}
    observed = {row["kind"]: row for row in report["capabilities"]}
    missing = sorted(expected - set(observed))
    failing = sorted(
        kind
        for kind, row in observed.items()
        if kind in expected
        and not (
            row["telethon_bytes"] == "pass"
            or (
                kind in NON_BYTE_KINDS
                and all(sample["decode"] == "pass" for sample in row["samples"])
            )
        )
    )
    green = not missing and not failing
    return {
        "verdict": "green" if green else "red",
        "missing": missing,
        "failing": failing,
        "excluded": dict(EXCLUDED_KINDS),
        "covered_by_r0": sorted(COVERED_BY_R0),
    }


def _kind_shas(report: dict) -> dict[str, list[str]]:
    return {
        row["kind"]: sorted(
            sample["sha256"] for sample in row["samples"] if sample["sha256"]
        )
        for row in report["capabilities"]
    }


def compare_transport(source_report: dict, dest_report: dict, *, transport: str) -> dict:
    if transport not in {"native", "reupload"}:
        raise ValueError(f"unknown transport: {transport}")
    source = {row["kind"]: row for row in source_report["capabilities"]}
    dest = {row["kind"]: row for row in dest_report["capabilities"]}
    source_shas = _kind_shas(source_report)
    dest_shas = _kind_shas(dest_report)

    rows = []
    for kind in sorted(set(source) & set(BYTE_FIXTURES)):
        expectation = "exact"
        if transport == "reupload":
            expectation = BYTE_FIXTURES[kind].reupload_fidelity
        if kind not in dest:
            rows.append({"kind": kind, "expectation": expectation, "result": "missing"})
            continue
        if expectation == "exact":
            matched = bool(source_shas[kind]) and source_shas[kind] == dest_shas.get(kind)
        else:
            matched = dest[kind]["telethon_bytes"] == "pass" and len(
                dest_shas.get(kind, [])
            ) == len(source_shas[kind])
        rows.append(
            {
                "kind": kind,
                "expectation": expectation,
                "result": "pass" if matched else "fail",
            }
        )
    green = rows and all(row["result"] == "pass" for row in rows)
    return {
        "transport": transport,
        "verdict": "green" if green else "red",
        "rows": rows,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q` then `.venv/bin/pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
safe-commit "Add mirror lab verdict and fidelity comparison" src/tgcli/mirror_lab.py tests/test_mirror_lab.py
```

---

### Task 4: Channel provisioning and seeding engines

**Files:**

- Modify: `src/tgcli/mirror_lab.py`
- Modify: `tests/test_mirror_lab.py`

**Interfaces:**

- Consumes: `safety.enforce_mutation_allowed(readonly: bool)`,
  `safety.append_audit(action: str, account: str, details: dict)`,
  Task 1/2 manifest and fixture APIs.
- Produces: `async create_lab_channels(tg, manifest, manifest_path,
  account_alias, note) -> dict`
- Produces: `async seed_sources(tg, manifest, manifest_path, account_alias,
  note) -> dict` (returns `{role: {kind: "seeded"|"blocked:<Error>"}}`)

- [ ] **Step 1: Write failing engine tests with a fake client**

```python
# append to tests/test_mirror_lab.py
from types import SimpleNamespace as NS

import pytest
from telethon.tl import functions

from tgcli.mirror_lab import create_lab_channels, seed_sources


class FakeTG:
    """Records raw requests and high-level sends; returns canned results."""

    def __init__(self):
        self.raw_requests = []
        self.sent_files = []
        self.sent_messages = []
        self._next_channel_id = 100
        self._next_message_id = 1000

    async def __call__(self, request):
        self.raw_requests.append(request)
        if isinstance(request, functions.channels.CreateChannelRequest):
            self._next_channel_id += 1
            return NS(chats=[NS(id=self._next_channel_id, title=request.title)])
        if isinstance(request, functions.messages.SendMediaRequest):
            self._next_message_id += 1
            return NS(updates=[NS(message=NS(id=self._next_message_id))])
        return NS(updates=[])

    async def get_entity(self, ref):
        return NS(id=getattr(ref, "channel_id", ref), title="")

    async def send_message(self, entity, text):
        self._next_message_id += 1
        self.sent_messages.append(text)
        return NS(id=self._next_message_id)

    async def send_file(self, entity, file, **kwargs):
        self.sent_files.append((entity, kwargs))
        if isinstance(file, list):
            out = []
            for _ in file:
                self._next_message_id += 1
                out.append(NS(id=self._next_message_id))
            return out
        self._next_message_id += 1
        return NS(id=self._next_message_id)


def quiet(_message):
    pass


@pytest.mark.asyncio
async def test_create_lab_channels_records_all_roles_and_protects_source(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    assert set(manifest["channels"]) == set(CHANNEL_ROLES)
    for entry in manifest["channels"].values():
        assert entry["title"].startswith(LAB_MARKER)
    toggles = [
        r for r in tg.raw_requests
        if isinstance(r, functions.messages.ToggleNoForwardsRequest)
    ]
    assert len(toggles) == 1 and toggles[0].enabled is True
    assert load_manifest(path)["channels"] == manifest["channels"]


@pytest.mark.asyncio
async def test_create_lab_channels_is_idempotent(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    created = len(tg.raw_requests)
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    assert len(tg.raw_requests) == created  # no second creation


@pytest.mark.asyncio
async def test_seed_sources_covers_plan_and_is_idempotent(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    results = await seed_sources(tg, manifest, path, "labacct", quiet)

    for role in ("protected_source", "open_source"):
        assert sorted(seeded_ids(manifest, role)) == sorted(planned_kinds())
        assert set(results[role].values()) == {"seeded"}
        assert len(seeded_ids(manifest, role)["album"]) == len(ALBUM_COLORS)

    sent_before = len(tg.sent_files)
    again = await seed_sources(tg, manifest, path, "labacct", quiet)
    assert len(tg.sent_files) == sent_before
    assert again == {"protected_source": {}, "open_source": {}}


@pytest.mark.asyncio
async def test_seed_sources_records_server_rejection_explicitly(tmp_path):
    class RejectingTG(FakeTG):
        async def send_file(self, entity, file, **kwargs):
            attributes = kwargs.get("attributes") or []
            if any(type(a).__name__ == "DocumentAttributeSticker" for a in attributes):
                raise RuntimeError("STICKER_INVALID")
            return await super().send_file(entity, file, **kwargs)

    tg = RejectingTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    results = await seed_sources(tg, manifest, path, "labacct", quiet)
    assert results["open_source"]["sticker"] == "blocked:RuntimeError"
    assert "sticker" not in seeded_ids(manifest, "open_source")


@pytest.mark.asyncio
async def test_mutations_respect_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_READONLY", "1")
    tg = FakeTG()
    manifest = new_manifest(7)
    with pytest.raises(PolicyError):
        await create_lab_channels(tg, manifest, tmp_path / "lab.json", "labacct", quiet)
    assert tg.raw_requests == []
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q`
Expected: FAIL with `ImportError` (`create_lab_channels` not defined)

- [ ] **Step 3: Implement provisioning and seeding**

```python
# append to src/tgcli/mirror_lab.py
import os

from telethon.tl import functions

from tgcli.safety import append_audit, enforce_mutation_allowed


def _random_id() -> int:
    return int.from_bytes(os.urandom(8), "little", signed=True)


def _sent_message_id(update) -> int:
    for item in getattr(update, "updates", ()):
        message = getattr(item, "message", None)
        if message is not None and hasattr(message, "id"):
            return message.id
    for item in getattr(update, "updates", ()):
        if type(item).__name__ == "UpdateMessageID":
            return item.id
    raise ValueError("could not extract sent message id from update")


async def _lab_entity(tg, manifest: dict, role: str):
    channel = manifest["channels"][role]
    assert_lab_peer(manifest, channel["peer_id"])
    return await tg.get_entity(types.PeerChannel(channel["peer_id"]))


async def create_lab_channels(tg, manifest, manifest_path, account_alias, note) -> dict:
    stamp = manifest["created_at"][:19].replace(":", "").replace("-", "")
    for role in CHANNEL_ROLES:
        if role in manifest["channels"]:
            note(f"{role}: already created, skipping")
            continue
        enforce_mutation_allowed(readonly=False)
        title = f"{LAB_MARKER} {role} {stamp}"
        append_audit("mirror-lab-create", account_alias, {"role": role, "title": title})
        update = await tg(
            functions.channels.CreateChannelRequest(
                title=title,
                about="tgcli R1 disposable lab channel",
                broadcast=True,
                megagroup=False,
            )
        )
        channel = update.chats[0]
        record_channel(manifest, role, channel.id, title)
        save_manifest(manifest_path, manifest)
        if role == "protected_source":
            await tg(
                functions.messages.ToggleNoForwardsRequest(peer=channel, enabled=True)
            )
        note(f"{role}: created lab channel")
    return manifest


async def _seed_kind(tg, entity, kind: str) -> list[int]:
    if kind == "text":
        message = await tg.send_message(entity, "lab text fixture")
        return [message.id]
    if kind == "album":
        files = [build_png(color) for color in ALBUM_COLORS]
        messages = await tg.send_file(entity, files)
        return [message.id for message in messages]
    if kind in BYTE_FIXTURES:
        fixture = BYTE_FIXTURES[kind]
        message = await tg.send_file(
            entity,
            fixture.payload(),
            attributes=fixture.attributes(),
            force_document=fixture.force_document,
        )
        return [message.id]
    media = non_byte_media()[kind]
    update = await tg(
        functions.messages.SendMediaRequest(
            peer=entity, media=media, message="", random_id=_random_id()
        )
    )
    return [_sent_message_id(update)]


async def seed_sources(tg, manifest, manifest_path, account_alias, note) -> dict:
    results: dict[str, dict[str, str]] = {}
    for role in ("protected_source", "open_source"):
        entity = await _lab_entity(tg, manifest, role)
        results[role] = {}
        for kind in pending_kinds(manifest, role):
            enforce_mutation_allowed(readonly=False)
            append_audit(
                "mirror-lab-seed", account_alias, {"role": role, "kind": kind}
            )
            try:
                ids = await _seed_kind(tg, entity, kind)
            except Exception as exc:
                results[role][kind] = f"blocked:{type(exc).__name__}"
                note(f"{role}: {kind} blocked by {type(exc).__name__}")
                continue
            record_seed(manifest, role, kind, ids)
            save_manifest(manifest_path, manifest)
            results[role][kind] = "seeded"
            note(f"{role}: seeded {kind}")
    return results
```

Note: `types` is already imported at module top from Task 2; keep exactly one
import. The kill-switch check runs before the audit append and before any
network call, matching ADR-0011 ordering.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q` then `.venv/bin/pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
safe-commit "Add mirror lab provisioning and seeding" src/tgcli/mirror_lab.py tests/test_mirror_lab.py
```

---

### Task 5: Copy transports — native forward and download/reupload

**Files:**

- Modify: `src/tgcli/mirror_lab.py`
- Modify: `tests/test_mirror_lab.py`

**Interfaces:**

- Consumes: Task 4 engines and manifest seeds.
- Produces: `async copy_native(tg, manifest, account_alias, note) -> dict`
  with keys `transport="native"`, `restricted_check`
  (`"confirmed"|"unexpected_success"|"skipped:<reason>"`), `results`
  (`{kind: "forwarded"|"blocked:<Error>"}`).
- Produces: `async copy_reupload(tg, manifest, workdir: Path, account_alias,
  note) -> dict` with keys `transport="reupload"`, `results`
  (`{kind: "copied"|"not_applicable"|"blocked:<Error>"}`).

- [ ] **Step 1: Write failing transport tests**

```python
# append to tests/test_mirror_lab.py
from pathlib import Path

from telethon.errors import ChatForwardsRestrictedError

from tgcli.mirror_lab import copy_native, copy_reupload


class TransportTG(FakeTG):
    def __init__(self):
        super().__init__()
        self.forwards = []
        self.downloads = []

    async def __call__(self, request):
        if isinstance(request, functions.messages.ForwardMessagesRequest):
            from_protected = getattr(self, "protected_peer_id", None) == getattr(
                request.from_peer, "id", request.from_peer
            )
            if from_protected:
                raise ChatForwardsRestrictedError(request=request)
            self.forwards.append(request)
            return NS(updates=[])
        return await super().__call__(request)

    async def get_messages(self, entity, ids):
        return [
            NS(id=i, document=NS(attributes=[]), media=object()) for i in ids
        ]

    async def download_media(self, message, file):
        path = Path(f"{file}.bin")
        path.write_bytes(b"payload-%d" % message.id)
        self.downloads.append(path)
        return str(path)


async def seeded_lab(tg, tmp_path):
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    await seed_sources(tg, manifest, path, "labacct", quiet)
    tg.protected_peer_id = manifest["channels"]["protected_source"]["peer_id"]
    return manifest


@pytest.mark.asyncio
async def test_copy_native_confirms_restriction_and_forwards_per_kind(tmp_path):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    result = await copy_native(tg, manifest, "labacct", quiet)

    assert result["transport"] == "native"
    assert result["restricted_check"] == "confirmed"
    assert set(result["results"]) == set(planned_kinds())
    assert all(v == "forwarded" for v in result["results"].values())
    for request in tg.forwards:
        assert request.drop_author is True
        assert len(request.random_id) == len(request.id)


@pytest.mark.asyncio
async def test_copy_native_records_per_kind_blocks(tmp_path):
    class GeoLiveBlockingTG(TransportTG):
        async def __call__(self, request):
            if isinstance(request, functions.messages.ForwardMessagesRequest):
                if getattr(self, "geo_live_ids", None) and set(request.id) & self.geo_live_ids:
                    raise RuntimeError("MEDIA_INVALID")
            return await super().__call__(request)

    tg = GeoLiveBlockingTG()
    manifest = await seeded_lab(tg, tmp_path)
    tg.geo_live_ids = set(seeded_ids(manifest, "open_source")["geo_live"])
    result = await copy_native(tg, manifest, "labacct", quiet)
    assert result["results"]["geo_live"] == "blocked:RuntimeError"
    assert result["results"]["photo"] == "forwarded"


@pytest.mark.asyncio
async def test_copy_reupload_downloads_and_resends_byte_kinds(tmp_path):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = await copy_reupload(tg, manifest, workdir, "labacct", quiet)

    assert result["transport"] == "reupload"
    for kind in BYTE_FIXTURES:
        assert result["results"][kind] == "copied"
    assert result["results"]["album"] == "copied"
    for kind in ("text", *NON_BYTE_LAB_KINDS):
        assert result["results"][kind] == "not_applicable"
    assert tg.downloads  # media actually went through the download path
    assert not list(workdir.iterdir())  # workdir cleaned after the phase


@pytest.mark.asyncio
async def test_copy_phases_respect_kill_switch(tmp_path, monkeypatch):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    with pytest.raises(PolicyError):
        await copy_native(tg, manifest, "labacct", quiet)
    with pytest.raises(PolicyError):
        await copy_reupload(tg, manifest, tmp_path, "labacct", quiet)
```

- [ ] **Step 2: Run tests and verify import failure**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q`
Expected: FAIL with `ImportError` (`copy_native` not defined)

- [ ] **Step 3: Implement both transports**

```python
# append to src/tgcli/mirror_lab.py
import shutil
from pathlib import Path

from telethon.errors import ChatForwardsRestrictedError


async def copy_native(tg, manifest, account_alias, note) -> dict:
    open_entity = await _lab_entity(tg, manifest, "open_source")
    dest_entity = await _lab_entity(tg, manifest, "dest_native")
    protected_entity = await _lab_entity(tg, manifest, "protected_source")

    protected_seeds = seeded_ids(manifest, "protected_source")
    restricted_check = "skipped:no_protected_seed"
    if protected_seeds:
        first_ids = next(iter(protected_seeds.values()))
        enforce_mutation_allowed(readonly=False)
        append_audit(
            "mirror-lab-forward-restricted-check",
            account_alias,
            {"ids": len(first_ids)},
        )
        try:
            await tg(
                functions.messages.ForwardMessagesRequest(
                    from_peer=protected_entity,
                    id=list(first_ids),
                    random_id=[_random_id() for _ in first_ids],
                    to_peer=dest_entity,
                    drop_author=True,
                )
            )
            restricted_check = "unexpected_success"
        except ChatForwardsRestrictedError:
            restricted_check = "confirmed"
        note(f"protected forward check: {restricted_check}")

    results: dict[str, str] = {}
    for kind, ids in seeded_ids(manifest, "open_source").items():
        enforce_mutation_allowed(readonly=False)
        append_audit(
            "mirror-lab-copy-native", account_alias, {"kind": kind, "ids": len(ids)}
        )
        try:
            await tg(
                functions.messages.ForwardMessagesRequest(
                    from_peer=open_entity,
                    id=list(ids),
                    random_id=[_random_id() for _ in ids],
                    to_peer=dest_entity,
                    drop_author=True,
                )
            )
        except Exception as exc:
            results[kind] = f"blocked:{type(exc).__name__}"
            note(f"native {kind}: blocked by {type(exc).__name__}")
            continue
        results[kind] = "forwarded"
        note(f"native {kind}: forwarded")
    return {
        "transport": "native",
        "restricted_check": restricted_check,
        "results": results,
    }


async def copy_reupload(tg, manifest, workdir: Path, account_alias, note) -> dict:
    source_entity = await _lab_entity(tg, manifest, "protected_source")
    dest_entity = await _lab_entity(tg, manifest, "dest_reupload")
    enforce_mutation_allowed(readonly=False)

    results: dict[str, str] = {}
    run_dir = Path(workdir) / "reupload"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        for kind, ids in seeded_ids(manifest, "protected_source").items():
            if kind not in BYTE_FIXTURES and kind != "album":
                results[kind] = "not_applicable"
                continue
            enforce_mutation_allowed(readonly=False)
            append_audit(
                "mirror-lab-copy-reupload",
                account_alias,
                {"kind": kind, "ids": len(ids)},
            )
            try:
                messages = await tg.get_messages(source_entity, ids=list(ids))
                paths = []
                attributes = None
                force_document = False
                for message in messages:
                    target = run_dir / f"{kind}-{message.id}"
                    paths.append(Path(await tg.download_media(message, file=target)))
                if kind in BYTE_FIXTURES:
                    document = getattr(messages[0], "document", None)
                    if document is not None:
                        attributes = list(document.attributes)
                        force_document = BYTE_FIXTURES[kind].force_document
                if kind == "album":
                    await tg.send_file(dest_entity, [str(p) for p in paths])
                else:
                    await tg.send_file(
                        dest_entity,
                        str(paths[0]),
                        attributes=attributes,
                        force_document=force_document,
                    )
            except Exception as exc:
                results[kind] = f"blocked:{type(exc).__name__}"
                note(f"reupload {kind}: blocked by {type(exc).__name__}")
                continue
            results[kind] = "copied"
            note(f"reupload {kind}: copied")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    return {"transport": "reupload", "results": results}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mirror_lab.py -q` then `.venv/bin/pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
safe-commit "Add mirror lab copy transports" src/tgcli/mirror_lab.py tests/test_mirror_lab.py
```

---

### Task 6: Teardown and script entrypoint

**Files:**

- Modify: `src/tgcli/mirror_lab.py`
- Modify: `tests/test_mirror_lab.py`
- Create: `scripts/mirror_lab.py`
- Create: `tests/test_mirror_lab_script.py`

**Interfaces:**

- Consumes: `config.load_config()`, `config.resolve_account(config, alias)`
  (`Account.alias`), `session.client(account)`,
  `mirror_probe.probe_chat(tg, chat, account_user_id, *, role, limit,
  samples_per_kind)`, `mirror_probe.write_report(path, report)`.
- Produces: `async teardown_lab(tg, manifest, account_alias, note) -> dict`
  with key `removed: list[str]`.
- Produces: `scripts/mirror_lab.py` subcommands `create`, `seed`, `probe`,
  `copy-native`, `copy-reupload`, `verdict`, `teardown`.

- [ ] **Step 1: Write failing teardown tests**

```python
# append to tests/test_mirror_lab.py
from tgcli.mirror_lab import teardown_lab


class TeardownTG(TransportTG):
    def __init__(self, titles):
        super().__init__()
        self._titles = titles
        self.deleted = []

    async def get_entity(self, ref):
        peer_id = getattr(ref, "channel_id", ref)
        return NS(id=peer_id, title=self._titles.get(peer_id, ""))

    async def __call__(self, request):
        if isinstance(request, functions.channels.DeleteChannelRequest):
            self.deleted.append(request.channel)
            return NS(updates=[])
        return await super().__call__(request)


@pytest.mark.asyncio
async def test_teardown_deletes_only_marked_lab_channels(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, f"{LAB_MARKER} open_source x")
    record_channel(manifest, "dest_native", 200, f"{LAB_MARKER} dest_native x")
    tg = TeardownTG({
        100: f"{LAB_MARKER} open_source x",
        200: f"{LAB_MARKER} dest_native x",
    })
    result = await teardown_lab(tg, manifest, "labacct", quiet)
    assert sorted(result["removed"]) == ["dest_native", "open_source"]
    assert len(tg.deleted) == 2


@pytest.mark.asyncio
async def test_teardown_refuses_channel_without_live_marker(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, f"{LAB_MARKER} open_source x")
    tg = TeardownTG({100: "renamed innocent channel"})
    with pytest.raises(PolicyError):
        await teardown_lab(tg, manifest, "labacct", quiet)
    assert tg.deleted == []
```

- [ ] **Step 2: Write failing script contract tests**

```python
# tests/test_mirror_lab_script.py
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mirror_lab as script  # noqa: E402


def test_parse_args_requires_subcommand_and_manifest():
    with pytest.raises(SystemExit):
        script.parse_args([])
    args = script.parse_args(["create", "--manifest", "/tmp/lab.json"])
    assert args.phase == "create"
    assert args.manifest == "/tmp/lab.json"


def test_probe_requires_role_and_output():
    with pytest.raises(SystemExit):
        script.parse_args(["probe", "--manifest", "m.json"])
    args = script.parse_args(
        ["probe", "--manifest", "m.json", "--role", "dest_native",
         "--output", "out.json"]
    )
    assert args.role == "dest_native"


def test_verdict_is_pure_and_needs_no_session(tmp_path, capsys):
    source = tmp_path / "source.json"
    dest = tmp_path / "dest.json"
    row = {
        "kind": "video", "sample_count": 1, "coverage": "complete",
        "telethon_bytes": "pass",
        "samples": [{"kind": "video", "decode": "pass",
                     "telethon_bytes": "pass", "sha256": "v1",
                     "bytes": 1, "error": None}],
    }
    source.write_text(json.dumps({"capabilities": [row]}))
    dest.write_text(json.dumps({"capabilities": [row]}))
    code = script.main([
        "verdict", "--manifest", str(tmp_path / "absent.json"),
        "--source-report", str(source), "--dest-report", str(dest),
        "--transport", "native",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "green"


def test_policy_error_maps_to_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_READONLY", "1")

    class FakeAccount(NS):
        pass

    def fake_resolve(config, alias):
        return FakeAccount(alias="labacct")

    class FakeClient:
        async def __aenter__(self):
            return NS(get_me=self._me)

        async def __aexit__(self, *exc):
            return False

        @staticmethod
        async def _me():
            return NS(id=7)

    monkeypatch.setattr(script.config, "load_config", lambda: {})
    monkeypatch.setattr(script.config, "resolve_account", fake_resolve)
    monkeypatch.setattr(script.session, "client", lambda account: FakeClient())

    code = script.main(["create", "--manifest", str(tmp_path / "lab.json")])
    assert code == 2
    assert "policy" in capsys.readouterr().err.lower()
```

- [ ] **Step 3: Run tests and verify failures**

Run: `.venv/bin/pytest tests/test_mirror_lab.py tests/test_mirror_lab_script.py -q`
Expected: FAIL (`teardown_lab` not defined; `scripts/mirror_lab.py` missing)

- [ ] **Step 4: Implement teardown and the script**

```python
# append to src/tgcli/mirror_lab.py
async def teardown_lab(tg, manifest, account_alias, note) -> dict:
    entities = {}
    for role, channel in manifest["channels"].items():
        entity = await tg.get_entity(types.PeerChannel(channel["peer_id"]))
        if not getattr(entity, "title", "").startswith(LAB_MARKER):
            raise PolicyError(
                f"{role}: live title lost lab marker; refusing to delete"
            )
        entities[role] = entity
    removed = []
    for role, entity in entities.items():
        enforce_mutation_allowed(readonly=False)
        append_audit("mirror-lab-teardown", account_alias, {"role": role})
        await tg(functions.channels.DeleteChannelRequest(channel=entity))
        removed.append(role)
        note(f"{role}: deleted lab channel")
    return {"removed": removed}
```

```python
#!/usr/bin/env python3
# scripts/mirror_lab.py
"""R1 controlled-lab probe: mutates only its own disposable lab channels."""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from telethon.errors import FloodWaitError

from tgcli import config, session
from tgcli.errors import ConfigError, NotFoundError, PolicyError
from tgcli import mirror_lab
from tgcli.mirror_probe import probe_chat, write_report


def note(message: str) -> None:
    print(message, file=sys.stderr)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=None)
    sub = parser.add_subparsers(dest="phase", required=True)

    for phase in ("create", "seed", "copy-native", "copy-reupload", "teardown"):
        p = sub.add_parser(phase)
        p.add_argument("--manifest", required=True)
        if phase.startswith("copy-"):
            p.add_argument("--output", required=True)

    probe = sub.add_parser("probe")
    probe.add_argument("--manifest", required=True)
    probe.add_argument("--role", required=True, choices=mirror_lab.CHANNEL_ROLES)
    probe.add_argument("--limit", type=int, default=200)
    probe.add_argument("--output", required=True)

    verdict = sub.add_parser("verdict")
    verdict.add_argument("--manifest", required=True)
    verdict.add_argument("--source-report", required=True)
    verdict.add_argument("--dest-report", default=None)
    verdict.add_argument("--transport", choices=("native", "reupload"), default=None)
    verdict.add_argument("--role", choices=mirror_lab.CHANNEL_ROLES, default=None)

    return parser.parse_args(argv)


def run_verdict(args) -> dict:
    source = json.loads(Path(args.source_report).read_text(encoding="utf-8"))
    if args.dest_report:
        if not args.transport:
            raise ValueError("--transport is required with --dest-report")
        dest = json.loads(Path(args.dest_report).read_text(encoding="utf-8"))
        return mirror_lab.compare_transport(source, dest, transport=args.transport)
    if not args.role:
        raise ValueError("--role is required without --dest-report")
    manifest = mirror_lab.load_manifest(Path(args.manifest))
    return mirror_lab.lab_verdict(source, manifest, args.role)


async def run(args) -> dict:
    account = config.resolve_account(config.load_config(), args.account)
    manifest_path = Path(args.manifest)
    async with session.client(account) as tg:
        me = await tg.get_me()
        if manifest_path.exists():
            manifest = mirror_lab.load_manifest(manifest_path)
            if manifest["account_user_id"] != me.id:
                raise ValueError("lab manifest belongs to another account")
        else:
            if args.phase != "create":
                raise ValueError(f"manifest not found: {manifest_path}")
            manifest = mirror_lab.new_manifest(me.id)

        if args.phase == "create":
            await mirror_lab.create_lab_channels(
                tg, manifest, manifest_path, account.alias, note
            )
            return {"phase": "create", "channels": sorted(manifest["channels"])}
        if args.phase == "seed":
            results = await mirror_lab.seed_sources(
                tg, manifest, manifest_path, account.alias, note
            )
            return {"phase": "seed", "results": results}
        if args.phase == "probe":
            peer_id = manifest["channels"][args.role]["peer_id"]
            return await probe_chat(
                tg, str(peer_id), me.id, role="lab", limit=args.limit
            )
        if args.phase == "copy-native":
            return await mirror_lab.copy_native(tg, manifest, account.alias, note)
        if args.phase == "copy-reupload":
            with tempfile.TemporaryDirectory(prefix="tgcli-r1-") as workdir:
                return await mirror_lab.copy_reupload(
                    tg, manifest, Path(workdir), account.alias, note
                )
        if args.phase == "teardown":
            return await mirror_lab.teardown_lab(tg, manifest, account.alias, note)
        raise ValueError(f"unknown phase: {args.phase}")


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.phase == "verdict":
            result = run_verdict(args)
        else:
            result = asyncio.run(run(args))
        if getattr(args, "output", None):
            write_report(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except PolicyError as exc:
        print(f"lab: policy blocked: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"lab: {exc}", file=sys.stderr)
        return 3
    except (NotFoundError, ValueError, FileNotFoundError, KeyError) as exc:
        print(f"lab: invalid input: {exc}", file=sys.stderr)
        return 4
    except FloodWaitError as exc:
        print(f"lab: FLOOD_WAIT retry_after={exc.seconds}", file=sys.stderr)
        return 5
    except Exception as exc:
        print(f"lab: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mirror_lab.py tests/test_mirror_lab_script.py -q`
then `.venv/bin/pytest -q` and `.venv/bin/python scripts/check-coverage.py`
Expected: all PASS; coverage gate green

- [ ] **Step 6: Commit**

```bash
safe-commit "Add mirror lab teardown and script entrypoint" src/tgcli/mirror_lab.py scripts/mirror_lab.py tests/test_mirror_lab.py tests/test_mirror_lab_script.py
```

---

### Task 7: Live acceptance run and DEVLOG evidence

**Files:**

- Modify: `docs/DEVLOG.md`

This task performs real Telegram mutations, but only against the four
disposable lab channels the script itself creates. Run it manually, phase by
phase, and stop on any non-zero exit.

- [ ] **Step 1: Provision and seed the lab**

```bash
LAB=$TGCLI_STATE_DIR/mirror-lab/r1.json
.venv/bin/python scripts/mirror_lab.py --account <account> create --manifest "$LAB"
.venv/bin/python scripts/mirror_lab.py --account <account> seed --manifest "$LAB"
```

Expected: exit 0 twice; `seed` output shows `"seeded"` for every planned kind,
or explicit `blocked:<Error>` rows (those become part of the evidence, not a
reason to edit the matrix silently).

- [ ] **Step 2: Prove byte access on the protected lab source (owner role)**

```bash
.venv/bin/python scripts/mirror_lab.py --account <account> probe \
  --manifest "$LAB" --role protected_source --output /tmp/r1-protected.json
.venv/bin/python scripts/mirror_lab.py --account <account> verdict \
  --manifest "$LAB" --source-report /tmp/r1-protected.json --role protected_source
```

Expected: `"verdict": "green"` — every seeded byte kind (including
`animation`, and the owned-role gaps `audio`, `document`, `sticker`, `voice`)
returns `telethon_bytes: "pass"`, and every seeded non-byte kind decodes.

- [ ] **Step 3: Run both copy transports and their fidelity verdicts**

```bash
.venv/bin/python scripts/mirror_lab.py --account <account> copy-native \
  --manifest "$LAB" --output /tmp/r1-native.json
.venv/bin/python scripts/mirror_lab.py --account <account> probe \
  --manifest "$LAB" --role open_source --output /tmp/r1-open.json
.venv/bin/python scripts/mirror_lab.py --account <account> probe \
  --manifest "$LAB" --role dest_native --output /tmp/r1-dest-native.json
.venv/bin/python scripts/mirror_lab.py --account <account> verdict \
  --manifest "$LAB" --source-report /tmp/r1-open.json \
  --dest-report /tmp/r1-dest-native.json --transport native

.venv/bin/python scripts/mirror_lab.py --account <account> copy-reupload \
  --manifest "$LAB" --output /tmp/r1-reupload.json
.venv/bin/python scripts/mirror_lab.py --account <account> probe \
  --manifest "$LAB" --role dest_reupload --output /tmp/r1-dest-reupload.json
.venv/bin/python scripts/mirror_lab.py --account <account> verdict \
  --manifest "$LAB" --source-report /tmp/r1-protected.json \
  --dest-report /tmp/r1-dest-reupload.json --transport reupload
```

Expected: `copy-native` reports `restricted_check: "confirmed"`
(`CHAT_FORWARDS_RESTRICTED` on the protected source — the §10 router
assumption); both `verdict` runs report green, with byte-exact SHA-256 for
every document-backed kind and `reencoded` acceptance only for `photo` under
reupload. Also verify grouped album messages exist in both destinations
(`sample_count` for `photo` matches the source report).

- [ ] **Step 4: Record privacy-safe R1 evidence in DEVLOG**

Record only: runtime versions; per-kind seed results (including explicit
`blocked:` rows); protected-source probe verdict; `restricted_check` outcome;
both transport fidelity verdicts with per-kind expectation/result; excluded
kinds with reasons; confirmation that every mutation targeted only
manifest-listed lab channels and produced audit records. Do not record peer
ids, titles beyond the marker prefix, or hashes.

- [ ] **Step 5: Tear down the lab (after evidence is committed)**

```bash
.venv/bin/python scripts/mirror_lab.py --account <account> teardown --manifest "$LAB"
```

Ask the user first if they prefer to keep the lab channels for M1/M2 live
acceptance; recreation is cheap (`create` + `seed`), so teardown is the
default.

- [ ] **Step 6: Run the closeout gate and commit the evidence**

```bash
.venv/bin/pytest -q
.venv/bin/python scripts/check-coverage.py
git diff --check
safe-commit "Record mirror R1 lab evidence" docs/DEVLOG.md
```

---

## R1 Decision Gate

Stop after Task 7. Classify the next step from evidence:

1. **All green (matrix filled, both transports pass fidelity):** M0's content
   evidence is complete beyond R0. Proceed to M0 Task 0.1 (watcher-session
   concurrency) and then M1 per
   [2026-07-11-phase-mirror.md](2026-07-11-phase-mirror.md). The renderer
   capability policy in M2 Task 2.1 consumes the per-kind results verbatim.
2. **A seeded kind is `blocked:` at seeding or red at probe:** the kind moves
   to the explicit-unsupported set in ADR-0013 §1 with the recorded error as
   evidence; it does not block M1.
3. **Native forward fails fidelity on an unprotected source, or
   `restricted_check` is `unexpected_success`:** stop; ADR-0013 §10's
   transport-selection assumptions are wrong and need a revision before M1.
4. **Reupload fidelity fails for a document-backed kind (SHA mismatch):**
   stop; the protected-content promise in ADR-0013 §1 must be narrowed in a
   revised ADR before M1.
5. **The harness cannot distinguish planned kinds because fixtures or
   observations collapse into another Telegram kind:** classify the run as
   `inconclusive`; repair the fixture, probe, and verdict, then rerun. Successful
   send/copy calls alone are not fidelity evidence and do not authorize M1/M2.

## R1 Acceptance

- Every mutation was gated by `enforce_mutation_allowed`, audited, and
  targeted a manifest-listed lab channel only.
- Every planned kind produced an explicit result: `seeded`/`blocked:` at
  seeding, green/red at probe, per-kind fidelity for both transports.
- Excluded kinds are documented with reasons; nothing was silently skipped.
- `CHAT_FORWARDS_RESTRICTED` on the protected source is recorded evidence.
- No media bytes, peer ids, or message text entered the repo or DEVLOG.
- `pytest -q` and the coverage gate stay green; post-repair probe reports use
  `probe_version: 2`.
- The lab is torn down (or explicitly kept by user decision) and the manifest
  records the final state.
