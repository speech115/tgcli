"""Per-clone state with fail-closed validation (ADR-0017/0060).

Public seam: ``CloneState``, ``load``, ``save``, ``supersede``, ``clone_id``,
``path_for``, ``slot_lock``. Storage is SQLite/WAL via ``clone.statedb``
(ADR-0060).
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from tgcli.clone import statedb
from tgcli.clone.ids import DestCommentId, DestPostId, SourceCommentId, SourcePostId
from tgcli.clone.statedb import MapTable, Pending
from tgcli.errors import PolicyError
from tgcli.output import note
from tgcli.state_paths import ensure_state_dir, state_dir

VERSION = 2
_TL_INT_MAX = 2_147_483_647


# Peer class per source kind (ADR-0103): a User/Chat/Channel can share a
# bare numeric id; not split further, since a megagroup toggling into a
# forum keeps its channel_id and must stay one slot.
_PEER_CLASS = {
    "dialog": "user",
    "basic": "chat",
    "broadcast": "channel",
    "megagroup": "channel",
    "forum": "channel",
}


def clone_id(account_user_id: int, source_peer_id: int, source_kind: str) -> str:
    """Peer-class-aware clone identity (ADR-0103)."""
    peer_class = _PEER_CLASS[source_kind]
    identity = f"{account_user_id}:{peer_class}:{source_peer_id}".encode()
    return hashlib.sha256(identity).hexdigest()


def _kind_blind_clone_id(account_user_id: int, source_peer_id: int) -> str:
    """Pre-ADR-0103 identity; import-only migration lookup."""
    identity = f"{account_user_id}:{source_peer_id}".encode()
    return hashlib.sha256(identity).hexdigest()


def clones_dir() -> Path:
    return state_dir() / "clones"


def ensure_clones_dir() -> Path:
    """`clones/` and the state root, created or repaired to 0700."""
    return ensure_state_dir("clones")


def path_for(clone_id: str) -> Path:
    """Active SQLite state path for a clone (ADR-0060)."""
    return clones_dir() / f"{clone_id}.db"


@contextmanager
def slot_lock(clone_id: str) -> Iterator[None]:
    """One writer per clone slot at a time.

    The session lock is per session file, but a slot is keyed by the account
    user id, so a `--session-role` run and a primary-session run could both
    write one clone and post every message twice. Never waits: the other run
    may be hours long. The `.lock` file stays; it holds no state.
    """
    lock = ensure_clones_dir() / f"{clone_id}.lock"
    with os.fdopen(os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600), "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PolicyError(
                f"clone {clone_id} is busy: another clone sync or init is "
                "running; retry when it finishes"
            ) from None
        yield


def _in_tl_range(pairs: Iterable[tuple[int, int]], minimum: int) -> bool:
    """Both sides of every pair are TL ints of at least ``minimum``."""
    return all(
        minimum <= source <= _TL_INT_MAX and minimum <= dest <= _TL_INT_MAX
        for source, dest in pairs
    )


class Comments(StrEnum):
    """Whether a clone copies its source's discussion group.

    ``enabled``: comments are copied. ``none``: the source has no linked
    group (or unlinked it; a relink resumes). ``unavailable``: Telegram
    refused the group, so the clone degraded to posts only. ``disabled``:
    the owner turned comments off.
    """

    ENABLED = "enabled"
    NONE = "none"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


@dataclass
class CloneState:
    account_user_id: int
    source_peer_id: int
    source_title: str
    source_kind: str = "broadcast"
    destination_kind: str = "broadcast"
    topic_map: dict[int, int] = field(default_factory=dict)
    version: int = VERSION
    destination_peer_id: int | None = None
    # Last name the destination was seen under, recorded by clone init and
    # clone sync so the offline `clone status` can say which channel a clone writes to
    # (a private destination has no username to look up). Null until the
    # clone is next touched by a command that resolves the peer.
    destination_title: str | None = None
    destination_username: str | None = None
    creation_marker: str | None = None
    cursor: int = 0
    id_map: dict[SourcePostId, DestPostId] = field(default_factory=dict)
    # Legacy ADR-0045 deadline: kept and reported, no longer written or enforced.
    retry_not_before: str | None = None
    created_at: str = ""
    last_synced_at: str | None = None
    discussion_source_peer_id: int | None = None
    discussion_destination_peer_id: int | None = None
    discussion_linked: bool = False
    discussion_cursor: int = 0
    discussion_id_map: dict[SourceCommentId, DestCommentId] = field(
        default_factory=dict
    )
    comments: Comments = Comments.NONE
    avatar_photo_ids: dict[int, int] = field(default_factory=dict)
    # Source discussion anchor id -> destination anchor id, so a later run
    # places a comment without re-reading either anchor.
    anchor_map: dict[SourceCommentId, DestCommentId] = field(default_factory=dict)
    pinned_dest_id: int | None = None
    pin_occupied: bool = False
    # Map writes since the last save; ``save`` applies them in order. A state
    # that was never saved starts empty, so its pending list is its whole map.
    _pending: list[Pending] = field(
        default_factory=list, init=False, repr=False, compare=False
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
        return clone_id(self.account_user_id, self.source_peer_id, self.source_kind)

    def record_mapping(
        self, source_id: SourcePostId, destination_id: DestPostId
    ) -> None:
        self.id_map[source_id] = destination_id
        self._pending.append((MapTable.ID_MAP, source_id, destination_id))

    def record_avatar(self, source_peer_id: int, photo_id: int) -> None:
        self.avatar_photo_ids[source_peer_id] = photo_id
        self._pending.append((MapTable.AVATAR_PHOTO_IDS, source_peer_id, photo_id))

    def avatar_for(self, source_peer_id: int) -> int | None:
        return self.avatar_photo_ids.get(source_peer_id)

    def record_anchor(
        self, source_anchor_id: SourceCommentId, destination_anchor_id: DestCommentId
    ) -> None:
        self.anchor_map[source_anchor_id] = destination_anchor_id
        self._pending.append(
            (MapTable.ANCHOR_MAP, source_anchor_id, destination_anchor_id)
        )

    def anchor_dest_for(
        self, source_anchor_id: SourceCommentId
    ) -> DestCommentId | None:
        return self.anchor_map.get(source_anchor_id)

    def dest_for(self, source_id: SourcePostId) -> DestPostId | None:
        return self.id_map.get(source_id)

    def record_topic(self, source_topic_id: int, destination_topic_id: int) -> None:
        if (
            type(source_topic_id) is not int
            or type(destination_topic_id) is not int
            or not 2 <= source_topic_id <= _TL_INT_MAX
            or not 2 <= destination_topic_id <= _TL_INT_MAX
            or any(
                value == destination_topic_id and key != source_topic_id
                for key, value in self.topic_map.items()
            )
        ):
            raise ValueError("invalid topic mapping")
        self.topic_map[source_topic_id] = destination_topic_id
        self._pending.append(
            (MapTable.TOPIC_MAP, source_topic_id, destination_topic_id)
        )

    def topic_dest_for(self, source_topic_id: int) -> int | None:
        return self.topic_map.get(source_topic_id)

    def record_discussion_mapping(
        self, source_id: SourceCommentId, destination_id: DestCommentId
    ) -> None:
        self.discussion_id_map[source_id] = destination_id
        self._pending.append((MapTable.DISCUSSION_ID_MAP, source_id, destination_id))

    def discussion_dest_for(self, source_id: SourceCommentId) -> DestCommentId | None:
        return self.discussion_id_map.get(source_id)

    def clear_discussion_progress(self) -> None:
        """Drop phase-2 cursor/map so comments can degrade to unavailable."""
        self._pending.extend(
            (MapTable.DISCUSSION_ID_MAP, source, None)
            for source in self.discussion_id_map
        )
        self.discussion_id_map.clear()
        self.discussion_cursor = 0

    def max_destination_id(self) -> int | None:
        return max([*self.id_map.values(), *self.topic_map.values()], default=None)

    def max_discussion_destination_id(self) -> int | None:
        return max(self.discussion_id_map.values(), default=None)


def _check_discussion(state: CloneState) -> None:
    """Phase-2 progress lives only on an enabled clone, or on a linked one
    whose source unlinked its group for a while ("none"): a relink of that
    group resumes the comments where they stopped."""
    progress = state.discussion_id_map or state.discussion_cursor
    resumable = (
        state.comments == Comments.NONE
        and state.discussion_linked
        and state.discussion_source_peer_id is not None
        and state.source_kind == "broadcast"
    )
    if (
        state.discussion_cursor < 0
        or not _in_tl_range(state.discussion_id_map.items(), 1)
        or (
            state.comments == Comments.ENABLED
            and (
                state.discussion_source_peer_id is None
                or state.source_kind != "broadcast"
            )
        )
        or (progress and state.comments != Comments.ENABLED and not resumable)
        or (state.discussion_linked and state.discussion_destination_peer_id is None)
    ):
        raise ValueError("inconsistent discussion state")


def _check(state: CloneState) -> None:
    """The invariants the schema cannot express; a state failing one is never
    handed to a caller (ADR-0017)."""
    if state.source_kind not in _PEER_CLASS:
        raise ValueError("invalid source kind")
    if not _in_tl_range(state.topic_map.items(), 2):
        raise ValueError("invalid topic map")
    if state.destination_kind != (
        "forum" if state.source_kind == "forum" else "broadcast"
    ) or (state.topic_map and state.source_kind != "forum"):
        raise ValueError("inconsistent forum state")
    _check_discussion(state)
    if (
        state.pinned_dest_id is not None
        and not 1 <= state.pinned_dest_id <= _TL_INT_MAX
    ) or (state.pin_occupied and state.pinned_dest_id is not None):
        raise ValueError("inconsistent pin state")
    if not _in_tl_range(state.id_map.items(), 1) or not _in_tl_range(
        state.anchor_map.items(), 1
    ):
        raise ValueError("invalid id map")
    if (
        state.retry_not_before is not None
        and datetime.fromisoformat(state.retry_not_before).utcoffset() is None
    ):
        raise ValueError("invalid cooldown deadline")


def _from_rows(
    meta: dict[str, Any], maps: dict[MapTable, dict[int, int]]
) -> CloneState:
    loaded = CloneState(
        **{
            **meta,
            "comments": Comments(meta["comments"]),
            "discussion_linked": bool(meta["discussion_linked"]),
            "pin_occupied": bool(meta["pin_occupied"]),
        },
        topic_map=maps[MapTable.TOPIC_MAP],
        id_map=cast("dict[SourcePostId, DestPostId]", maps[MapTable.ID_MAP]),
        discussion_id_map=cast(
            "dict[SourceCommentId, DestCommentId]", maps[MapTable.DISCUSSION_ID_MAP]
        ),
        avatar_photo_ids=maps[MapTable.AVATAR_PHOTO_IDS],
        anchor_map=cast(
            "dict[SourceCommentId, DestCommentId]", maps[MapTable.ANCHOR_MAP]
        ),
    )
    _check(loaded)
    return loaded


def load(clone_id: str) -> CloneState | None:
    """The slot's state, or None when the slot has no database."""
    db_path = path_for(clone_id)
    if not db_path.exists():
        return None
    try:
        return _from_rows(*statedb.load(db_path))
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyError(
            f"clone state {db_path.name} is invalid; manual repair is required"
        ) from exc


def resolve_slot(account_user_id: int, source_peer_id: int, source_kind: str) -> str:
    """Canonical clone id, migrating a same-class legacy slot onto it once
    (ADR-0103); a different-class legacy slot is left for its own class."""
    canonical = clone_id(account_user_id, source_peer_id, source_kind)
    legacy = _kind_blind_clone_id(account_user_id, source_peer_id)
    canonical_taken = path_for(canonical).exists()
    legacy_present = path_for(legacy).exists()
    if legacy == canonical or canonical_taken or not legacy_present:
        return canonical
    try:
        legacy_state = load(legacy)
    except PolicyError:
        return canonical  # corrupt legacy slot: left for manual repair
    same_class = legacy_state and _PEER_CLASS.get(
        legacy_state.source_kind
    ) == _PEER_CLASS.get(source_kind)
    if not same_class:
        return canonical
    legacy_db, canonical_db = path_for(legacy), path_for(canonical)
    os.replace(legacy_db, canonical_db)
    for src, dst in zip(
        statedb.sidecar_paths(legacy_db),
        statedb.sidecar_paths(canonical_db),
        strict=True,
    ):
        if src.exists():
            os.replace(src, dst)
    note(f"migrated clone state {legacy} -> {canonical} (ADR-0103)")
    return canonical


def probe(clone_id: str) -> dict:
    """Local status probe: schema version + integrity without raising."""
    return statedb.probe_path(path_for(clone_id))


def supersede(clone_id: str, sidecars: tuple[Path, ...] = ()) -> list[Path]:
    """Archive active state (+ sidecars) out of the slot; renames, never deletes."""
    db_path = path_for(clone_id)
    wal, shm = statedb.sidecar_paths(db_path)
    return statedb.archive_paths(db_path, wal, shm, *sidecars)


def save(state: CloneState) -> None:
    """Persist the meta row and every pending map write in one transaction."""
    ensure_clones_dir()
    meta = {name: getattr(state, name) for name in statedb.META_FIELDS}
    statedb.persist(path_for(state.clone_id), meta, state._pending)
    state._pending.clear()
