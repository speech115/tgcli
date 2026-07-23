"""Inspect and clean local tgcli state (ADR-0040). Offline only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

RELIC_NAMES = ("mirrors", "mirror-lab", "labs", "probes")
_PREVIEW_BUCKETS = ("live", "expired", "spent", "pending")


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size
    return total


def _file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _preview_expires_at(path: Path) -> datetime | None:
    try:
        record = json.loads(path.read_text())
        return datetime.fromisoformat(record["expires_at"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _classify_preview(path: Path, *, now: datetime) -> str | None:
    suffix = path.suffix
    if suffix == ".used":
        return "spent"
    if suffix == ".pending":
        return "pending"
    if suffix != ".json":
        return None
    expires = _preview_expires_at(path)
    if expires is None:
        return "expired"
    return "live" if expires > now else "expired"


def _empty_bucket() -> dict:
    return {"count": 0, "bytes": 0}


def scan(root: Path, *, now: datetime | None = None) -> dict:
    """Classify every artefact under the state root."""
    now = now or datetime.now(UTC)
    previews = {name: _empty_bucket() for name in _PREVIEW_BUCKETS}
    preview_root = root / "previews"
    if preview_root.is_dir():
        for path in preview_root.iterdir():
            if not path.is_file():
                continue
            bucket = _classify_preview(path, now=now)
            if bucket is None:
                continue
            size = _file_bytes(path)
            previews[bucket]["count"] += 1
            previews[bucket]["bytes"] += size

    sessions_dir = root / "sessions"
    session_files = (
        [p for p in sessions_dir.glob("*.session") if p.is_file()]
        if sessions_dir.is_dir()
        else []
    )
    relics = []
    for name in RELIC_NAMES:
        path = root / name
        if path.exists():
            relics.append({"name": name, "bytes": _dir_bytes(path)})

    return {
        "previews": previews,
        "audit_log": {"bytes": _file_bytes(root / "audit.jsonl")},
        "invocations": {"bytes": _file_bytes(root / "invocations.jsonl")},
        "sessions": {
            "count": len(session_files),
            "bytes": sum(_file_bytes(path) for path in session_files),
        },
        "clones": {"bytes": _dir_bytes(root / "clones")},
        "downloads": {"bytes": _dir_bytes(root / "downloads")},
        "relics": relics,
    }


def stats(root: Path, *, now: datetime | None = None) -> dict:
    return scan(root, now=now)


def stats_rows(data: dict) -> list[tuple]:
    rows: list[tuple] = []
    for name in _PREVIEW_BUCKETS:
        bucket = data["previews"][name]
        rows.append((f"previews.{name}", bucket["count"], bucket["bytes"]))
    rows.append(("audit_log", None, data["audit_log"]["bytes"]))
    rows.append(("invocations", None, data["invocations"]["bytes"]))
    rows.append(("sessions", data["sessions"]["count"], data["sessions"]["bytes"]))
    rows.append(("clones", None, data["clones"]["bytes"]))
    rows.append(("downloads", None, data["downloads"]["bytes"]))
    for relic in data["relics"]:
        rows.append((f"relic.{relic['name']}", None, relic["bytes"]))
    return rows
