"""Login-attempt state under `logins/` (ADR-0042).

A login attempt is not a preview: it owns a staged session and optional
`phone_code_hash`, and it is the only writer that may promote into
`sessions/<alias>.session`.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.errors import ConfigError, NotFoundError, PolicyError
from tgcli.session import state_dir

LOGIN_TTL = timedelta(minutes=30)
_LOGIN_ID_RE = re.compile(r"^l_[A-Za-z0-9_-]+$")


def _write_attempt(path: Path, record: dict) -> None:
    """Atomically replace an attempt json at mode 0600.

    A mid-write truncate would otherwise be classified as expired by a
    concurrent `store cleanup --confirm` and take the staged session with it.
    """
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(prefix=".login-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(record))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def logins_dir() -> Path:
    path = state_dir() / "logins"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def staged_session_path(login_id: str) -> Path:
    _validate_id(login_id)
    return logins_dir() / f"{login_id}.session"


def _attempt_path(login_id: str) -> Path:
    _validate_id(login_id)
    return logins_dir() / f"{login_id}.json"


def _validate_id(login_id: str) -> None:
    if "/" in login_id or not _LOGIN_ID_RE.match(login_id):
        raise NotFoundError(f"unknown or invalid login_id: {login_id!r}")


def create_attempt(
    alias: str,
    method: str,
    *,
    api_id: int,
    api_hash: str,
    phone: str | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    login_id = f"l_{secrets.token_urlsafe(16)}"
    record = {
        "login_id": login_id,
        "alias": alias,
        "method": method,
        "api_id": api_id,
        "api_hash": api_hash,
        "phone": phone,
        "phone_code_hash": None,
        "created_at": now.isoformat(),
        "expires_at": (now + LOGIN_TTL).isoformat(),
    }
    path = _attempt_path(login_id)
    _write_attempt(path, record)
    return record


def load_attempt(login_id: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    path = _attempt_path(login_id)
    if not path.is_file():
        raise NotFoundError(f"unknown or expired login_id: {login_id!r}")
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise NotFoundError(f"unknown or expired login_id: {login_id!r}") from exc
    expires_at = datetime.fromisoformat(record["expires_at"])
    if now >= expires_at:
        discard_attempt(login_id)
        raise NotFoundError(f"login_id {login_id!r} expired at {record['expires_at']}")
    return record


def update_attempt(login_id: str, **fields) -> dict:
    record = load_attempt(login_id)
    record.update(fields)
    path = _attempt_path(login_id)
    _write_attempt(path, record)
    return record


def discard_attempt(login_id: str) -> None:
    _validate_id(login_id)
    directory = logins_dir()
    for path in (
        directory / f"{login_id}.json",
        directory / f"{login_id}.session",
        directory / f"{login_id}.session-journal",
    ):
        path.unlink(missing_ok=True)


def promote(
    login_id: str,
    destination: Path,
    *,
    keep_backup: bool,
) -> Path | None:
    """Atomically move the staged session into `destination`.

    The only writer of `sessions/<alias>.session`. Caller must have
    disconnected the Telethon client first so SQLite is closed.
    """
    staged = staged_session_path(login_id)
    if not staged.is_file():
        raise PolicyError(f"staged session missing for {login_id!r}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.with_suffix(".lock")
    lock = lock_path.open("w")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConfigError(
                f"session {destination.stem!r} is busy "
                "(another tg process is using it); retry in a few seconds"
            ) from exc
        backup_path: Path | None = None
        bak = Path(str(destination) + ".bak")
        if destination.exists() and keep_backup:
            os.replace(destination, bak)
            backup_path = bak
            try:
                os.replace(staged, destination)
            except Exception:
                # Restore the working session if the staged move failed.
                if bak.is_file() and not destination.exists():
                    os.replace(bak, destination)
                    backup_path = None
                raise
        else:
            os.replace(staged, destination)
        # Drop the attempt json (and any leftover staged journal) after the
        # session has landed; staged itself is already moved.
        path = _attempt_path(login_id)
        path.unlink(missing_ok=True)
        (logins_dir() / f"{login_id}.session-journal").unlink(missing_ok=True)
        return backup_path
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
