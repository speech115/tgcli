"""Unit tests for clone refresh eligibility and candidate scan (ADR-0054)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl import types

from tgcli.clone import refresh, state, transport


def _message(text="тело", entities=None, **overrides):
    values = {
        "id": 1,
        "message": text,
        "entities": entities,
        "fwd_from": None,
        "media": None,
        "grouped_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_eligible_when_dest_matches_raw_and_renderer_adds_prefix():
    bold = types.MessageEntityBold(offset=0, length=4)
    message = _message("тело", [bold])
    rendered = "Переслано от Имя\n\nтело"
    rendered_entities = [
        types.MessageEntityMentionName(offset=13, length=3, user_id=7),
        types.MessageEntityBold(offset=18, length=4),
    ]
    assert (
        refresh.eligible_for_backfill(
            message, "тело", [bold], rendered, rendered_entities
        )
        is True
    )


def test_ineligible_when_dest_text_differs_from_raw_source():
    message = _message("тело")
    assert (
        refresh.eligible_for_backfill(
            message,
            "Переслано от Имя\n\nтело",
            None,
            "Переслано от Имя\n\nтело",
            None,
        )
        is False
    )


def test_ineligible_when_dest_entities_differ_same_text():
    message = _message("тело", [types.MessageEntityBold(offset=0, length=4)])
    dest_entities = [types.MessageEntityItalic(offset=0, length=4)]
    rendered = "Переслано от Имя\n\nтело"
    assert (
        refresh.eligible_for_backfill(message, "тело", dest_entities, rendered, None)
        is False
    )


def test_ineligible_when_renderer_would_leave_raw_unchanged():
    message = _message("тело")
    assert refresh.eligible_for_backfill(message, "тело", None, "тело", None) is False


@pytest.mark.asyncio
async def test_render_with_fwd_from_calls_forwarded_author_once(monkeypatch):
    author = SimpleNamespace(text="Имя", mention_user_id=None, lead="Переслано от ")
    forwarded = AsyncMock(return_value=author)
    monkeypatch.setattr(refresh.attribution, "forwarded_author_of", forwarded)
    message = _message(
        "тело",
        fwd_from=types.MessageFwdHeader(date=None, from_name="Имя", imported=False),
    )
    cache: dict = {}
    cooldown = AsyncMock(side_effect=lambda awaitable: awaitable)

    text, entities = await refresh.render_with_current_rules(
        object(), message, cache, cooldown
    )

    forwarded.assert_awaited_once()
    expected = refresh.quote_fallback.apply_body(
        message,
        author,
        transport.TransportPlan(
            mode="reuploaded",
            reply_to=None,
            reply_flattened=False,
            needs_author=True,
        ),
    )
    assert (text, entities) == expected


@pytest.mark.asyncio
async def test_render_without_fwd_from_skips_forwarded_author(monkeypatch):
    forwarded = AsyncMock()
    monkeypatch.setattr(refresh.attribution, "forwarded_author_of", forwarded)
    message = _message("тело", entities=[types.MessageEntityBold(offset=0, length=4)])
    text, entities = await refresh.render_with_current_rules(
        object(), message, {}, AsyncMock()
    )
    forwarded.assert_not_awaited()
    assert text == "тело"
    assert entities == [types.MessageEntityBold(offset=0, length=4)]


def _clone_state(*, id_map=None, discussion_id_map=None):
    clone_state = state.CloneState.new(
        account_user_id=42,
        source_peer_id=123,
        source_title="Source",
        source_kind="broadcast",
    )
    clone_state.destination_peer_id = 999
    if id_map:
        for source_id, dest_id in id_map.items():
            clone_state.record_mapping(source_id, dest_id)
    if discussion_id_map:
        clone_state.comments = "enabled"
        clone_state.discussion_source_peer_id = 456
        clone_state.discussion_destination_peer_id = 888
        clone_state.discussion_linked = True
        for source_id, dest_id in discussion_id_map.items():
            clone_state.record_discussion_mapping(source_id, dest_id)
    return clone_state


def _fwd(from_name="Имя"):
    return types.MessageFwdHeader(date=None, from_name=from_name, imported=False)


class _ScanClient:
    """Minimal tg fake for candidates(): get_messages by ids, no vote RPCs."""

    def __init__(self, source_msgs, dest_msgs):
        self.source = SimpleNamespace(id=123)
        self.destination = SimpleNamespace(id=999)
        self.source_msgs = {m.id: m for m in source_msgs}
        self.dest_msgs = {m.id: m for m in dest_msgs}
        self.get_messages_calls = []
        self.get_entity_calls = []
        self.call_requests = []

    async def get_messages(self, entity, ids=None, limit=None):
        self.get_messages_calls.append((entity, ids))
        store = self.source_msgs if entity is self.source else self.dest_msgs
        return [store.get(item) for item in ids]

    async def get_entity(self, ref):
        self.get_entity_calls.append(ref)
        raise AssertionError("get_entity must not run for excluded polls")

    async def __call__(self, request):
        self.call_requests.append(request)
        raise AssertionError(f"unexpected RPC: {type(request).__name__}")


async def _passthrough(make):
    """ADR-0052: the cooldown seam takes a zero-arg thunk, not an awaitable."""
    return await make()


@pytest.mark.asyncio
async def test_candidates_routes_get_messages_through_cooldown():
    """Scan RPCs must go through cooldown so FloodWait arms retry_not_before."""
    source = _message(id=10, text="тело", fwd_from=_fwd())
    dest = _message(id=100, text="тело")
    client = _ScanClient([source], [dest])
    clone_state = _clone_state(id_map={10: 100})
    wrapped: list[object] = []

    async def tracking_cooldown(make):
        wrapped.append(make)
        return await make()

    await refresh.candidates(
        client, clone_state, client.source, client.destination, tracking_cooldown
    )

    assert len(wrapped) >= 2
    assert len(client.get_messages_calls) == 2


@pytest.mark.asyncio
async def test_poll_snapshot_excluded_before_render(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(refresh, "render_with_current_rules", render)
    poll = types.MessageMediaPoll(
        poll=types.Poll(
            id=1,
            question=types.TextWithEntities(text="Q", entities=[]),
            answers=[
                types.PollAnswer(
                    text=types.TextWithEntities(text="A", entities=[]), option=b"a"
                )
            ],
            hash=0,
        ),
        results=types.PollResults(results=[], total_voters=0),
    )
    source = _message(id=10, text="poll body", fwd_from=_fwd(), media=poll)
    dest = _message(id=100, text="poll body")
    client = _ScanClient([source], [dest])
    clone_state = _clone_state(id_map={10: 100})

    eligible, excluded = await refresh.candidates(
        client, clone_state, client.source, client.destination, _passthrough
    )

    assert eligible == []
    assert excluded == [refresh.Excluded(source_id=10, reason="poll-snapshot")]
    render.assert_not_awaited()
    assert client.get_entity_calls == []
    assert client.call_requests == []


@pytest.mark.asyncio
async def test_story_snapshot_excluded_before_render(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(refresh, "render_with_current_rules", render)
    story = types.MessageMediaStory(peer=types.PeerUser(user_id=1), id=5)
    source = _message(id=11, text="story", fwd_from=_fwd(), media=story)
    dest = _message(id=110, text="story")
    client = _ScanClient([source], [dest])
    clone_state = _clone_state(id_map={11: 110})

    eligible, excluded = await refresh.candidates(
        client, clone_state, client.source, client.destination, _passthrough
    )

    assert eligible == []
    assert excluded == [refresh.Excluded(source_id=11, reason="poll-snapshot")]
    render.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_reforward_excluded_without_render(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr(refresh, "render_with_current_rules", render)
    source = _message(id=12, text="тело", fwd_from=_fwd())
    dest = _message(id=120, text="тело", fwd_from=_fwd("Other"))
    client = _ScanClient([source], [dest])
    clone_state = _clone_state(id_map={12: 120})

    eligible, excluded = await refresh.candidates(
        client, clone_state, client.source, client.destination, _passthrough
    )

    assert eligible == []
    assert excluded == [refresh.Excluded(source_id=12, reason="native-reforward")]
    render.assert_not_awaited()


@pytest.mark.asyncio
async def test_discussion_id_map_never_scanned(monkeypatch):
    """Discussion-leg mappings are invisible to the scan by construction."""
    render = AsyncMock(return_value=("Переслано от Имя\n\nтело", None))
    monkeypatch.setattr(refresh, "render_with_current_rules", render)
    # Discussion source 50 would look eligible if scanned, but only lives in
    # discussion_id_map. Posts-leg 10 is a plain non-forward — silent skip.
    posts = _message(id=10, text="plain")
    discussion = _message(id=50, text="тело", fwd_from=_fwd())
    dest_posts = _message(id=100, text="plain")
    dest_disc = _message(id=500, text="тело")
    client = _ScanClient([posts, discussion], [dest_posts, dest_disc])
    clone_state = _clone_state(id_map={10: 100}, discussion_id_map={50: 500})

    eligible, excluded = await refresh.candidates(
        client, clone_state, client.source, client.destination, _passthrough
    )

    assert eligible == []
    assert excluded == []
    assert all(50 not in (ids or []) for _, ids in client.get_messages_calls)
    render.assert_not_awaited()


@pytest.mark.asyncio
async def test_album_non_lead_excluded_even_if_eligible_alone(monkeypatch):
    render = AsyncMock(return_value=("Переслано от Имя\n\nтело", None))
    monkeypatch.setattr(refresh, "render_with_current_rules", render)
    lead = _message(id=20, text="тело", fwd_from=_fwd(), grouped_id=77)
    follower = _message(id=21, text="тело", fwd_from=_fwd(), grouped_id=77)
    dest_lead = _message(id=200, text="тело")
    dest_follower = _message(id=201, text="тело")
    client = _ScanClient([lead, follower], [dest_lead, dest_follower])
    clone_state = _clone_state(id_map={20: 200, 21: 201})

    eligible, excluded = await refresh.candidates(
        client, clone_state, client.source, client.destination, _passthrough
    )

    assert [c.source_id for c in eligible] == [20]
    assert excluded == [refresh.Excluded(source_id=21, reason="album-non-lead")]


@pytest.mark.asyncio
async def test_album_lead_is_scanned_normally(monkeypatch):
    render = AsyncMock(return_value=("Переслано от Имя\n\ncaption", None))
    monkeypatch.setattr(refresh, "render_with_current_rules", render)
    lead = _message(id=30, text="caption", fwd_from=_fwd(), grouped_id=88)
    dest = _message(id=300, text="caption")
    client = _ScanClient([lead], [dest])
    clone_state = _clone_state(id_map={30: 300})

    eligible, excluded = await refresh.candidates(
        client, clone_state, client.source, client.destination, _passthrough
    )

    assert len(eligible) == 1
    assert eligible[0].source_id == 30
    assert eligible[0].destination_id == 300
    assert eligible[0].text == "Переслано от Имя\n\ncaption"
    assert excluded == []
