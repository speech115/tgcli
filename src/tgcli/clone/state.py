"""Per-clone state with fail-closed validation (ADR-0017/0060).

Public seam: ``CloneState``, ``load``, ``save``, ``supersede``, ``clone_id``,
``path_for``. Storage is SQLite/WAL via ``clone.statedb`` (ADR-0060); a
one-time JSON import renames the legacy file to ``.json.imported``.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.clone import statedb
from tgcli.errors import PolicyError
from tgcli.output import note

# Telegram's longest realistic FloodWait is on the order of a day; kept
# identical to the governor ledger's clamp while both were live.
MAX_COOLDOWN_S = 86_400

VERSION = 2


def clone_id(account_user_id: int, source_peer_id: int) -> str:
    identity = f"{account_user_id}:{source_peer_id}".encode()
    return hashlib.sha256(identity).hexdigest()


def state_dir() -> Path:
    return Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser()


def clones_dir() -> Path:
    return state_dir() / "clones"


def path_for(clone_id: str) -> Path:
    """Active SQLite state path for a clone (ADR-0060)."""
    return clones_dir() / f"{clone_id}.db"


def json_path_for(clone_id: str) -> Path:
    """Legacy v2 JSON path used only for one-time import."""
    return clones_dir() / f"{clone_id}.json"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _valid_id_map(mapping: object, minimum: int) -> bool:
    """A canonical decimal-string source id → TL-int destination id table, with
    both sides at least ``minimum`` and no destination reused across sources."""
    return (
        type(mapping) is dict
        and not any(
            type(k) is not str
            or not k.isascii()
            or not k.isdecimal()
            or k.startswith("0")
            or not minimum <= int(k) <= 2_147_483_647
            or type(v) is not int
            or not minimum <= v <= 2_147_483_647
            for k, v in mapping.items()
        )
        and len(set(mapping.values())) == len(mapping)
    )


@dataclass
class CloneState:
    account_user_id: int
    source_peer_id: int
    source_title: str
    source_kind: str = "broadcast"
    destination_kind: str = "broadcast"
    topic_map: dict[str, int] = field(default_factory=dict)
    version: int = VERSION
    destination_peer_id: int | None = None
    # Last name the destination was seen under, recorded by clone init and
    # clone sync (never by refresh, whose preview must not write state) so
    # the offline `clone status` can say which channel a clone writes to
    # (a private destination has no username to look up). Null until the
    # clone is next touched by a command that resolves the peer.
    destination_title: str | None = None
    destination_username: str | None = None
    creation_marker: str | None = None
    cursor: int = 0
    id_map: dict[str, int] = field(default_factory=dict)
    retry_not_before: str | None = None
    created_at: str = ""
    last_synced_at: str | None = None
    discussion_source_peer_id: int | None = None
    discussion_destination_peer_id: int | None = None
    discussion_linked: bool = False
    discussion_cursor: int = 0
    discussion_id_map: dict[str, int] = field(default_factory=dict)
    comments: str = "none"
    avatar_photo_ids: dict[str, int] = field(default_factory=dict)
    pinned_dest_id: int | None = None
    pin_occupied: bool = False
    _persisted: bool = field(default=False, init=False, repr=False, compare=False)
    _dirty_id_map: set[str] = field(
        default_factory=set, init=False, repr=False, compare=False
    )
    _dirty_discussion_id_map: set[str] = field(
        default_factory=set, init=False, repr=False, compare=False
    )
    _dirty_topic_map: set[str] = field(
        default_factory=set, init=False, repr=False, compare=False
    )
    _dirty_avatar_photo_ids: set[str] = field(
        default_factory=set, init=False, repr=False, compare=False
    )
    _deleted_discussion_id_map: set[str] = field(
        default_factory=set, init=False, repr=False, compare=False
    )

    @classmethod
    def new(
        cls,
        *,
        account_user_id: int,
        source_peer_id: int,
        source_title: str,
        source_kind: str = "broadcast",
    ) -> CloneState:
        return cls(
            account_user_id=account_user_id,
            source_peer_id=source_peer_id,
            source_title=source_title,
            source_kind=source_kind,
            destination_kind="forum" if source_kind == "forum" else "broadcast",
            created_at=datetime.now(UTC).isoformat(),
        )

    @property
    def clone_id(self) -> str:
        return clone_id(self.account_user_id, self.source_peer_id)

    def record_mapping(self, source_id: int, destination_id: int) -> None:
        key = str(source_id)
        self.id_map[key] = destination_id
        self._dirty_id_map.add(key)

    def record_avatar(self, source_peer_id: int, photo_id: int) -> None:
        key = str(source_peer_id)
        self.avatar_photo_ids[key] = photo_id
        self._dirty_avatar_photo_ids.add(key)

    def avatar_for(self, source_peer_id: int) -> int | None:
        return self.avatar_photo_ids.get(str(source_peer_id))

    def dest_for(self, source_id: int) -> int | None:
        return self.id_map.get(str(source_id))

    def record_topic(self, source_topic_id: int, destination_topic_id: int) -> None:
        if (
            type(source_topic_id) is not int
            or type(destination_topic_id) is not int
            or not 2 <= source_topic_id <= 2_147_483_647
            or not 2 <= destination_topic_id <= 2_147_483_647
            or any(
                value == destination_topic_id and key != str(source_topic_id)
                for key, value in self.topic_map.items()
            )
        ):
            raise ValueError("invalid topic mapping")
        key = str(source_topic_id)
        self.topic_map[key] = destination_topic_id
        self._dirty_topic_map.add(key)

    def topic_dest_for(self, source_topic_id: int) -> int | None:
        return self.topic_map.get(str(source_topic_id))

    def record_discussion_mapping(self, source_id: int, destination_id: int) -> None:
        key = str(source_id)
        self.discussion_id_map[key] = destination_id
        self._dirty_discussion_id_map.add(key)

    def discussion_dest_for(self, source_id: int) -> int | None:
        return self.discussion_id_map.get(str(source_id))

    def clear_discussion_progress(self) -> None:
        """Drop phase-2 cursor/map so comments can degrade to unavailable."""
        self._deleted_discussion_id_map.update(self.discussion_id_map)
        self.discussion_id_map.clear()
        self.discussion_cursor = 0

    def max_destination_id(self) -> int | None:
        return max([*self.id_map.values(), *self.topic_map.values()], default=None)

    def max_discussion_destination_id(self) -> int | None:
        return max(self.discussion_id_map.values(), default=None)

    def set_cooldown(self, deadline: datetime) -> None:
        """Persist the per-clone deadline (legacy ADR-0045 field).

        Production no longer writes this field — floods arm the governor's
        per-type cooldown instead (ADR-0072). Kept for old clones' state
        and for tests that stage a cooling clone.
        """
        aware = _require_aware(deadline)
        current = self.cooldown_deadline()
        if current is not None and current > aware:
            aware = current
        self.retry_not_before = aware.isoformat()

    def cooldown_deadline(self) -> datetime | None:
        """The per-clone deadline, clamped like the account-scoped one."""
        if self.retry_not_before is None:
            return None
        deadline = datetime.fromisoformat(self.retry_not_before)
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            return None
        ceiling = datetime.now(UTC) + timedelta(seconds=MAX_COOLDOWN_S)
        return min(deadline.astimezone(UTC), ceiling)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "account_user_id": self.account_user_id,
            "source_peer_id": self.source_peer_id,
            "source_title": self.source_title,
            "source_kind": self.source_kind,
            "destination_kind": self.destination_kind,
            "topic_map": self.topic_map,
            "destination_peer_id": self.destination_peer_id,
            "destination_title": self.destination_title,
            "destination_username": self.destination_username,
            "creation_marker": self.creation_marker,
            "cursor": self.cursor,
            "id_map": self.id_map,
            "retry_not_before": self.retry_not_before,
            "created_at": self.created_at,
            "last_synced_at": self.last_synced_at,
            "discussion_source_peer_id": self.discussion_source_peer_id,
            "discussion_destination_peer_id": self.discussion_destination_peer_id,
            "discussion_linked": self.discussion_linked,
            "discussion_cursor": self.discussion_cursor,
            "discussion_id_map": self.discussion_id_map,
            "comments": self.comments,
            "avatar_photo_ids": self.avatar_photo_ids,
            "pinned_dest_id": self.pinned_dest_id,
            "pin_occupied": self.pin_occupied,
        }

    def _clear_dirty(self) -> None:
        self._dirty_id_map.clear()
        self._dirty_discussion_id_map.clear()
        self._dirty_topic_map.clear()
        self._dirty_avatar_photo_ids.clear()
        self._deleted_discussion_id_map.clear()
        self._persisted = True

    @classmethod
    def from_dict(cls, data: dict) -> CloneState:
        source_kind = data.get("source_kind", "broadcast")
        if source_kind not in {"broadcast", "megagroup", "dialog", "basic", "forum"}:
            raise ValueError("invalid source kind")
        destination_kind = data.get("destination_kind", "broadcast")
        topic_map = data.get("topic_map", {})
        if not _valid_id_map(topic_map, 2):
            raise ValueError("invalid topic map")
        if destination_kind != ("forum" if source_kind == "forum" else "broadcast") or (
            topic_map and source_kind != "forum"
        ):
            raise ValueError("inconsistent forum state")
        comments = data.get("comments", "none")
        discussion_id_map = data.get("discussion_id_map", {})
        discussion_cursor = data.get("discussion_cursor", 0)
        discussion_linked = data.get("discussion_linked", False)
        discussion_source_peer_id = data.get("discussion_source_peer_id")
        discussion_destination_peer_id = data.get("discussion_destination_peer_id")
        if (
            comments not in {"enabled", "unavailable", "none", "disabled"}
            or not _valid_id_map(discussion_id_map, 1)
            or type(discussion_cursor) is not int
            or discussion_cursor < 0
            or type(discussion_linked) is not bool
            or (
                comments == "enabled"
                and (discussion_source_peer_id is None or source_kind != "broadcast")
            )
            or (comments != "enabled" and (discussion_id_map or discussion_cursor))
            or (discussion_linked and discussion_destination_peer_id is None)
        ):
            raise ValueError("inconsistent discussion state")
        pinned_dest_id = data.get("pinned_dest_id")
        pin_occupied = data.get("pin_occupied", False)
        if (
            type(pin_occupied) is not bool
            or (
                pinned_dest_id is not None
                and (
                    type(pinned_dest_id) is not int
                    or pinned_dest_id < 1
                    or pinned_dest_id > 2_147_483_647
                )
            )
            or (pin_occupied and pinned_dest_id is not None)
        ):
            raise ValueError("inconsistent pin state")
        destination_title = data.get("destination_title")
        destination_username = data.get("destination_username")
        if any(
            value is not None and type(value) is not str
            for value in (destination_title, destination_username)
        ):
            raise ValueError("invalid destination name")
        id_map = data.get("id_map", {})
        if not _valid_id_map(id_map, 1):
            raise ValueError("invalid id map")
        retry_not_before = data.get("retry_not_before")
        if retry_not_before is not None:
            try:
                _require_aware(datetime.fromisoformat(retry_not_before))
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid cooldown deadline") from exc
        return cls(
            version=data["version"],
            account_user_id=data["account_user_id"],
            source_peer_id=data["source_peer_id"],
            source_title=data["source_title"],
            source_kind=source_kind,
            destination_kind=destination_kind,
            topic_map=dict(topic_map),
            destination_peer_id=data.get("destination_peer_id"),
            destination_title=destination_title,
            destination_username=destination_username,
            creation_marker=data.get("creation_marker"),
            cursor=data.get("cursor", 0),
            id_map=dict(id_map),
            retry_not_before=retry_not_before,
            created_at=data.get("created_at", ""),
            last_synced_at=data.get("last_synced_at"),
            discussion_source_peer_id=discussion_source_peer_id,
            discussion_destination_peer_id=discussion_destination_peer_id,
            discussion_linked=discussion_linked,
            discussion_cursor=discussion_cursor,
            discussion_id_map=dict(discussion_id_map),
            comments=comments,
            avatar_photo_ids=dict(data.get("avatar_photo_ids", {})),
            pinned_dest_id=pinned_dest_id,
            pin_occupied=pin_occupied,
        )


def _hydrate(data: dict, *, path_name: str) -> CloneState:
    try:
        loaded = CloneState.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyError(
            f"clone state {path_name} is invalid; manual repair is required"
        ) from exc
    loaded._clear_dirty()
    return loaded


def load(clone_id: str) -> CloneState | None:
    db_path = path_for(clone_id)
    json_path = json_path_for(clone_id)
    if db_path.exists() and json_path.exists():
        raise PolicyError(
            f"clone state {clone_id} has both {db_path.name} and {json_path.name}; "
            "manual resolution is required"
        )
    if db_path.exists():
        return _hydrate(statedb.load_dict(db_path), path_name=db_path.name)
    if json_path.exists():
        data = statedb.read_json_file(json_path, expected_version=VERSION)
        loaded = _hydrate(data, path_name=json_path.name)
        statedb.finish_json_import(json_path, db_path, loaded.to_dict())
        note(f"imported clone state {json_path.name} → {db_path.name}")
        return loaded
    return None


def probe(clone_id: str) -> dict:
    """Local status probe: schema version + integrity without raising."""
    return statedb.probe_paths(path_for(clone_id), json_path_for(clone_id))


def supersede(clone_id: str, sidecars: tuple[Path, ...] = ()) -> list[Path]:
    """Archive active state (+ sidecars) out of the slot; renames, never deletes."""
    db_path = path_for(clone_id)
    wal, shm = statedb.sidecar_paths(db_path)
    return statedb.archive_paths(db_path, wal, shm, json_path_for(clone_id), *sidecars)


def save(state: CloneState) -> None:
    """Persist dirty mutations (or a full first write) in one SQLite transaction."""
    clones_dir().mkdir(parents=True, exist_ok=True)
    statedb.persist(
        path_for(state.clone_id),
        state.to_dict(),
        full=not state._persisted,
        dirty_id_map=set(state._dirty_id_map),
        dirty_discussion_id_map=set(state._dirty_discussion_id_map),
        dirty_topic_map=set(state._dirty_topic_map),
        dirty_avatar_photo_ids=set(state._dirty_avatar_photo_ids),
        deleted_discussion_id_map=set(state._deleted_discussion_id_map),
    )
    state._clear_dirty()
