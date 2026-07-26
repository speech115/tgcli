# tests/test_clone_reforward.py
"""Direct unit tests for ADR-0050 Part B native re-forward proof."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import legs, reforward, state, transport

DATE = datetime(2026, 7, 25, 13, 55, tzinfo=UTC)


def _clone_state(*, discussion_source_peer_id=55, kind="broadcast"):
    clone_state = state.CloneState.new(
        account_user_id=1,
        source_peer_id=2,
        source_title="src",
        source_kind=kind,
    )
    clone_state.discussion_source_peer_id = discussion_source_peer_id
    return clone_state


def _plan(**overrides):
    fields = {
        "mode": "reuploaded",
        "reply_to": None,
        "reply_flattened": False,
        "needs_author": True,
    }
    fields.update(overrides)
    return transport.TransportPlan(**fields)


def _post(*, fwd_from=None, message="body", media=None, entities=None):
    return SimpleNamespace(
        fwd_from=fwd_from, message=message, media=media, entities=entities
    )


def _candidate(message_id, *, date=DATE, message="body", media=None, entities=None):
    return SimpleNamespace(
        id=message_id, date=date, message=message, media=media, entities=entities
    )


def _fwd(**fields):
    fields.setdefault("date", DATE)
    return types.MessageFwdHeader(**fields)


def _photo(photo_id=7, spoiler=False):
    return types.MessageMediaPhoto(photo=types.PhotoEmpty(id=photo_id), spoiler=spoiler)


async def _invoke(make_awaitable):
    return await make_awaitable()


class FakeTg:
    def __init__(self, *, group=None, get_entity_error=None, search_results=()):
        self.group = group
        self.get_entity_error = get_entity_error
        self.search_results = list(search_results)
        self.get_entity_calls = []
        self.get_messages_calls = []

    async def get_entity(self, peer):
        self.get_entity_calls.append(peer)
        if self.get_entity_error is not None:
            raise self.get_entity_error
        return self.group

    async def get_messages(
        self, group, *, from_user=None, offset_date=None, limit=None
    ):
        self.get_messages_calls.append(
            SimpleNamespace(
                group=group, from_user=from_user, offset_date=offset_date, limit=limit
            )
        )
        return self.search_results


# --- eligible() -------------------------------------------------------


def test_eligible_false_for_album():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    leg = legs.posts(_clone_state())
    messages = [
        _post(fwd_from=fwd),
        SimpleNamespace(fwd_from=fwd, message="body", media=None, grouped_id=77),
    ]
    assert reforward.eligible(leg, messages, _plan()) is False


def test_eligible_false_for_snapshots_mode():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    leg = legs.posts(_clone_state())
    assert (
        reforward.eligible(leg, [_post(fwd_from=fwd)], _plan(mode="snapshots")) is False
    )


def test_eligible_false_for_forwarded_mode():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    leg = legs.posts(_clone_state())
    assert (
        reforward.eligible(leg, [_post(fwd_from=fwd)], _plan(mode="forwarded")) is False
    )


def test_eligible_false_for_megagroup_leg():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    leg = legs.posts(_clone_state(kind="megagroup"))
    assert reforward.eligible(leg, [_post(fwd_from=fwd)], _plan()) is False


def test_eligible_false_without_fwd_from():
    leg = legs.posts(_clone_state())
    assert reforward.eligible(leg, [_post(fwd_from=None)], _plan()) is False


def test_eligible_false_for_hidden_account_fwd_from():
    """`from_name` only (hidden account) is unsearchable by construction."""
    fwd = _fwd(from_name="Кто-то")
    leg = legs.posts(_clone_state())
    assert reforward.eligible(leg, [_post(fwd_from=fwd)], _plan()) is False


def test_eligible_false_with_reply_to():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    leg = legs.posts(_clone_state())
    reply_to = types.InputReplyToMessage(reply_to_msg_id=5)
    assert (
        reforward.eligible(leg, [_post(fwd_from=fwd)], _plan(reply_to=reply_to))
        is False
    )


def test_eligible_false_for_quote_fallback_batch():
    """A quote fallback rewrites the body, so the untouched original is no
    longer what the post says — and it carries no `reply_to` to catch it."""
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    leg = legs.posts(_clone_state())
    assert (
        reforward.eligible(leg, [_post(fwd_from=fwd)], _plan(body_prefix="> цитата\n"))
        is False
    )
    assert (
        reforward.eligible(
            leg, [_post(fwd_from=fwd)], _plan(quote_flattened={"id": 5, "quote": "x"})
        )
        is False
    )


def test_eligible_true_for_protected_broadcast_repost():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    leg = legs.posts(_clone_state())
    assert reforward.eligible(leg, [_post(fwd_from=fwd)], _plan()) is True


# --- locate() -----------------------------------------------------------


def test_locate_happy_path_returns_group_and_message_id():
    peer = types.PeerUser(user_id=9)
    fwd = _fwd(from_id=peer)
    post = _post(fwd_from=fwd, message="body", media=_photo())
    group = SimpleNamespace(id=55, noforwards=False)
    candidate = _candidate(321, message="body", media=_photo())
    tg = FakeTg(group=group, search_results=[candidate])
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result == (group, 321)
    [call] = tg.get_messages_calls
    assert call.group is group
    assert call.from_user == peer
    assert call.offset_date == DATE + timedelta(seconds=1)
    assert call.limit == reforward.SEARCH_LIMIT


def test_locate_none_without_discussion_source_peer_issues_no_rpc():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd)
    tg = FakeTg(group=SimpleNamespace(id=55, noforwards=False))
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(
            tg,
            _clone_state(discussion_source_peer_id=None),
            post,
            cache,
            invoke=_invoke,
        )
    )

    assert result is None
    assert tg.get_entity_calls == []
    assert tg.get_messages_calls == []


def test_locate_none_when_group_is_protected_issues_no_search():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd)
    group = SimpleNamespace(id=55, noforwards=True)
    tg = FakeTg(group=group)
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None
    assert tg.get_messages_calls == []


def test_locate_none_when_group_unreachable():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd)
    tg = FakeTg(get_entity_error=ValueError("not joined"))
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None
    assert tg.get_messages_calls == []


def test_locate_none_when_two_candidates_match_sender_and_date():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd, message="body")
    group = SimpleNamespace(id=55, noforwards=False)
    tg = FakeTg(
        group=group,
        search_results=[
            _candidate(1, message="body"),
            _candidate(2, message="body"),
        ],
    )
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None


def test_locate_none_when_candidate_text_was_edited_after_repost():
    """Live `[икона]` 69 case: reposted 13:55, edited 16:18 — must not
    republish the unedited original under a genuine-looking header."""
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd, message="edited body")
    group = SimpleNamespace(id=55, noforwards=False)
    tg = FakeTg(group=group, search_results=[_candidate(1, message="original body")])
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None


def test_locate_none_when_candidate_is_missing_media_the_post_has():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd, message="body", media=_photo())
    group = SimpleNamespace(id=55, noforwards=False)
    tg = FakeTg(group=group, search_results=[_candidate(1, message="body", media=None)])
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None


def test_locate_none_when_post_is_missing_media_the_candidate_has():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd, message="body", media=None)
    group = SimpleNamespace(id=55, noforwards=False)
    tg = FakeTg(
        group=group, search_results=[_candidate(1, message="body", media=_photo())]
    )
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None


def test_locate_none_when_the_post_hides_media_the_candidate_shows():
    """A channel that reposts a photo behind a spoiler hid it deliberately.
    The group original carries the same file id with no spoiler, so matching
    on the id alone would forward the blur away and publish it uncovered."""
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd, message="body", media=_photo(spoiler=True))
    group = SimpleNamespace(id=55, noforwards=False)
    tg = FakeTg(
        group=group,
        search_results=[_candidate(1, message="body", media=_photo(spoiler=False))],
    )
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None


def test_locate_matches_when_the_spoiler_flag_agrees():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd, message="body", media=_photo(spoiler=True))
    group = SimpleNamespace(id=55, noforwards=False)
    tg = FakeTg(
        group=group,
        search_results=[_candidate(1, message="body", media=_photo(spoiler=True))],
    )
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result == (group, 1)


def test_locate_none_when_candidate_entities_differ_from_post():
    """Live `[икона]` 69 shape: reposted 13:55, edited 16:18 without changing
    a character — an edit that only moves formatting must still block the
    forward, or `_same_content` would miss the exact case it exists for."""
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    bold = types.MessageEntityBold(offset=0, length=4)
    post = _post(fwd_from=fwd, message="body", entities=[bold])
    group = SimpleNamespace(id=55, noforwards=False)
    tg = FakeTg(
        group=group, search_results=[_candidate(1, message="body", entities=None)]
    )
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None


def test_locate_matches_when_candidate_entities_are_identical():
    """Guard against over-strictness: matching entities must not be treated
    as a mismatch."""
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    bold = types.MessageEntityBold(offset=0, length=4)
    post = _post(fwd_from=fwd, message="body", entities=[bold])
    group = SimpleNamespace(id=55, noforwards=False)
    same_bold = types.MessageEntityBold(offset=0, length=4)
    candidate = _candidate(321, message="body", entities=[same_bold])
    tg = FakeTg(group=group, search_results=[candidate])
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result == (group, 321)


def test_locate_none_when_candidate_date_outside_window_is_ignored():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd, message="body")
    group = SimpleNamespace(id=55, noforwards=False)
    other_date = DATE + timedelta(seconds=1)
    tg = FakeTg(
        group=group, search_results=[_candidate(1, date=other_date, message="body")]
    )
    cache: dict = {}

    result = asyncio.run(
        reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke)
    )

    assert result is None


def test_source_group_resolved_once_per_run_across_locate_calls():
    group = SimpleNamespace(id=55, noforwards=False)
    tg = FakeTg(group=group, search_results=[_candidate(1, message="body")])
    cache: dict = {}
    clone_state = _clone_state()
    first_post = _post(fwd_from=_fwd(from_id=types.PeerUser(user_id=9)), message="body")
    second_post = _post(
        fwd_from=_fwd(from_id=types.PeerUser(user_id=10)), message="body"
    )

    asyncio.run(reforward.locate(tg, clone_state, first_post, cache, invoke=_invoke))
    asyncio.run(reforward.locate(tg, clone_state, second_post, cache, invoke=_invoke))

    assert len(tg.get_entity_calls) == 1


def test_locate_propagates_flood_wait():
    fwd = _fwd(from_id=types.PeerUser(user_id=9))
    post = _post(fwd_from=fwd)
    group = SimpleNamespace(id=55, noforwards=False)

    class FloodTg(FakeTg):
        async def get_messages(self, group, **kwargs):
            raise telethon_errors.FloodWaitError(request=None)

    tg = FloodTg(group=group)
    cache: dict = {}

    with pytest.raises(telethon_errors.FloodWaitError):
        asyncio.run(reforward.locate(tg, _clone_state(), post, cache, invoke=_invoke))
