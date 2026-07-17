"""Direct unit tests for clone discussion helpers."""
import asyncio
from types import SimpleNamespace

from telethon.tl import functions, types

from tgcli.clone import discussion


def _full(**overrides):
    values = {"linked_chat_id": None, "linked_monoforum_id": None}
    values.update(overrides)
    return SimpleNamespace(full_chat=SimpleNamespace(**values))


def test_linked_chat_id_reads_the_linked_chat():
    assert discussion.linked_chat_id(_full(linked_chat_id=55).full_chat) == 55


def test_linked_chat_id_ignores_monoforums():
    assert discussion.linked_chat_id(
        _full(linked_monoforum_id=77).full_chat) is None


def test_is_discussion_destination_accepts_private_owned_megagroup():
    entity = SimpleNamespace(title="G", creator=True, megagroup=True,
                             broadcast=False, forum=False, username=None,
                             usernames=[])
    assert discussion.is_discussion_destination(entity, title="G") is True


def test_is_discussion_destination_rejects_forum():
    entity = SimpleNamespace(title="G", creator=True, megagroup=True,
                             broadcast=False, forum=True, username=None,
                             usernames=[])
    assert discussion.is_discussion_destination(entity) is False


def _anchor(source_channel_id, post_id):
    return SimpleNamespace(id=1, fwd_from=types.MessageFwdHeader(
        date=None, channel_post=post_id,
        saved_from_peer=types.PeerChannel(channel_id=source_channel_id),
        saved_from_msg_id=post_id))


def test_autoforward_post_id_matches_saved_from_pair():
    assert discussion.autoforward_post_id(_anchor(123, 3680), 123) == 3680


def test_autoforward_post_id_rejects_other_channels():
    assert discussion.autoforward_post_id(_anchor(999, 3680), 123) is None


def test_autoforward_post_id_ignores_plain_messages():
    assert discussion.autoforward_post_id(
        SimpleNamespace(id=2, fwd_from=None), 123) is None


def test_ensure_linked_unhides_history_then_links():
    requests = []

    async def mutate(request):
        requests.append(request)

    asyncio.run(discussion.ensure_linked(mutate, "channel", "group"))
    assert isinstance(requests[0],
                      functions.channels.TogglePreHistoryHiddenRequest)
    assert requests[0].enabled is False
    assert isinstance(requests[1], functions.channels.SetDiscussionGroupRequest)
    assert requests[1].broadcast == "channel" and requests[1].group == "group"


def test_anchor_for_caches_lookups():
    calls = []

    async def mutate(request):
        calls.append(request.msg_id)
        return SimpleNamespace(messages=[SimpleNamespace(id=500)])

    cache = {}
    first = asyncio.run(discussion.anchor_for(mutate, "dest", 10, cache))
    second = asyncio.run(discussion.anchor_for(mutate, "dest", 10, cache))
    assert first == second == 500
    assert calls == [10]


def test_anchor_for_returns_none_without_anchor():
    async def mutate(request):
        return SimpleNamespace(messages=[])

    assert asyncio.run(discussion.anchor_for(mutate, "dest", 10, {})) is None
