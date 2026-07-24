"""Inspect and clean local tgcli state (ADR-0040). Offline only."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.output import note
from tgcli.safety import PREVIEW_TTL

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


def _preview_age_anchor(path: Path) -> datetime:
    expires = _preview_expires_at(path)
    if expires is not None:
        return expires
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


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
        # Unreadable expires_at — truncated mid-write, or hand-edited. Fall back
        # to mtime so a preview created moments ago is never reaped as expired.
        return "expired" if now - _preview_age_anchor(path) >= PREVIEW_TTL else "live"
    return "live" if expires > now else "expired"


def _empty_bucket() -> dict:
    return {"count": 0, "bytes": 0}


def scan(root: Path, *, now: datetime | None = None) -> dict:
    """Classify every artefact under the state root."""
    now = now or datetime.now(UTC)
    previews = {name: _empty_bucket() for name in _PREVIEW_BUCKETS}
    world_readable = 0
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
            mode = path.stat().st_mode
            if mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
                world_readable += 1

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
        "previews_world_readable": world_readable,
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


def _pending_far_past_ttl(path: Path, *, now: datetime) -> bool:
    """Pending holds random_id; only reclaim when past expires_at by a full TTL."""
    expires = _preview_expires_at(path)
    if expires is None:
        return now - _preview_age_anchor(path) >= PREVIEW_TTL
    return now >= expires + PREVIEW_TTL


def _deletable_paths(
    root: Path,
    *,
    older_than: timedelta | None,
    include_pending: bool,
    now: datetime,
) -> list[Path]:
    preview_root = root / "previews"
    if not preview_root.is_dir():
        return []
    selected: list[Path] = []
    for path in sorted(preview_root.iterdir()):
        if not path.is_file():
            continue
        bucket = _classify_preview(path, now=now)
        if bucket in ("spent", "expired"):
            eligible = True
        elif bucket == "pending" and include_pending:
            eligible = _pending_far_past_ttl(path, now=now)
        else:
            eligible = False
        if not eligible:
            continue
        if older_than is not None and now - _preview_age_anchor(path) < older_than:
            continue
        selected.append(path)
    return selected


def cleanup(
    root: Path,
    *,
    older_than: timedelta | None = None,
    include_pending: bool = False,
    confirm: bool = False,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    selected = _deletable_paths(
        root, older_than=older_than, include_pending=include_pending, now=now
    )
    bytes_total = sum(_file_bytes(path) for path in selected)
    names = [path.name for path in selected]
    removed: list[str] = []
    would_remove: list[str] = []
    if confirm:
        for path in selected:
            path.unlink(missing_ok=True)
            removed.append(path.name)
        preview_root = root / "previews"
        if preview_root.is_dir():
            for path in preview_root.iterdir():
                if path.is_file():
                    try:
                        os.chmod(path, 0o600)
                    except OSError:
                        pass
    else:
        would_remove = list(names)

    inventory = scan(root, now=now)
    result = {
        "removed": removed,
        "would_remove": would_remove,
        "bytes": bytes_total,
        "confirmed": confirm,
        "kept": {
            "audit_log": True,
            "sessions": True,
            "relics": [item["name"] for item in inventory["relics"]],
        },
    }
    if not confirm and selected:
        kib = bytes_total // 1024
        note(
            f"{len(selected)} artefacts ({kib} KB) would be removed; "
            "re-run with --confirm"
        )
    return result


def cleanup_rows(data: dict) -> list[tuple]:
    names = data["removed"] if data["confirmed"] else data["would_remove"]
    return [
        ("confirmed", data["confirmed"]),
        ("count", len(names)),
        ("bytes", data["bytes"]),
        ("files", ",".join(names) or None),
    ]


def parse_older_than(value: str) -> timedelta:
    """Accept an integer day count or Nd/Nh forms (e.g. 7, 7d, 12h)."""
    raw = value.strip().lower()
    if raw.isdigit():
        return timedelta(days=int(raw))
    if len(raw) >= 2 and raw[:-1].isdigit() and raw[-1] in ("d", "h"):
        amount = int(raw[:-1])
        return timedelta(days=amount) if raw[-1] == "d" else timedelta(hours=amount)
    raise ValueError(f"invalid --older-than duration: {value!r}")
