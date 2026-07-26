"""Inspect and clean local tgcli state (ADR-0040). Offline only."""

from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli import session
from tgcli.login_state import LOGIN_TTL
from tgcli.output import note
from tgcli.safety import PREVIEW_TTL

RELIC_NAMES = ("mirrors", "mirror-lab", "labs", "probes")
_PREVIEW_BUCKETS = ("live", "expired", "spent", "pending")
_LOGIN_BUCKETS = ("live", "expired")
# Previews and logins carry `expires_at`, so a live record is never eligible
# whatever the flags say. A clone media cache (ADR-0052) carries no TTL, so
# this floor is the whole liveness gate: below it the cache belongs to a
# `clone sync` that is running right now, and nothing may delete it.
MEDIA_CACHE_MIN_AGE = timedelta(hours=1)


def _dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            total += _file_bytes(entry)
    return total


def _file_bytes(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _file_mode(path: Path) -> int | None:
    """Permission bits, or None once the file is gone."""
    try:
        return path.stat().st_mode
    except OSError:
        return None


def _record_expires_at(path: Path) -> datetime | None:
    """Parsed `expires_at`, or None when it cannot be trusted.

    A timezone-less stamp (older build, hand edit, partial write) cannot be
    compared against an aware `now` — it used to raise TypeError straight out
    of `store stats`. It is unusable, not fatal, so it takes the same mtime
    fallback as an unreadable one.
    """
    try:
        record = json.loads(path.read_text())
        expires = datetime.fromisoformat(record["expires_at"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return expires if expires.tzinfo is not None else None


def _record_age_anchor(path: Path) -> datetime | None:
    """Age anchor for a record, or None once the file is gone.

    The walk lists a directory and stats its entries one by one, so a preview
    or a login consumed by a concurrent `tg` in between is simply absent —
    never a crash in an offline, read-only command.
    """
    expires = _record_expires_at(path)
    if expires is not None:
        return expires
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, tz=UTC)


def _media_cache_anchor(path: Path) -> datetime | None:
    """Newest mtime in a clone media cache: the directory or anything inside it.

    Creating `src-<id>` stamps the directory once; the download that follows
    only advances the file's own mtime, so a half-gigabyte transfer can leave
    the directory looking hours old while it is very much alive. None means the
    cache vanished mid-walk (a finished batch clears it).
    """
    stamps = []
    for entry in (path, *path.rglob("*")):
        try:
            stamps.append(entry.stat().st_mtime)
        except OSError:
            continue
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=UTC)


def _classify_ttl_record(path: Path, *, now: datetime, ttl: timedelta) -> str | None:
    """live/expired for any `expires_at`-carrying json under the state root.

    One classifier for every bucket: an unreadable `expires_at` (truncated
    mid-write, or hand-edited) falls back to mtime + ttl, so a record written
    moments ago is never reaped as expired. Previews learned this the hard
    way; logins repeated it before growing the same guard — the shared helper
    is what stops a third bucket from repeating it again. A record that
    vanished mid-walk classifies as None: absent, not expired.
    """
    expires = _record_expires_at(path)
    if expires is None:
        anchor = _record_age_anchor(path)
        if anchor is None:
            return None
        return "expired" if now - anchor >= ttl else "live"
    return "live" if expires > now else "expired"


def _classify_preview(path: Path, *, now: datetime) -> str | None:
    suffix = path.suffix
    if suffix == ".used":
        return "spent"
    if suffix == ".pending":
        return "pending"
    if suffix != ".json":
        return None
    return _classify_ttl_record(path, now=now, ttl=PREVIEW_TTL)


def _empty_bucket() -> dict:
    return {"count": 0, "bytes": 0}


def _classify_login(path: Path, *, now: datetime) -> str | None:
    """Classify a login-attempt json; staged sessions are paired by stem."""
    if path.suffix != ".json":
        return None
    return _classify_ttl_record(path, now=now, ttl=LOGIN_TTL)


def _login_pair_stats(directory: Path, login_id: str) -> tuple[int, int]:
    """Return (file_count, bytes) for an attempt's json + staged session files."""
    count = 0
    total = 0
    for name in (
        f"{login_id}.json",
        f"{login_id}.session",
        f"{login_id}.session-journal",
    ):
        path = directory / name
        if path.is_file():
            count += 1
            total += _file_bytes(path)
    return count, total


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
            mode = _file_mode(path)
            if mode is None:
                # Consumed by a concurrent tg between listing and stat: absent.
                continue
            previews[bucket]["count"] += 1
            previews[bucket]["bytes"] += _file_bytes(path)
            if mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
                world_readable += 1

    logins = {name: _empty_bucket() for name in _LOGIN_BUCKETS}
    logins_root = root / "logins"
    if logins_root.is_dir():
        seen: set[str] = set()
        for path in logins_root.iterdir():
            if not path.is_file() or path.suffix != ".json":
                continue
            login_id = path.stem
            if login_id in seen:
                continue
            seen.add(login_id)
            bucket = _classify_login(path, now=now)
            if bucket is None:
                continue
            file_count, size = _login_pair_stats(logins_root, login_id)
            logins[bucket]["count"] += file_count
            logins[bucket]["bytes"] += size

    sessions_dir = root / "sessions"
    session_files = (
        [p for p in sessions_dir.glob("*.session") if p.is_file()]
        if sessions_dir.is_dir()
        else []
    )
    bak_files = (
        [p for p in sessions_dir.glob("*.session.bak") if p.is_file()]
        if sessions_dir.is_dir()
        else []
    )
    relics = []
    for name in RELIC_NAMES:
        path = root / name
        if path.exists():
            relics.append({"name": name, "bytes": _dir_bytes(path)})

    clones_root = root / "clones"
    media_dirs = (
        sorted(path for path in clones_root.glob("*-media") if path.is_dir())
        if clones_root.is_dir()
        else []
    )

    return {
        "previews": previews,
        "previews_world_readable": world_readable,
        "logins": logins,
        "audit_log": {"bytes": _file_bytes(root / "audit.jsonl")},
        "invocations": {"bytes": _file_bytes(root / "invocations.jsonl")},
        "sessions": {
            "count": len(session_files),
            "bytes": sum(_file_bytes(path) for path in session_files),
        },
        "session_backups": {
            "count": len(bak_files),
            "bytes": sum(_file_bytes(path) for path in bak_files),
        },
        "clones": {"bytes": _dir_bytes(clones_root)},
        "clone_media_cache": {
            "count": len(media_dirs),
            "bytes": sum(_dir_bytes(path) for path in media_dirs),
        },
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
    for name in _LOGIN_BUCKETS:
        bucket = data["logins"][name]
        rows.append((f"logins.{name}", bucket["count"], bucket["bytes"]))
    rows.append(("audit_log", None, data["audit_log"]["bytes"]))
    rows.append(("invocations", None, data["invocations"]["bytes"]))
    rows.append(("sessions", data["sessions"]["count"], data["sessions"]["bytes"]))
    rows.append(
        (
            "session_backups",
            data["session_backups"]["count"],
            data["session_backups"]["bytes"],
        )
    )
    rows.append(("clones", None, data["clones"]["bytes"]))
    rows.append(
        (
            "clone_media_cache",
            data["clone_media_cache"]["count"],
            data["clone_media_cache"]["bytes"],
        )
    )
    rows.append(("downloads", None, data["downloads"]["bytes"]))
    for relic in data["relics"]:
        rows.append((f"relic.{relic['name']}", None, relic["bytes"]))
    return rows


def _pending_far_past_ttl(path: Path, *, now: datetime) -> bool:
    """Pending holds random_id; only reclaim when past expires_at by a full TTL."""
    expires = _record_expires_at(path)
    if expires is None:
        anchor = _record_age_anchor(path)
        return anchor is not None and now - anchor >= PREVIEW_TTL
    return now >= expires + PREVIEW_TTL


def _lock_busy(target: Path) -> bool:
    """Whether another process holds the flock beside `target`.

    Probed exactly the non-blocking way `session.lock_held` does, and only when
    the lock file already exists — a holder always creates it first, so a
    read-only inventory never leaves a new lock file behind. Fail-open:
    anything but a definite "free" counts as busy, because the caller is about
    to delete state that a running command may still own, and a probe that
    cannot run must never block the cleanup either.
    """
    if not target.with_suffix(".lock").exists():
        return False
    try:
        return session.lock_held(target) is not False
    except OSError:
        return True


def _attempt_lock_held(logins_root: Path, login_id: str) -> bool:
    """Whether a login attempt is still running.

    A QR wait can outlive `LOGIN_TTL`, and the running login holds the staged
    session's flock for the whole attempt (`authclient.unauthorized_client`),
    so an expired record alone does not mean abandoned: reaping it would take
    an authorization in flight.
    """
    return _lock_busy(logins_root / f"{login_id}.session")


def _any_session_lock_held(root: Path) -> bool:
    """Whether any account session is in use right now.

    A clone media cache carries no TTL and nothing writes to it during a long
    upload-only phase, so its mtime ages past `MEDIA_CACHE_MIN_AGE` while the
    batch that owns it is still running (ADR-0052). The run does hold its
    account session lock from the first request to the last, so that lock is
    the liveness signal the cache itself cannot provide. Coarse on purpose:
    cleanup would rather keep a dead cache than delete a live one.
    """
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return False
    return any(_lock_busy(path) for path in sorted(sessions_dir.glob("*.session")))


def _deletable_paths(
    root: Path,
    *,
    older_than: timedelta | None,
    include_pending: bool,
    now: datetime,
) -> list[Path]:
    selected: list[Path] = []
    preview_root = root / "previews"
    if preview_root.is_dir():
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
            anchor = _record_age_anchor(path)
            if anchor is None:
                continue
            if older_than is not None and now - anchor < older_than:
                continue
            selected.append(path)

    logins_root = root / "logins"
    if logins_root.is_dir():
        for path in sorted(logins_root.glob("l_*.json")):
            if not path.is_file():
                continue
            if _classify_login(path, now=now) != "expired":
                continue
            # Age filter uses expires_at when trustworthy, mtime otherwise;
            # a record whose age cannot be established at all is never reaped.
            anchor = _record_age_anchor(path)
            if anchor is None:
                continue
            if older_than is not None and now - anchor < older_than:
                continue
            login_id = path.stem
            if _attempt_lock_held(logins_root, login_id):
                continue
            for name in (
                f"{login_id}.json",
                f"{login_id}.session",
                f"{login_id}.session-journal",
            ):
                candidate = logins_root / name
                if candidate.is_file():
                    selected.append(candidate)

    clones_root = root / "clones"
    if clones_root.is_dir():
        floor = max(older_than or timedelta(0), MEDIA_CACHE_MIN_AGE)
        caches = [path for path in sorted(clones_root.glob("*-media")) if path.is_dir()]
        # The mtime floor cannot see an upload-only phase, so a held session
        # lock vetoes every cache: some tg run may own one of them.
        if caches and not _any_session_lock_held(root):
            for path in caches:
                # No expires_at — age is always mtime (ADR-0052 media cache).
                anchor = _media_cache_anchor(path)
                if anchor is None or now - anchor < floor:
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
    bytes_total = sum(
        _dir_bytes(path) if path.is_dir() else _file_bytes(path) for path in selected
    )
    names = [path.name for path in selected]
    removed: list[str] = []
    would_remove: list[str] = []
    if confirm:
        for path in selected:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
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
            "session_backups": True,
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
