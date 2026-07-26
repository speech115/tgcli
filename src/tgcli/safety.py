"""Local safety gates, write previews, and audit records for mutations."""

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli import atomic
from tgcli.errors import PolicyError
from tgcli.session import ensure_state_dir, restrict_file, state_dir

PREVIEW_TTL = timedelta(minutes=5)


def enforce_mutation_allowed(readonly: bool) -> None:
    """Block a mutation before configuration, sessions, or network work."""
    if readonly or os.environ.get("TGCLI_READONLY") == "1":
        raise PolicyError("mutation blocked by readonly mode")
    if os.environ.get("TGCLI_NO_SEND") == "1":
        raise PolicyError("mutation blocked by TGCLI_NO_SEND")


def enforce_local_mutation_allowed(readonly: bool) -> None:
    """Block a local-state mutation (ADR-0040).

    `TGCLI_NO_SEND` deliberately does not apply: it guards Telegram sends, and
    housekeeping under the state root reaches no network.
    """
    if readonly or os.environ.get("TGCLI_READONLY") == "1":
        raise PolicyError("mutation blocked by readonly mode")


def previews_dir() -> Path:
    return state_dir() / "previews"


def audit_path() -> Path:
    return state_dir() / "audit.jsonl"


def create_preview(payload: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    preview_id = f"p_{secrets.token_urlsafe(16)}"
    expires_at = now + PREVIEW_TTL
    record = {"payload": payload, "expires_at": expires_at.isoformat()}
    directory = ensure_state_dir("previews")
    path = directory / f"{preview_id}.json"
    atomic.replace_text(path, json.dumps(record))
    return {"preview_id": preview_id, "expires_at": record["expires_at"], **payload}


def consume_preview(preview_id: str, *, now: datetime | None = None) -> dict:
    if not preview_id.startswith("p_") or "/" in preview_id:
        raise PolicyError("preview is already used or does not exist")
    path = previews_dir() / f"{preview_id}.json"
    try:
        consumed_path = path.with_suffix(".used")
        path.replace(consumed_path)
        os.chmod(consumed_path, 0o600)
        record = json.loads(consumed_path.read_text())
    except FileNotFoundError:
        raise PolicyError("preview is already used or does not exist") from None
    now = now or datetime.now(UTC)
    if now >= _expires_at(record):
        raise PolicyError("preview has expired")
    return record["payload"]


def _expires_at(record: dict) -> datetime:
    """The record's deadline, or a PolicyError.

    A naive or unparseable stamp (hand edit, older build) must not reach a
    comparison against an aware `now`: a preview whose expiry cannot be
    established is treated as unusable, never as a TypeError crash on the
    commit path.
    """
    try:
        expires = datetime.fromisoformat(record["expires_at"])
    except (KeyError, TypeError, ValueError):
        raise PolicyError("preview is already used or does not exist") from None
    if expires.tzinfo is None or expires.utcoffset() is None:
        raise PolicyError("preview is already used or does not exist")
    return expires


def _validate_preview_kind(payload: dict, expected_kind: str | None) -> None:
    if expected_kind is not None and payload.get("kind") != expected_kind:
        raise PolicyError(f"preview does not match {expected_kind}")


def begin_commit(
    preview_id: str,
    *,
    now: datetime | None = None,
    expected_kind: str | None = None,
) -> dict:
    """Move a preview to .pending and return its payload.

    Unlike consume_preview, a .pending preview may be begun again: the
    stored random_id makes a retried network send idempotent (ADR-0028).
    """
    if not preview_id.startswith("p_") or "/" in preview_id:
        raise PolicyError("preview is already used or does not exist")
    path = previews_dir() / f"{preview_id}.json"
    pending = path.with_suffix(".pending")
    try:
        record = json.loads(path.read_text())
    except FileNotFoundError:
        if not pending.exists():
            raise PolicyError("preview is already used or does not exist") from None
    else:
        _validate_preview_kind(record["payload"], expected_kind)
        try:
            path.replace(pending)
            os.chmod(pending, 0o600)
        except FileNotFoundError:
            if not pending.exists():
                raise PolicyError("preview is already used or does not exist") from None
    try:
        record = json.loads(pending.read_text())
    except FileNotFoundError:
        raise PolicyError("preview is already used or does not exist") from None
    _validate_preview_kind(record["payload"], expected_kind)
    now = now or datetime.now(UTC)
    if now >= _expires_at(record):
        raise PolicyError("preview has expired")
    os.chmod(pending, 0o600)
    return record["payload"]


def finish_commit(preview_id: str) -> None:
    if not preview_id.startswith("p_") or "/" in preview_id:
        raise PolicyError("preview is already used or does not exist")
    pending = previews_dir() / f"{preview_id}.pending"
    try:
        used = pending.with_suffix(".used")
        pending.replace(used)
        os.chmod(used, 0o600)
    except FileNotFoundError:
        pass


def append_audit(action: str, account: str, details: dict) -> None:
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "account": account,
        **details,
    }
    path = audit_path()
    try:
        ensure_state_dir()
        with path.open("a") as handle:
            restrict_file(path)
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise PolicyError(f"cannot write audit record: {exc}") from exc
