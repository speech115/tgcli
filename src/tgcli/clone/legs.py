"""One source->destination leg of a clone: posts, or the discussion group."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from tgcli.clone import state
from tgcli.clone.ids import DestCommentId, DestPostId, SourceCommentId, SourcePostId

# ADR-0051: posts/comments interleave in fixed windows (not a flag).
WINDOW = 50

LegKind = Literal["posts", "discussion"]


@dataclass
class Leg[S: (SourcePostId, SourceCommentId), D: (DestPostId, DestCommentId)](ABC):
    """The view of CloneState that batching/transport/replies read. Cursor and
    id-map writes land on the underlying CloneState; callers still save the
    CloneState itself. A leg knows its kind and its other leg, so code that
    crosses legs asks ``leg.other`` instead of naming CloneState fields."""

    clone_state: state.CloneState
    kind: ClassVar[LegKind]

    @property
    @abstractmethod
    def source_kind(self) -> str: ...

    @property
    @abstractmethod
    def destination_kind(self) -> str: ...

    @property
    @abstractmethod
    def source_peer_id(self) -> int | None: ...

    @property
    @abstractmethod
    def other(self) -> Leg[Any, Any]: ...

    @abstractmethod
    def dest_for(self, source_id: S) -> D | None: ...

    @abstractmethod
    def record_mapping(self, source_id: S, destination_id: D) -> None: ...

    @property
    @abstractmethod
    def cursor(self) -> int: ...

    @cursor.setter
    @abstractmethod
    def cursor(self, value: int) -> None: ...

    @property
    def clone_id(self) -> str:
        return self.clone_state.clone_id


@dataclass
class PostsLeg(Leg[SourcePostId, DestPostId]):
    kind: ClassVar[LegKind] = "posts"

    @property
    def source_kind(self) -> str:
        return self.clone_state.source_kind

    @property
    def destination_kind(self) -> str:
        return self.clone_state.destination_kind

    @property
    def source_peer_id(self) -> int | None:
        return self.clone_state.source_peer_id

    @property
    def other(self) -> DiscussionLeg:
        return DiscussionLeg(self.clone_state)

    def dest_for(self, source_id: SourcePostId) -> DestPostId | None:
        return self.clone_state.dest_for(source_id)

    def record_mapping(
        self, source_id: SourcePostId, destination_id: DestPostId
    ) -> None:
        self.clone_state.record_mapping(source_id, destination_id)

    @property
    def cursor(self) -> int:
        return self.clone_state.cursor

    @cursor.setter
    def cursor(self, value: int) -> None:
        self.clone_state.cursor = value


@dataclass
class DiscussionLeg(Leg[SourceCommentId, DestCommentId]):
    kind: ClassVar[LegKind] = "discussion"

    @property
    def source_kind(self) -> str:
        return "megagroup"

    @property
    def destination_kind(self) -> str:
        return "megagroup"

    @property
    def source_peer_id(self) -> int | None:
        return self.clone_state.discussion_source_peer_id

    @property
    def other(self) -> PostsLeg:
        return PostsLeg(self.clone_state)

    def dest_for(self, source_id: SourceCommentId) -> DestCommentId | None:
        return self.clone_state.discussion_dest_for(source_id)

    def record_mapping(
        self, source_id: SourceCommentId, destination_id: DestCommentId
    ) -> None:
        self.clone_state.record_discussion_mapping(source_id, destination_id)

    @property
    def cursor(self) -> int:
        return self.clone_state.discussion_cursor

    @cursor.setter
    def cursor(self, value: int) -> None:
        self.clone_state.discussion_cursor = value


def posts(clone_state: state.CloneState) -> PostsLeg:
    return PostsLeg(clone_state)


def discussion(clone_state: state.CloneState) -> DiscussionLeg:
    return DiscussionLeg(clone_state)
