"""Archive scope helpers: standing private category + explicit allowlist."""

from __future__ import annotations

from telethon import utils
from telethon.tl import types

from tgcli.errors import PolicyError


def peer_id(entity) -> int:
    return int(utils.get_peer_id(entity))


def classify_entity(entity) -> str:
    """Return ``user`` | ``group`` | ``channel`` for Telegram entity types."""
    if isinstance(entity, types.User):
        return "user"
    if isinstance(entity, types.Chat):
        return "group"
    if isinstance(entity, types.Channel):
        return "group" if bool(getattr(entity, "megagroup", False)) else "channel"
    raise PolicyError(f"unsupported archive peer type: {type(entity).__name__}")


def entity_title(entity) -> str | None:
    if isinstance(entity, types.User):
        parts = [entity.first_name or "", entity.last_name or ""]
        name = " ".join(part for part in parts if part).strip()
        return name or entity.username
    return getattr(entity, "title", None) or getattr(entity, "username", None)


def require_explicit_kind(kind: str) -> None:
    if kind == "user":
        raise PolicyError(
            "private 1:1 dialogs are always in archive scope; "
            "tg archive add is only for groups and channels"
        )
    if kind not in ("group", "channel"):
        raise PolicyError(f"unsupported archive scope kind: {kind!r}")


def allow_backfill(kind: str, *, explicitly_scoped: bool) -> None:
    if kind == "user":
        return
    if kind in ("group", "channel") and explicitly_scoped:
        return
    raise PolicyError(
        "groups and channels must be added with tg archive add before backfill"
    )
