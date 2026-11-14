import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tests.conftest import TRANSIENT_ERRORS
from tgcli.clone import roster, state


def user(uid, **overrides):
    values = {
        "id": uid,
        "username": None,
        "first_name": f"U{uid}",
        "last_name": None,
        "phone": None,
        "bot": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeTg:
    """Minimal duck-typed client for roster.collect."""

    def __init__(
        self,
        *,
        source=(),
        discussion=(),
        source_error=None,
        discussion_error=None,
        discussion_entity=True,
        entity_error=None,
    ):
        self._source = list(source)
        self._discussion = list(discussion)
        self._source_error = source_error
        self._discussion_error = discussion_error
        self._discussion_entity = discussion_entity
        self._entity_error = entity_error
        self.source_entity = SimpleNamespace(id=123)
        self.group_entity = SimpleNamespace(id=55)

    async def get_entity(self, peer):
        assert isinstance(peer, types.PeerChannel)
        if self._entity_error is not None:
            raise self._entity_error
        if not self._discussion_entity:
            raise ValueError("unresolved")
        return self.group_entity

    async def iter_participants(self, entity, limit=None):
        if entity is self.source_entity:
            error, people = self._source_error, self._source
        else:
            error, people = self._discussion_error, self._discussion
        emitted = 0
        for person in people:
            if error is not None and emitted == 1:
                raise error
            yield person
            emitted += 1
        if error is not None and not people:
            raise error


def seed(comments="none"):
    s = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    s.destination_peer_id = 999
    if comments == "enabled":
        s.comments = "enabled"
        s.discussion_source_peer_id = 55
        s.discussion_destination_peer_id = 888
        s.discussion_linked = True
    return s


def run(tg, clone_state):
    return asyncio.run(roster.collect(tg, clone_state, tg.source_entity))


def read_lines(clone_state):
    path = roster.path_for(clone_state.clone_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_collect_writes_source_rows_and_reports_counts():
    clone_state = seed()
    tg = FakeTg(source=[user(1, username="a"), user(2)])
    result = run(tg, clone_state)
    assert result["source"]["status"] == "collected"
    assert result["source"]["count"] == 2
    assert result["discussion"]["status"] == "none"
    lines = read_lines(clone_state)
    assert [row["peer"] for row in lines] == ["source", "source"]
    assert lines[0] == {
        "peer": "source",
        "id": 1,
        "username": "a",
        "first_name": "U1",
        "last_name": None,
        "phone": None,
        "is_bot": False,
    }


def test_collect_marks_channel_unavailable_when_not_admin():
    clone_state = seed()
    tg = FakeTg(source_error=telethon_errors.ChatAdminRequiredError(request=None))
    result = run(tg, clone_state)
    assert result["source"]["status"] == "unavailable"
    assert result["source"]["count"] == 0
    assert read_lines(clone_state) == []


@pytest.mark.parametrize("make_error", TRANSIENT_ERRORS)
def test_collect_defers_on_a_transient_error_and_discards_partial(make_error):
    clone_state = seed()
    tg = FakeTg(source=[user(1), user(2), user(3)], source_error=make_error())
    result = run(tg, clone_state)
    assert result["source"]["status"] == "deferred"
    assert result["source"]["count"] == 0
    assert read_lines(clone_state) == []


def test_collect_gathers_discussion_group_when_comments_enabled():
    clone_state = seed(comments="enabled")
    tg = FakeTg(
        source_error=telethon_errors.ChatAdminRequiredError(request=None),
        discussion=[user(10, username="z"), user(11)],
    )
    result = run(tg, clone_state)
    assert result["source"]["status"] == "unavailable"
    assert result["discussion"]["status"] == "collected"
    assert result["discussion"]["count"] == 2
    assert result["discussion"]["peer_id"] == 55
    lines = read_lines(clone_state)
    assert [row["peer"] for row in lines] == ["discussion", "discussion"]


def test_collect_marks_discussion_unavailable_when_entity_unresolved():
    clone_state = seed(comments="enabled")
    tg = FakeTg(source=[user(1)], discussion_entity=False)
    result = run(tg, clone_state)
    assert result["source"]["status"] == "collected"
    assert result["discussion"]["status"] == "unavailable"


@pytest.mark.parametrize(
    "error",
    [
        telethon_errors.ChannelPrivateError(request=None),
        telethon_errors.ChatForbiddenError(request=None),
        telethon_errors.ChatAdminRequiredError(request=None),
        telethon_errors.ChannelInvalidError(request=None),
    ],
)
def test_collect_marks_discussion_unavailable_when_group_turned_private(error):
    """Telegram refuses to name the peer with an RPCError, not a ValueError.
    The roster is a best-effort snapshot after the copying is done — an
    access refusal is a marker, never an exception out of `collect`."""
    clone_state = seed(comments="enabled")
    tg = FakeTg(source=[user(1)], entity_error=error)
    result = run(tg, clone_state)
    assert result["source"]["status"] == "collected"
    assert result["discussion"]["status"] == "unavailable"
    assert result["discussion"]["reason"] == type(error).__name__
    assert result["discussion"]["count"] == 0
    # the finished message sync still reports its own rows
    assert [row["id"] for row in read_lines(clone_state)] == [1]


@pytest.mark.parametrize("make_error", TRANSIENT_ERRORS)
def test_collect_defers_discussion_when_entity_resolve_fails_transiently(
    make_error,
):
    clone_state = seed(comments="enabled")
    error = make_error()
    tg = FakeTg(source=[user(1)], entity_error=error)
    result = run(tg, clone_state)
    assert result["discussion"]["status"] == "deferred"
    assert result["discussion"]["reason"] == (
        "flood_wait"
        if isinstance(
            error,
            (telethon_errors.FloodWaitError, telethon_errors.FloodPremiumWaitError),
        )
        else type(error).__name__
    )


def test_collect_overwrites_previous_snapshot():
    clone_state = seed()
    run(FakeTg(source=[user(1), user(2)]), clone_state)
    run(FakeTg(source=[user(9)]), clone_state)
    lines = read_lines(clone_state)
    assert [row["id"] for row in lines] == [9]


@pytest.mark.parametrize(
    "error",
    [
        telethon_errors.SessionRevokedError(request=None),
        telethon_errors.AuthKeyUnregisteredError(request=None),
    ],
)
@pytest.mark.parametrize("where", ["source", "discussion_entity"])
def test_collect_lets_a_dead_session_escape(error, where):
    """A dead session is the run's problem: never an "unavailable" marker."""
    clone_state = seed(comments="enabled")
    if where == "source":
        tg = FakeTg(source_error=error)
    else:
        tg = FakeTg(source=[user(1)], entity_error=error)
    with pytest.raises(type(error)):
        run(tg, clone_state)


@pytest.mark.parametrize("comments", ["enabled", "none"])
def test_a_deferred_roster_defers_the_discussion_only_when_comments_are_on(comments):
    clone_state = seed(comments=comments)
    clone_state.discussion_source_peer_id = 55

    result = roster.deferred(clone_state, "wall_clock_cap")

    assert result["source"]["status"] == "deferred"
    expected = "deferred" if comments == "enabled" else "none"
    assert result["discussion"]["status"] == expected


def age(clone_state, seconds):
    path = roster.path_for(clone_state.clone_id)
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_a_roster_is_due_when_missing_and_again_after_a_day():
    clone_state = seed()
    assert roster.due(clone_state)

    run(FakeTg(source=[user(1)]), clone_state)

    assert not roster.due(clone_state)
    age(clone_state, roster.REFRESH_S - 60)
    assert not roster.due(clone_state)
    age(clone_state, roster.REFRESH_S)
    assert roster.due(clone_state)


def test_a_roster_stamped_in_the_future_is_due():
    clone_state = seed()
    run(FakeTg(source=[user(1)]), clone_state)
    age(clone_state, -3600)

    assert roster.due(clone_state)


def test_the_fresh_marker_names_each_peer_and_touches_no_file():
    clone_state = seed(comments="enabled")

    result = roster.fresh(clone_state)

    assert result["source"] == {
        "peer_id": 123,
        "status": "fresh",
        "count": 0,
        "reason": "collected_within_24h",
    }
    assert result["discussion"]["status"] == "fresh"
    assert not roster.path_for(clone_state.clone_id).exists()


@pytest.mark.parametrize("make_error", TRANSIENT_ERRORS)
def test_a_deferred_listing_keeps_the_last_snapshot_and_its_age(make_error):
    clone_state = seed()
    run(FakeTg(source=[user(1), user(2)]), clone_state)
    age(clone_state, roster.REFRESH_S + 600)
    path = roster.path_for(clone_state.clone_id)
    before = (path.read_text(), path.stat().st_mtime)

    result = run(
        FakeTg(source=[user(9), user(8)], source_error=make_error()), clone_state
    )

    assert result["source"]["status"] == "deferred"
    assert (path.read_text(), path.stat().st_mtime) == before
    assert roster.due(clone_state)


def test_a_deferred_discussion_keeps_the_snapshot_too():
    clone_state = seed(comments="enabled")
    run(FakeTg(source=[user(1)], discussion=[user(10)]), clone_state)
    path = roster.path_for(clone_state.clone_id)
    before = path.read_text()

    result = run(
        FakeTg(
            source=[user(1)],
            entity_error=telethon_errors.FloodWaitError(request=None, capture=30),
        ),
        clone_state,
    )

    assert result["discussion"]["status"] == "deferred"
    assert path.read_text() == before
