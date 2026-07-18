"""Local safety gates, write previews, and audit records for mutations."""

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.errors import PolicyError
from tgcli.session import state_dir


PREVIEW_TTL = timedelta(minutes=5)


def enforce_mutation_allowed(readonly: bool) -> None:
    """Block a mutation before configuration, sessions, or network work."""
    if readonly or os.environ.get("TGCLI_READONLY") == "1":
        raise PolicyError("mutation blocked by readonly mode")
    if os.environ.get("TGCLI_NO_SEND") == "1":
        raise PolicyError("mutation blocked by TGCLI_NO_SEND")


def previews_dir() -> Path:
    return state_dir() / "previews"


def audit_path() -> Path:
    return state_dir() / "audit.jsonl"


def create_preview(payload: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    preview_id = f"p_{secrets.token_urlsafe(16)}"
    expires_at = now + PREVIEW_TTL
    record = {"payload": payload, "expires_at": expires_at.isoformat()}
    directory = previews_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{preview_id}.json").write_text(json.dumps(record))
    return {"preview_id": preview_id, "expires_at": record["expires_at"], **payload}


def consume_preview(preview_id: str, *, now: datetime | None = None) -> dict:
    if not preview_id.startswith("p_") or "/" in preview_id:
        raise PolicyError("preview is already used or does not exist")
    path = previews_dir() / f"{preview_id}.json"
    try:
        consumed_path = path.with_suffix(".used")
        path.replace(consumed_path)
        record = json.loads(consumed_path.read_text())
    except FileNotFoundError:
        raise PolicyError("preview is already used or does not exist") from None
    now = now or datetime.now(UTC)
    if now >= datetime.fromisoformat(record["expires_at"]):
        raise PolicyError("preview has expired")
    return record["payload"]


def begin_commit(preview_id: str, *, now: datetime | None = None) -> dict:
    """Move a preview to .pending and return its payload.

    Unlike consume_preview, a .pending preview may be begun again: the
    stored random_id makes a retried network send idempotent (ADR-0028).
    """
    if not preview_id.startswith("p_") or "/" in preview_id:
        raise PolicyError("preview is already used or does not exist")
    path = previews_dir() / f"{preview_id}.json"
    pending = path.with_suffix(".pending")
    try:
        path.replace(pending)
    except FileNotFoundError:
        if not pending.exists():
            raise PolicyError("preview is already used or does not exist") from None
    record = json.loads(pending.read_text())
    now = now or datetime.now(UTC)
    if now >= datetime.fromisoformat(record["expires_at"]):
        raise PolicyError("preview has expired")
    return record["payload"]


def finish_commit(preview_id: str) -> None:
    pending = previews_dir() / f"{preview_id}.pending"
    try:
        pending.replace(pending.with_suffix(".used"))
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
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise PolicyError(f"cannot write audit record: {exc}") from exc
