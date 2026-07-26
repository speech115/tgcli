# tests/test_clone_quotes.py
"""Resolver unit tests for clone quote replies (ADR-0036 slice 2)."""

import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import (
    attribution,
    legs,
    quote_fallback,
    quotes,
    replies,
    state,
    transport,
)


def _discussion(*, id_map=None, discussion_id_map=None, source_peer_id=2):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=source_peer_id, source_title="src"
    )
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 4454061248
    clone_state.discussion_destination_peer_id = 99
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    for source_id, destination_id in (discussion_id_map or {}).items():
        clone_state.record_discussion_mapping(source_id, destination_id)
    return legs.discussion(clone_state)


def _posts(id_map=None):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="src"
    )
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    return legs.posts(clone_state)


def _msg(message_id=10, *, reply_to=None, message="body", entities=None):
    return SimpleNamespace(
        id=message_id,
        reply_to=reply_to,
        message=message,
        entities=entities,
        media=None,
        noforwards=False,
        peer_id=types.PeerChannel(4454061248),
    )


DISCUSSION_SOURCE = SimpleNamespace(id=4454061248, noforwards=False)
SOURCE = SimpleNamespace(id=2, noforwards=False)


def test_resolver_helpers_stay_out_of_the_fallback_renderer():
    assert not hasattr(quote_fallback, "peer_cache_key")
    assert not hasattr(quote_fallback, "reuploaded")


class FakeClient:
    """Minimal client surface for quotes.resolve reachability probes."""

    def __init__(
        self,
        *,
        entities=None,
        readable=None,
        input_peers=None,
        chats=None,
        history_error=None,
    ):
        self.entities = entities or {}
        self.readable = readable if readable is not None else {}
        self.input_peers = input_peers or {}
        self.chats = chats
        self.history_error = history_error
        self.entity_calls = []
        self.message_calls = []
        self.history_calls = []

    async def __call__(self, request):
        """Raw request surface — only ``GetHistory`` for forbidden-peer titles."""
        if self.chats is None:
            raise TypeError("client is not callable in this test")
        self.history_calls.append(request)
        if self.history_error is not None:
            raise self.history_error
        return SimpleNamespace(messages=[], chats=list(self.chats), users=[])

    def _key(self, peer):
        if isinstance(peer, types.PeerChannel):
            return ("channel", peer.channel_id)
        if isinstance(peer, types.PeerUser):
            return ("user", peer.user_id)
        if isinstance(peer, types.PeerChat):
            return ("chat", peer.chat_id)
        return ("other", id(peer))

    async def get_entity(self, peer):
        self.entity_calls.append(peer)
        key = self._key(peer)
        if key not in self.entities:
            raise ValueError("peer not found")
        return self.entities[key]

    async def get_input_entity(self, peer):
        key = self._key(peer)
        if key not in self.input_peers:
            raise ValueError("no input peer")
        return self.input_peers[key]

    async def get_messages(self, entity, limit=None, ids=None):
        self.message_calls.append((entity, limit, ids))
        key = None
        for candidate, value in self.entities.items():
            if value is entity:
                key = candidate
                break
        if key is None or not self.readable.get(key, False):
            raise telethon_errors.ChannelPrivateError(request=None)
        if ids is not None:
            return SimpleNamespace(id=ids, message="quoted")
        return [SimpleNamespace(id=1, message="ok")]


def _ctx(client, *, destination=None, source_group=None, source_channel_id=None):
    anchors = {}
    anchor_cache = {}

    async def mutate(request):
        msg_id = getattr(request, "msg_id", None)
        found = anchor_cache.get(msg_id)
        return SimpleNamespace(
            messages=[] if found is None else [SimpleNamespace(id=found)]
        )

    return quotes.ResolveContext(
        tg=client,
        mutate=mutate,
        destination=destination or SimpleNamespace(id=999),
        source_group=source_group,
        source_channel_id=source_channel_id,
        anchors=anchors,
        anchor_cache=anchor_cache,
    )


def test_mapped_same_leg_keeps_quote_on_destination_parent():
    header = types.MessageReplyHeader(
        reply_to_msg_id=5,
        quote_text="claim",
        quote_entities=[types.MessageEntityBold(offset=0, length=5)],
        quote_offset=2,
    )
    messages = [_msg(reply_to=header)]
    leg = _posts({5: 105})
    plan = transport.decide(messages, leg, SOURCE)
    ctx = _ctx(FakeClient())

    resolved = asyncio.run(quotes.resolve(messages, plan, leg, SOURCE, ctx))

    assert isinstance(resolved.reply_to, types.InputReplyToMessage)
    assert resolved.reply_to.reply_to_msg_id == 105
    assert resolved.reply_to.quote_text == "claim"
    assert resolved.reply_to.quote_offset == 2
    assert resolved.reply_to.quote_entities == [
        types.MessageEntityBold(offset=0, length=5)
    ]
    assert resolved.reply_flattened is False
    assert resolved.body_prefix is None


def test_mapped_cross_leg_rewrites_via_post_map_and_destination_anchor():
    """Source 2378: quote of channel post 789 → dest 773 → discussion anchor."""
    header = types.MessageReplyHeader(
        reply_to_msg_id=789,
        reply_to_peer_id=types.PeerChannel(2),
        quote_text="quoted post",
        quote_entities=[types.MessageEntityItalic(offset=0, length=6)],
        quote_offset=1,
        reply_to_top_id=2377,
    )
    messages = [_msg(2378, reply_to=header)]
    leg = _discussion(id_map={789: 773}, discussion_id_map={2377: 901})
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    client = FakeClient()
    ctx = _ctx(client, source_channel_id=2)
    ctx.anchor_cache[773] = 550

    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))

    assert isinstance(resolved.reply_to, types.InputReplyToMessage)
    assert resolved.reply_to.reply_to_msg_id == 550
    assert resolved.reply_to.quote_text == "quoted post"
    assert resolved.reply_to.quote_offset == 1
    assert resolved.reply_to.quote_entities == [
        types.MessageEntityItalic(offset=0, length=6)
    ]
    assert resolved.reply_flattened is False
    assert resolved.mode == "reuploaded"


def test_foreign_peer_reachable_uses_native_quote_at_original():
    peer = types.PeerChannel(2275285084)
    entity = SimpleNamespace(id=2275285084, title="Foreign channel")
    input_peer = types.InputPeerChannel(channel_id=2275285084, access_hash=99)
    header = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=peer,
        quote_text="foreign quote",
        reply_to_top_id=2373,
    )
    messages = [_msg(2374, reply_to=header)]
    leg = _discussion(discussion_id_map={2373: 900})
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    client = FakeClient(
        entities={("channel", 2275285084): entity},
        readable={("channel", 2275285084): True},
        input_peers={("channel", 2275285084): input_peer},
    )
    ctx = _ctx(client)

    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))

    assert isinstance(resolved.reply_to, types.InputReplyToMessage)
    assert resolved.reply_to.reply_to_msg_id == 1244
    assert resolved.reply_to.reply_to_peer_id == input_peer
    assert resolved.reply_to.quote_text == "foreign quote"
    assert resolved.reply_flattened is False
    assert resolved.body_prefix is None
    # Reachability is cached: a second resolve must not re-probe.
    asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))
    assert len(client.entity_calls) == 1


def test_foreign_peer_unreachable_renders_fallback():
    peer = types.PeerChannel(2275285084)
    header = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=peer,
        quote_text="foreign quote",
        reply_to_top_id=2373,
    )
    messages = [_msg(2374, reply_to=header, message="author text")]
    leg = _discussion(discussion_id_map={2373: 900})
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    client = FakeClient()  # get_entity raises → unreachable
    ctx = _ctx(client)

    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))

    assert resolved.body_prefix is not None
    assert "foreign quote" in resolved.body_prefix
    assert any(
        isinstance(entity, types.MessageEntityBlockquote)
        for entity in resolved.body_prefix_entities
    )
    assert resolved.reply_to is None or resolved.reply_to.quote_text is None
    assert resolved.quote_flattened is not None
    assert resolved.quote_flattened["reason"] == "unreachable"


def _forbidden_case(chats, *, history_error=None):
    """Unresolved-peer quote ready to resolve: messages, plan, leg, client, ctx."""
    peer = types.PeerChannel(2275285084)
    header = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=peer,
        quote_text="foreign quote",
        reply_to_top_id=2373,
    )
    messages = [_msg(2374, reply_to=header, message="author text")]
    leg = _discussion(discussion_id_map={2373: 900})
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    # get_entity raises → unreachable
    client = FakeClient(chats=chats, history_error=history_error)
    return messages, plan, leg, client, _ctx(client)


def _forbidden_fallback(chats):
    messages, plan, leg, client, ctx = _forbidden_case(chats)
    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))
    return resolved, client, (messages, plan, leg, ctx)


def test_forbidden_peer_title_comes_from_the_enclosing_history_response():
    """Telegram ships the banned channel's title as ``ChannelForbidden``."""
    resolved, client, (messages, plan, leg, ctx) = _forbidden_fallback(
        [
            SimpleNamespace(id=4454061248, title="Злой чат"),
            SimpleNamespace(id=2275285084, title="Свободный Капиталюга"),
        ]
    )

    head = f"{quote_fallback.FALLBACK_SOURCE_LABEL} Свободный Капиталюга\n"
    assert resolved.body_prefix is not None
    assert resolved.body_prefix.startswith(head)
    assert "id 2275285084" not in resolved.body_prefix
    # The blockquote still covers exactly the quote, not the label line.
    (blockquote,) = resolved.body_prefix_entities
    assert isinstance(blockquote, types.MessageEntityBlockquote)
    assert blockquote.offset == attribution.utf16_len(head)
    assert blockquote.length == attribution.utf16_len("foreign quote")
    # Titles are cached per run: a second resolve must not re-request history.
    asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))
    assert len(client.history_calls) == 1


def test_forbidden_peer_title_ignores_a_chat_whose_id_merely_contains_the_peer():
    """`-100`-prefixed and lookalike ids must not be mistaken for the peer."""
    resolved, _client, _ = _forbidden_fallback(
        [
            SimpleNamespace(id=1072275285084, title="Якунин | Про прибыль Messages"),
            SimpleNamespace(id=22752850841, title="Decoy"),
        ]
    )

    assert resolved.body_prefix is not None
    assert resolved.body_prefix.startswith(
        f"{quote_fallback.FALLBACK_SOURCE_LABEL} id 2275285084\n"
    )


def test_forbidden_peer_without_a_title_keeps_the_bare_id():
    resolved, _client, _ = _forbidden_fallback([])

    assert resolved.body_prefix is not None
    assert resolved.body_prefix.startswith(
        f"{quote_fallback.FALLBACK_SOURCE_LABEL} id 2275285084\n"
    )


def test_forbidden_peer_title_propagates_a_flood_wait_without_caching_it():
    """A FloodWait is the run's flood, not a missing title: it must reach the
    caller's cooldown machinery, and the peer must stay uncached so the title
    still resolves after the wait instead of degrading for the whole run."""
    messages, plan, leg, _client, ctx = _forbidden_case(
        [SimpleNamespace(id=2275285084, title="Свободный Капиталюга")],
        history_error=telethon_errors.FloodWaitError(request=None, capture=61),
    )

    with pytest.raises(telethon_errors.FloodWaitError):
        asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))
    assert ctx.peer_titles == {}


def test_forbidden_peer_title_degrades_and_caches_on_a_non_flood_rpc_error():
    messages, plan, leg, client, ctx = _forbidden_case(
        [SimpleNamespace(id=2275285084, title="Свободный Капиталюга")],
        history_error=telethon_errors.ChannelPrivateError(request=None),
    )

    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))
    assert resolved.body_prefix is not None
    assert resolved.body_prefix.startswith(
        f"{quote_fallback.FALLBACK_SOURCE_LABEL} id 2275285084\n"
    )
    assert ctx.peer_titles == {("channel", 2275285084): None}
    # The miss is cached: a second quote from that peer re-requests nothing.
    asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))
    assert len(client.history_calls) == 1


def test_reachable_then_rejected_degrades_to_fallback():
    peer = types.PeerChannel(2275285084)
    entity = SimpleNamespace(id=2275285084, title="Foreign channel")
    input_peer = types.InputPeerChannel(channel_id=2275285084, access_hash=99)
    header = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=peer,
        quote_text="foreign quote",
    )
    messages = [_msg(2374, reply_to=header, message="author text")]
    leg = _discussion()
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    client = FakeClient(
        entities={("channel", 2275285084): entity},
        readable={("channel", 2275285084): True},
        input_peers={("channel", 2275285084): input_peer},
    )
    ctx = _ctx(client)
    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))
    assert resolved.reply_to is not None
    assert resolved.reply_to.reply_to_peer_id == input_peer

    degraded = quotes.degrade_to_fallback(
        messages, resolved, leg, DISCUSSION_SOURCE, ctx
    )

    assert degraded.body_prefix is not None
    assert "foreign quote" in degraded.body_prefix
    assert (
        degraded.reply_to is None
        or getattr(degraded.reply_to, "quote_text", None) is None
    )
    assert degraded.quote_flattened is not None
    assert degraded.quote_flattened["reason"] == "rejected"


def test_quiet_trap_utf16_offsets_after_quote_prefix_with_surrogate():
    """Prefix shift must use attribution's UTF-16 path — 😴 is a surrogate pair."""
    quote = "claim 😴 end"
    author_entity = types.MessageEntityBold(offset=0, length=4)
    messages = [
        _msg(
            2374,
            reply_to=types.MessageReplyHeader(
                reply_to_msg_id=1244,
                reply_to_peer_id=types.PeerChannel(2275285084),
                quote_text=quote,
            ),
            message="body",
            entities=[author_entity],
        )
    ]
    leg = _discussion()
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    ctx = _ctx(FakeClient())

    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))

    text, entities = attribution.with_prefix(
        messages[0].message,
        messages[0].entities,
        resolved.body_prefix,
        resolved.body_prefix_entities,
    )
    # Exactly one shift path: body entity offset == UTF-16 length of the prefix.
    expected_shift = len(resolved.body_prefix.encode("utf-16-le")) // 2
    bold = next(e for e in entities if isinstance(e, types.MessageEntityBold))
    assert bold.offset == expected_shift
    assert bold.length == 4
    assert author_entity.offset == 0  # original untouched
    assert quote in text
    assert "id 2275285084" in resolved.body_prefix


def test_quiet_trap_source_2374_never_uses_discussion_map_id_1244():
    """reply_to_msg_id 1244 addresses a foreign channel; discussion 1244 differs."""
    header = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=types.PeerChannel(2275285084),
        reply_from=types.MessageFwdHeader(date=None),
        reply_media=types.MessageMediaPhoto(),
        quote_text="что это де-факто не наставничество…",
        reply_to_top_id=2373,
    )
    messages = [_msg(2374, reply_to=header)]
    # Poison: discussion map has an unrelated 1244 → 9999. Wrong map = silent bug.
    leg = _discussion(discussion_id_map={1244: 9999, 2373: 900})
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    assert replies.target(messages, leg, DISCUSSION_SOURCE).kind == "foreign-peer"
    ctx = _ctx(FakeClient())

    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))

    if resolved.reply_to is not None:
        assert resolved.reply_to.reply_to_msg_id != 9999
        assert resolved.reply_to.quote_text is None or resolved.body_prefix is not None
    assert resolved.body_prefix is not None or (
        resolved.reply_to is not None and resolved.reply_to.reply_to_peer_id is not None
    )
    # Unreachable without a fake entity → must be fallback, never mapped 9999.
    assert resolved.body_prefix is not None
    assert resolved.reply_to is None or resolved.reply_to.reply_to_msg_id != 9999


def _threaded_ctx(client):
    return _ctx(
        client,
        source_group=SimpleNamespace(id=4454061248),
        source_channel_id=2,
    )


def test_quiet_trap_a_foreign_parent_id_is_never_walked_against_the_group():
    """ADR-0036 §4: the group's own 1244 must not capture a foreign quote."""
    header = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=types.PeerChannel(2275285084),
        quote_text="foreign quote",
    )
    messages = [_msg(2374, reply_to=header, message="author text")]
    leg = _discussion(id_map={789: 773})
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    ctx = _threaded_ctx(FakeClient())
    # The group's own message 1244 happens to be the anchor of source post 789,
    # mapped to destination post 773 whose anchor is 550.
    ctx.anchors[1244] = 789
    ctx.anchor_cache[773] = 550

    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))

    assert resolved.body_prefix is not None
    # Never a native reply at 550, and never the foreign quote on top of it.
    assert resolved.reply_to is None
    assert resolved.quote_flattened["reason"] == "unreachable"


def test_degrade_keeps_the_thread_placement_the_rejected_send_resolved():
    """A discussion top is an auto-forward anchor: dest_for alone cannot find it."""
    peer = types.PeerChannel(2275285084)
    input_peer = types.InputPeerChannel(channel_id=2275285084, access_hash=99)
    header = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=peer,
        quote_text="foreign quote",
        reply_to_top_id=2373,
    )
    messages = [_msg(2374, reply_to=header, message="author text")]
    leg = _discussion(id_map={789: 773})
    plan = transport.decide(messages, leg, DISCUSSION_SOURCE)
    client = FakeClient(
        entities={("channel", 2275285084): SimpleNamespace(id=2275285084, title="Ch")},
        readable={("channel", 2275285084): True},
        input_peers={("channel", 2275285084): input_peer},
    )
    ctx = _threaded_ctx(client)
    ctx.anchors[2373] = 789
    ctx.anchor_cache[773] = 550
    resolved = asyncio.run(quotes.resolve(messages, plan, leg, DISCUSSION_SOURCE, ctx))
    assert resolved.reply_to.top_msg_id == 550
    assert leg.dest_for(2373) is None  # the anchor is never in the leg's map

    degraded = quotes.degrade_to_fallback(
        messages, resolved, leg, DISCUSSION_SOURCE, ctx
    )

    assert degraded.body_prefix is not None
    assert degraded.reply_to.reply_to_msg_id == 550
    assert degraded.reply_to.quote_text is None


def _bad_request(message: str):
    error = telethon_errors.BadRequestError(request=None, message=message)
    error.message = message
    return error


def test_stale_quote_is_dropped_and_the_reply_link_survives():
    """Source 2378: the quoted post was edited, so the fragment no longer matches."""
    header = types.MessageReplyHeader(
        reply_to_msg_id=789,
        reply_to_peer_id=types.PeerChannel(4301599563),
        quote_text="Им самих не смущает эта хуйня?",
        quote_offset=282,
        reply_to_top_id=2375,
    )
    messages = [_msg(2378, reply_to=header)]
    plan = transport.TransportPlan(
        mode="reuploaded",
        reply_to=types.InputReplyToMessage(
            reply_to_msg_id=550,
            quote_text="Им самих не смущает эта хуйня?",
            quote_offset=282,
        ),
        reply_flattened=False,
        needs_author=True,
    )

    stripped = quote_fallback.drop_stale_quote(
        messages, plan, _bad_request("QUOTE_TEXT_INVALID")
    )

    assert stripped is not None
    # The reply still points at the mapped destination message.
    assert stripped.reply_to.reply_to_msg_id == 550
    assert stripped.reply_to.quote_text is None
    assert stripped.reply_to.quote_offset is None
    assert stripped.quote_flattened == {
        "id": 2378,
        "peer": 4301599563,
        "reason": "quote-rejected",
    }
    # The original plan is untouched — the retry must not mutate the first send.
    assert plan.reply_to.quote_text == "Им самих не смущает эта хуйня?"


def test_send_with_degrade_retries_once_without_the_stale_quote():
    header = types.MessageReplyHeader(
        reply_to_msg_id=789,
        reply_to_peer_id=types.PeerChannel(4301599563),
        quote_text="stale",
        reply_to_top_id=2375,
    )
    messages = [_msg(2378, reply_to=header)]
    plan = transport.TransportPlan(
        mode="reuploaded",
        reply_to=types.InputReplyToMessage(reply_to_msg_id=550, quote_text="stale"),
        reply_flattened=False,
        needs_author=True,
    )
    sent = []

    async def send(current):
        sent.append(current)
        if current.reply_to.quote_text is not None:
            raise _bad_request("QUOTE_TEXT_INVALID")
        return "ok"

    leg = _discussion(id_map={789: 773})
    result = asyncio.run(
        quotes.send_with_degrade(
            send, messages, plan, leg, DISCUSSION_SOURCE, _ctx(FakeClient())
        )
    )

    assert result == "ok"
    assert len(sent) == 2
    assert sent[1].reply_to.reply_to_msg_id == 550
    assert sent[1].quote_flattened["reason"] == "quote-rejected"


def test_send_with_degrade_reraises_unrelated_bad_requests():
    messages = [_msg(2378)]
    plan = transport.TransportPlan(
        mode="reuploaded", reply_to=None, reply_flattened=False, needs_author=True
    )

    async def send(_current):
        raise _bad_request("MESSAGE_TOO_LONG")

    leg = _discussion()
    with pytest.raises(telethon_errors.BadRequestError):
        asyncio.run(
            quotes.send_with_degrade(
                send, messages, plan, leg, DISCUSSION_SOURCE, _ctx(FakeClient())
            )
        )
