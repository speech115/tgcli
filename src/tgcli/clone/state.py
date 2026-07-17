"""Per-clone JSON state with fail-closed validation (ADR-0017)."""
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from tgcli.errors import PolicyError
VERSION = 2
def clone_id(account_user_id: int, source_peer_id: int) -> str:
    identity = f"{account_user_id}:{source_peer_id}".encode()
    return hashlib.sha256(identity).hexdigest()
def state_dir() -> Path:
    return Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser()
def clones_dir() -> Path:
    return state_dir() / "clones"
def path_for(clone_id: str) -> Path:
    return clones_dir() / f"{clone_id}.json"
def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)
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
    @classmethod
    def new(
        cls, *, account_user_id: int, source_peer_id: int, source_title: str,
        source_kind: str = "broadcast"
    ) -> "CloneState":
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
        self.id_map[str(source_id)] = destination_id
    def dest_for(self, source_id: int) -> int | None:
        return self.id_map.get(str(source_id))
    def record_topic(self, source_topic_id: int, destination_topic_id: int) -> None:
        if (type(source_topic_id) is not int or type(destination_topic_id) is not int
                or not 2 <= source_topic_id <= 2_147_483_647
                or not 2 <= destination_topic_id <= 2_147_483_647
                or any(value == destination_topic_id and key != str(source_topic_id)
                       for key, value in self.topic_map.items())):
            raise ValueError("invalid topic mapping")
        self.topic_map[str(source_topic_id)] = destination_topic_id
    def topic_dest_for(self, source_topic_id: int) -> int | None:
        return self.topic_map.get(str(source_topic_id))
    def record_discussion_mapping(self, source_id: int, destination_id: int) -> None:
        self.discussion_id_map[str(source_id)] = destination_id
    def discussion_dest_for(self, source_id: int) -> int | None:
        return self.discussion_id_map.get(str(source_id))
    def max_destination_id(self) -> int | None:
        return max([*self.id_map.values(), *self.topic_map.values()], default=None)
    def max_discussion_destination_id(self) -> int | None:
        return max(self.discussion_id_map.values(), default=None)
    def set_cooldown(self, deadline: datetime) -> None:
        self.retry_not_before = _require_aware(deadline).isoformat()
    def cooldown_deadline(self) -> datetime | None:
        return None if self.retry_not_before is None else datetime.fromisoformat(self.retry_not_before)
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
        }
    @classmethod
    def from_dict(cls, data: dict) -> "CloneState":
        source_kind = data.get("source_kind", "broadcast")
        if source_kind not in {"broadcast", "megagroup", "dialog", "basic", "forum"}:
            raise ValueError("invalid source kind")
        destination_kind = data.get("destination_kind", "broadcast")
        topic_map = data.get("topic_map", {})
        if type(topic_map) is not dict or any(type(k) is not str or not k.isascii() or not k.isdecimal() or k.startswith("0") or not 2 <= int(k) <= 2_147_483_647 or type(v) is not int or not 2 <= v <= 2_147_483_647 for k, v in topic_map.items()) or len(set(topic_map.values())) != len(topic_map):
            raise ValueError("invalid topic map")
        if destination_kind != ("forum" if source_kind == "forum" else "broadcast") or (topic_map and source_kind != "forum"):
            raise ValueError("inconsistent forum state")
        comments = data.get("comments", "none")
        discussion_id_map = data.get("discussion_id_map", {})
        discussion_cursor = data.get("discussion_cursor", 0)
        discussion_linked = data.get("discussion_linked", False)
        discussion_source_peer_id = data.get("discussion_source_peer_id")
        discussion_destination_peer_id = data.get("discussion_destination_peer_id")
        if comments not in {"enabled", "unavailable", "none"} or type(discussion_id_map) is not dict or any(type(k) is not str or not k.isascii() or not k.isdecimal() or k.startswith("0") or not 1 <= int(k) <= 2_147_483_647 or type(v) is not int or not 1 <= v <= 2_147_483_647 for k, v in discussion_id_map.items()) or len(set(discussion_id_map.values())) != len(discussion_id_map) or type(discussion_cursor) is not int or discussion_cursor < 0 or type(discussion_linked) is not bool or (comments == "enabled" and (discussion_source_peer_id is None or source_kind != "broadcast")) or (comments != "enabled" and (discussion_id_map or discussion_cursor)) or (discussion_linked and discussion_destination_peer_id is None):
            raise ValueError("inconsistent discussion state")
        return cls(
            version=data["version"],
            account_user_id=data["account_user_id"],
            source_peer_id=data["source_peer_id"],
            source_title=data["source_title"],
            source_kind=source_kind,
            destination_kind=destination_kind,
            topic_map=dict(topic_map),
            destination_peer_id=data.get("destination_peer_id"),
            creation_marker=data.get("creation_marker"),
            cursor=data.get("cursor", 0),
            id_map=dict(data.get("id_map", {})),
            retry_not_before=data.get("retry_not_before"),
            created_at=data.get("created_at", ""),
            last_synced_at=data.get("last_synced_at"),
            discussion_source_peer_id=discussion_source_peer_id,
            discussion_destination_peer_id=discussion_destination_peer_id,
            discussion_linked=discussion_linked,
            discussion_cursor=discussion_cursor,
            discussion_id_map=dict(discussion_id_map),
            comments=comments,
        )
def load(clone_id: str) -> CloneState | None:
    path = path_for(clone_id)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyError(
            f"clone state {path.name} is corrupted; manual repair is required"
        ) from exc
    if data.get("version") != VERSION:
        raise PolicyError(
            f"clone state {path.name} has unsupported version "
            f"{data.get('version')!r}; expected {VERSION}"
        )
    try:
        return CloneState.from_dict(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyError(
            f"clone state {path.name} is invalid; manual repair is required"
        ) from exc
def supersede(clone_id: str, sidecars: tuple[Path, ...] = ()) -> list[Path]:
    """Archive a clone's active state (plus any caller-supplied sidecar files)
    out of the slot so a fresh `init --replace` can start clean. Renames, never
    deletes: the old clone stays recoverable and its Telegram destination is
    untouched. Returns the archived paths ([] if the slot was empty)."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archived = []
    for path in (path_for(clone_id), *sidecars):
        if path.exists():
            target = path.with_name(f"{path.name}.superseded-{stamp}")
            os.replace(path, target)
            archived.append(target)
    return archived
def save(state: CloneState) -> None:
    directory = clones_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = path_for(state.clone_id)
    fd, tmp = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state.to_dict(), handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
