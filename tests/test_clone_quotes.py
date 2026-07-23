# tests/test_clone_quotes.py
"""Resolver unit tests for clone quote replies (ADR-0036 slice 2)."""

import asyncio
from types import SimpleNamespace

from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import attribution, legs, quotes, replies, state, transport


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
    )


DISCUSSION_SOURCE = SimpleNamespace(id=4454061248, noforwards=False)
SOURCE = SimpleNamespace(id=2, noforwards=False)


class FakeClient:
    """Minimal client surface for quotes.resolve reachability probes."""

    def __init__(self, *, entities=None, readable=None, input_peers=None):
        self.entities = entities or {}
        self.readable = readable if readable is not None else {}
        self.input_peers = input_peers or {}
        self.entity_calls = []
        self.message_calls = []

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
    """reply_to_msg_id 1244 addresses a foreign channel; discussion 1244 is unrelated."""
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
