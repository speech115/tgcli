"""A clone world whose Telegram fails on schedule.

``FaultyWorldClient`` is ``clone_world.WorldClient`` with a fault plan: the
n-th operation the run makes against Telegram (an RPC, a history item, a
destination tail read, an entity lookup) fails the way Telegram or the
network fails. Faults come in two families:

- *before*: Telegram never ran the request (FloodWait, a 5xx, a timeout, a
  dropped connection, or the process killed while it waited);
- *after*: Telegram ran a send, then the answer was lost (the connection
  dropped, the process died) or came back without the confirmations the
  clone matches by ``random_id``.

``created`` records every destination message Telegram made, as
(chat, source id, destination id), seeded with the earlier run's copies: it is
the truth the clone's own state is checked against.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from telethon import errors as telethon_errors
from telethon.tl import types

from tests.fakes import clone_world
from tests.fakes.clone import message


class Killed(BaseException):
    """The process died: no handler of the code under test runs."""


BEFORE = ("flood", "premium_flood", "server", "timeout", "connection", "kill")
AFTER = ("lost", "kill_after", "too_long", "unconfirmed")
KINDS = BEFORE + AFTER


def _raise_before(kind: str):
    if kind == "flood":
        raise telethon_errors.FloodWaitError(request=None, capture=30)
    if kind == "premium_flood":
        raise telethon_errors.FloodPremiumWaitError(request=None, capture=30)
    if kind == "server":
        raise telethon_errors.ServerError(request=None, message="INTERNAL", code=500)
    if kind == "timeout":
        raise telethon_errors.TimedOutError(request=None, message="TIMEOUT", code=-503)
    if kind == "connection":
        raise ConnectionError("connection lost")
    raise Killed(kind)


# An after-fault on an operation that sends nothing is the matching
# before-fault: there is no answer to lose.
_AS_BEFORE = {
    "lost": "connection",
    "kill_after": "kill",
    "too_long": "server",
    "unconfirmed": "server",
}


class FaultyWorldClient(clone_world.WorldClient):
    def __init__(self, messages, comments=()):
        super().__init__(messages, comments)
        self.plan: dict[int, str] = {}
        self.send_plan: dict[int, str] = {}
        self.operations = 0
        self.sends = 0
        self.fired: list[str] = []
        self.created: list[tuple[str, int, int]] = []
        self.log: list[str] = []

    def arm(self, plan: dict[int, str], sends: dict[int, str] | None = None) -> None:
        """Count afresh; fail the operations ``plan`` names and the sends
        ``sends`` names (n-th operation, n-th send, from 0)."""
        self.plan = dict(plan)
        self.send_plan = dict(sends or {})
        self.operations = 0
        self.sends = 0
        self.fired = []
        self.log = []

    def seed_created(self) -> None:
        self.created = [
            (chat, source_id, dest_id)
            for (chat, source_id), dest_id in sorted(self.copies.items())
        ]

    def _next(self, what: str, *, sends: bool = False) -> str | None:
        """The fault planned for this operation, raised when it is a before."""
        self.log.append(what)
        kind = self.plan.get(self.operations)
        self.operations += 1
        if sends:
            kind = self.send_plan.get(self.sends, kind)
            self.sends += 1
        if kind is None:
            return None
        if not sends:
            kind = _AS_BEFORE.get(kind, kind)
        self.fired.append(kind)
        if kind in BEFORE:
            _raise_before(kind)
        return kind

    async def get_entity(self, ref):
        self._next(f"get_entity {ref!r}")
        return await super().get_entity(ref)

    async def get_messages(self, entity, limit=None, ids=None):
        self._next(f"get_messages {getattr(entity, 'id', entity)} {limit} {ids}")
        return await super().get_messages(entity, limit=limit, ids=ids)

    async def iter_messages(self, entity, *, min_id=0, reverse=False, wait_time=None):
        async for item in super().iter_messages(entity, min_id=min_id, reverse=reverse):
            self._next(f"history {getattr(entity, 'id', entity)} #{item.id}")
            yield item

    async def __call__(self, request):
        after = self._next(
            type(request).__name__, sends=isinstance(request, clone_world.SENDS)
        )
        response = await super().__call__(request)
        if isinstance(request, clone_world.SENDS):
            peer = getattr(request, "peer", None) or getattr(request, "to_peer", None)
            chat = "group" if peer is self.destination_group else "channel"
            assigned = {
                update.random_id: update.id
                for update in response.updates
                if isinstance(update, types.UpdateMessageID)
            }
            for source_id, random_id in clone_world._sources(request):
                dest_id = assigned[random_id]
                self.created.append((chat, source_id, dest_id))
                if chat == "group":
                    # What the group really holds: the fake would otherwise
                    # show every unplanted id as one of Telegram's anchors.
                    self.group_tail[dest_id] = message(
                        dest_id,
                        chat=self.destination_group,
                        message=clone_world._text(source_id),
                    )
        if after == "lost":
            raise ConnectionError("connection lost after Telegram ran the send")
        if after == "kill_after":
            raise Killed(after)
        if after == "too_long":
            return types.UpdatesTooLong()
        if after == "unconfirmed":
            return SimpleNamespace(
                updates=[
                    update
                    for update in response.updates
                    if not isinstance(update, types.UpdateMessageID)
                ]
            )
        return response


def _peer_chat(client, peer) -> str | None:
    """Which destination chat an input peer names, ``None`` for the request's own."""
    if peer is None:
        return None
    if peer is client.destination_group:
        return "group"
    if peer is client.destination:
        return "channel"
    channel_id = getattr(peer, "channel_id", getattr(peer, "id", None))
    return {
        clone_world.DESTINATION_CHANNEL: "channel",
        clone_world.DESTINATION_GROUP: "group",
    }.get(channel_id, f"peer {channel_id}")


def shape(client, recorded) -> dict[tuple[str, int], tuple]:
    """Each recorded copy as source-space facts a fault must not change.

    For every (chat, source) the clone recorded: the request kind that made
    it, the body it carries, and what its reply header points at, named by
    source message (``("anchor", post)`` for Telegram's anchor of a post) so
    two runs that gave different destination ids compare equal.
    """
    by_dest = {(chat, dest): source for chat, source, dest in recorded}
    anchors = {anchor_id: dest for dest, anchor_id in client.anchor_ids.items()}

    def named(chat: str, dest_id: int | None):
        if dest_id is None:
            return None
        if (chat, dest_id) in by_dest:
            return (chat, by_dest[(chat, dest_id)])
        if chat == "group" and dest_id in anchors:
            return ("anchor", by_dest.get(("channel", anchors[dest_id])))
        return (chat, f"unknown {dest_id}")

    facts = {}
    for chat, source, dest in recorded:
        request = client.sent.get((chat, dest))
        if request is None:  # copied by the earlier run the world seeded
            continue
        bodies = getattr(request, "multi_media", None) or [request]
        body = next((item for item in bodies if source_text(item) == source), bodies[0])
        reply = getattr(request, "reply_to", None)
        target = top = None
        if reply is not None:
            space = _peer_chat(client, getattr(reply, "reply_to_peer_id", None))
            target = named(space or chat, getattr(reply, "reply_to_msg_id", None))
            top = named(chat, getattr(reply, "top_msg_id", None))
        facts[(chat, source)] = (
            type(request).__name__,
            getattr(body, "message", None),
            target,
            top,
        )
    return facts


def source_text(body) -> int | None:
    """The source message a reuploaded body names (``clone_world._text``)."""
    found = re.search(r"source message #(\d+)", getattr(body, "message", None) or "")
    return None if found is None else int(found.group(1))
