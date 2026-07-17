import asyncio
import json
from types import SimpleNamespace

from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import roster, state


def user(uid, **overrides):
    values = {"id": uid, "username": None, "first_name": f"U{uid}",
              "last_name": None, "phone": None, "bot": False}
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeTg:
    """Minimal duck-typed client for roster.collect."""

    def __init__(self, *, source=(), discussion=(), source_error=None,
                 discussion_error=None, discussion_entity=True):
        self._source = list(source)
        self._discussion = list(discussion)
        self._source_error = source_error
        self._discussion_error = discussion_error
        self._discussion_entity = discussion_entity
        self.source_entity = SimpleNamespace(id=123)
        self.group_entity = SimpleNamespace(id=55)

    async def get_entity(self, peer):
        assert isinstance(peer, types.PeerChannel)
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
    s = state.CloneState.new(account_user_id=42, source_peer_id=123,
                             source_title="Source channel")
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
    assert lines[0] == {"peer": "source", "id": 1, "username": "a",
                        "first_name": "U1", "last_name": None, "phone": None,
                        "is_bot": False}


def test_collect_marks_channel_unavailable_when_not_admin():
    clone_state = seed()
    tg = FakeTg(source_error=telethon_errors.ChatAdminRequiredError(request=None))
    result = run(tg, clone_state)
    assert result["source"]["status"] == "unavailable"
    assert result["source"]["count"] == 0
    assert read_lines(clone_state) == []


def test_collect_defers_on_floodwait_and_discards_partial():
    clone_state = seed()
    flood = telethon_errors.FloodWaitError(request=None)
    flood.seconds = 30
    tg = FakeTg(source=[user(1), user(2), user(3)], source_error=flood)
    result = run(tg, clone_state)
    assert result["source"]["status"] == "deferred"
    assert result["source"]["count"] == 0
    assert read_lines(clone_state) == []
    # a roster flood must not arm the main clone cooldown
    assert clone_state.cooldown_deadline() is None


def test_collect_gathers_discussion_group_when_comments_enabled():
    clone_state = seed(comments="enabled")
    tg = FakeTg(source_error=telethon_errors.ChatAdminRequiredError(request=None),
                discussion=[user(10, username="z"), user(11)])
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


def test_collect_overwrites_previous_snapshot():
    clone_state = seed()
    run(FakeTg(source=[user(1), user(2)]), clone_state)
    run(FakeTg(source=[user(9)]), clone_state)
    lines = read_lines(clone_state)
    assert [row["id"] for row in lines] == [9]
