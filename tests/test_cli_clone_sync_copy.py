"""clone sync: plain copy, author headers, replies and quote fallback."""

import json

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone import (
    CloneReuploadClient,
    CloneSyncClient,
    channel,
    legacy_group,
    message,
    seed_clone,
)

# A fixture: the redundant alias marks an explicit re-export for ruff.
from tests.fakes.clone_env import config_env as config_env
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import state


def test_clone_sync_copies_plain_text_oldest_first_and_reruns_idempotently(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(3), message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result.pop("remaining") is False
    participants = result["sync"].pop("participants")
    assert result["sync"].pop("pinned") == {
        "source_id": None,
        "destination_id": None,
        "status": "unmapped",
    }
    assert result["sync"] == {
        "copied": 2,
        "forwarded": 2,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "quote_flattened": [],
        "markup_dropped": [],
        "poll_votes": [],
        "skipped_service": 0,
        "skipped_unsupported": [],
        "skipped_autoforward": 0,
        "topics_created": 0,
        "cursor": 3,
        "discussion_cursor": 0,
        "more": False,
    }
    # a broadcast source the account does not administer reports no roster
    assert participants["source"]["status"] == "unavailable"
    assert participants["discussion"]["status"] == "none"
    assert [request.id for request in client.requests] == [[2], [3]]
    assert all(request.drop_author is True for request in client.requests)
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 3
    assert saved.dest_for(2) == 2
    assert saved.dest_for(3) == 3
    assert saved.last_synced_at is not None
    assert client.session_mutation_safe is True
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert [record["action"] for record in audits] == [
        "clone-sync-forward",
        "clone-sync-forward",
    ]

    assert main(["clone", "sync", "@source", "--json"]) == 0
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["sync"]["copied"] == 0
    assert len(client.requests) == 2
    assert client.iter_messages_calls == [("posts", 0), ("posts", 3)]


def test_clone_sync_keeps_native_forward_header_for_reforwarded_broadcast_post(
    config_env, monkeypatch, capsys
):
    """A broadcast source's own post drops its author (clone looks native), but a
    post that is itself a forward keeps drop_author=False so Telegram restores
    the original forward header instead of erasing the re-forward's origin."""
    seed_clone()
    own = message(2)
    reforward = message(
        3, fwd_from=types.MessageFwdHeader(date=None, from_name="Original Author")
    )
    client = CloneSyncClient([own, reforward])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    by_id = {
        tuple(request.id): request.drop_author
        for request in client.requests
        if isinstance(request, functions.messages.ForwardMessagesRequest)
    }
    assert by_id == {(2,): True, (3,): False}


def test_clone_sync_keeps_native_forward_header_for_reforwarded_album(
    config_env, monkeypatch, capsys
):
    """A forwarded album carries fwd_from on every item, so the whole batch
    forwards with drop_author=False and keeps its origin header."""
    seed_clone()
    header = types.MessageFwdHeader(date=None, from_name="Original Author")
    album = [
        message(2, grouped_id=77, fwd_from=header),
        message(3, grouped_id=77, fwd_from=header),
    ]
    client = CloneSyncClient(album)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.ForwardMessagesRequest)
    ]
    assert request.id == [2, 3]
    assert request.drop_author is False


def test_clone_sync_forwards_megagroup_nonreply_with_author_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneSyncClient([message(2)])
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["source"]["kind"] == "megagroup"
    assert result["sync"]["forwarded"] == 1
    assert result["sync"]["reuploaded"] == 0
    assert client.requests[0].drop_author is False


def test_clone_sync_forwards_basic_group_nonreply_with_author_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="basic", title="Legacy group")
    client = CloneSyncClient([message(2)])
    client.source = legacy_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert client.requests[0].drop_author is False
    assert sync["forwarded"] == 1


def test_clone_sync_reuploads_megagroup_reply_with_prefix_and_shifted_entities(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="megagroup", title="Team chat")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    original = types.MessageEntityBold(offset=2, length=4)
    client = CloneReuploadClient(
        [
            message(
                2,
                message="😀bold",
                entities=[original],
                from_id=types.PeerUser(77),
                reply_to=types.MessageReplyHeader(reply_to_msg_id=1),
            )
        ]
    )
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Zoë 🚀")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message == "Zoë 🚀: \n\n😀bold"
    assert request.reply_to.reply_to_msg_id == 1001
    prefix_units = len("Zoë 🚀: \n\n".encode("utf-16-le")) // 2
    mention, shifted = request.entities
    assert mention == types.MessageEntityMentionName(
        offset=0, length=len("Zoë 🚀".encode("utf-16-le")) // 2, user_id=77
    )
    assert shifted.offset == prefix_units + 2
    assert original.offset == 2
    assert sync["reuploaded"] == 1
    assert sync["reply_flattened"] == 0


def test_clone_sync_reuploads_protected_megagroup_with_cached_author_prefix(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneReuploadClient(
        [
            message(2, message="first", from_id=types.PeerUser(77)),
            message(3, message="second", from_id=types.PeerUser(77)),
        ],
        protected=True,
    )
    client.source = channel(
        123,
        "Team chat",
        broadcast=False,
        megagroup=True,
        forum=False,
        noforwards=True,
    )
    author_lookups = 0

    async def get_entity(ref):
        nonlocal author_lookups
        if isinstance(ref, types.PeerUser):
            author_lookups += 1
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    sends = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMessageRequest)
    ]
    assert [item.message for item in sends] == ["Alex: \n\nfirst", "Alex: \n\nsecond"]
    assert author_lookups == 1
    assert sync["reuploaded"] == 2


def test_clone_sync_reuploads_protected_broadcast_repost_with_forward_prefix(
    config_env, monkeypatch, capsys
):
    """ADR-0050: protected broadcast post with fwd_from gets Переслано от + mention."""
    seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient(
        [
            message(
                2,
                message="repost body",
                media=photo,
                fwd_from=types.MessageFwdHeader(
                    date=None, from_id=types.PeerUser(user_id=973293498)
                ),
            )
        ],
        protected=True,
    )
    author_lookups = 0

    async def get_entity(ref):
        nonlocal author_lookups
        if isinstance(ref, types.PeerUser) and ref.user_id == 973293498:
            author_lookups += 1
            return types.User(id=973293498, first_name="Subscriber", username=None)
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = [
        item
        for item in client.requests
        if isinstance(
            item,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMessageRequest,
            ),
        )
    ]
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert request.message.startswith("Переслано от ")
    assert request.message == "Переслано от Subscriber\n\nrepost body"
    lead_units = len("Переслано от ".encode("utf-16-le")) // 2
    assert request.entities == [
        types.MessageEntityMentionName(
            offset=lead_units,
            length=len("Subscriber".encode("utf-16-le")) // 2,
            user_id=973293498,
        )
    ]
    assert author_lookups == 1
    assert sync["copied"] == 1
    assert sync["reuploaded"] == 1
    assert "forward_prefix" not in sync


def test_clone_sync_protected_broadcast_without_fwd_from_stays_byte_identical(
    config_env, monkeypatch, capsys
):
    """Regression: ordinary protected posts gain no Переслано от line."""
    seed_clone()
    client = CloneReuploadClient([message(2, message="plain post")], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message == "plain post"
    assert request.entities is None


def test_clone_sync_reuploads_protected_forwarded_album_prefixes_leading_item_only(
    config_env, monkeypatch, capsys
):
    seed_clone()
    header = types.MessageFwdHeader(date=None, from_id=types.PeerUser(user_id=77))
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient(
        [
            message(2, grouped_id=77, message="cap", media=photo, fwd_from=header),
            message(3, grouped_id=77, message="", media=photo, fwd_from=header),
        ],
        protected=True,
    )

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex", username=None)
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMultiMediaRequest)
    ]
    assert request.multi_media[0].message == "Переслано от Alex\n\ncap"
    assert request.multi_media[1].message == ""


def test_clone_sync_forwards_private_dialog_nonreply_with_author_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="dialog", title="Alex Smith")
    client = CloneSyncClient([message(2)])
    client.source = types.User(id=123, first_name="Alex", last_name="Smith")
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert client.requests[0].drop_author is False
    assert sync["forwarded"] == 1


def test_clone_sync_reuploads_private_dialog_reply_with_explicit_source_peer(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="dialog", title="Alex Smith")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1, reply_to_peer_id=types.PeerUser(123)
    )
    client = CloneReuploadClient(
        [
            message(
                2,
                message="answer",
                from_id=types.PeerUser(123),
                reply_to=reply,
            )
        ]
    )
    client.source = types.User(id=123, first_name="Alex", last_name="Smith")
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return client.source
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message == "Alex Smith: \n\nanswer"
    assert request.reply_to.reply_to_msg_id == 1001


def test_clone_sync_reuploads_basic_group_reply_with_explicit_source_peer(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="basic", title="Legacy group")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1, reply_to_peer_id=types.PeerChat(123)
    )
    client = CloneReuploadClient([message(2, message="answer", reply_to=reply)])
    client.source = legacy_group()
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.reply_to.reply_to_msg_id == 1001


def test_clone_sync_reuploads_basic_group_reply_with_prefix(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="basic", title="Legacy group")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    client = CloneReuploadClient(
        [
            message(
                2,
                message="pong",
                from_id=types.PeerUser(77),
                reply_to=types.MessageReplyHeader(reply_to_msg_id=1),
            ),
        ]
    )
    client.source = legacy_group()
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMessageRequest)
    ]
    assert request.message == "Alex: \n\npong"
    assert request.reply_to.reply_to_msg_id == 1001
    assert sync["reuploaded"] == 1


def test_clone_sync_copies_basic_group_foreign_peer_reply_without_wedging(
    config_env, monkeypatch, capsys
):
    """Different-source-peer reply: unreachable probe → fallback + PartialFailure."""
    clone_state = seed_clone(kind="basic", title="Legacy group")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1, reply_to_peer_id=types.PeerChat(456)
    )
    client = CloneReuploadClient([message(2, reply_to=reply)])
    client.source = legacy_group()
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["reuploaded"] == 1
    assert sync["quote_flattened"] == [{"id": 2, "peer": 456, "reason": "unreachable"}]
    # The unreachable peer's title is looked up in the source's own history.
    assert [request.peer for request in client.history_requests] == [
        types.PeerChat(chat_id=123)
    ]
    assert state.load(clone_state.clone_id).dest_for(2) is not None


def test_clone_sync_quote_fallback_exits_partial_with_result_document(
    config_env, monkeypatch, capsys
):
    """Unreachable foreign quote completes the run, emits JSON, exits PolicyError."""
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=types.PeerChannel(2275285084),
        quote_text="что это де-факто не наставничество",
    )
    client = CloneReuploadClient([message(2, reply_to=reply)])
    client.destination_last_id = 1
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    captured = capsys.readouterr()
    assert "reply shape is not supported" not in captured.err
    result = json.loads(captured.out)
    sync = result["sync"]
    assert sync["copied"] == 1
    assert sync["quote_flattened"] == [
        {"id": 2, "peer": 2275285084, "reason": "unreachable"}
    ]
    assert state.load(clone_state.clone_id).cursor == 2


def test_clone_sync_reports_quote_fallback_before_a_later_flood_ends_the_run(
    config_env, monkeypatch, capsys
):
    """The durable copy is behind the cursor after the flood, so stderr is
    the only report that can survive when the tail result document does not."""
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=9,
        reply_to_peer_id=types.PeerChannel(456),
        quote_text="quoted",
    )

    class FloodAfterQuoteCopy(CloneReuploadClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.SendMessageRequest) and any(
                isinstance(item, functions.messages.SendMessageRequest)
                for item in self.requests
            ):
                raise telethon_errors.FloodWaitError(request=request, capture=600)
            return await super().__call__(request)

    client = FloodAfterQuoteCopy(
        [message(2, reply_to=reply), message(3)], protected=True
    )
    client.destination_last_id = 1
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    captured = capsys.readouterr()
    assert "sync" not in json.loads(captured.out)
    assert "source message 2 planted quote fallback (unreachable)" in captured.err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 2
    assert saved.dest_for(2) == 2


def test_clone_sync_quote_fallback_plain_reports_count_and_exits_nonzero(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=9, reply_to_peer_id=types.PeerChannel(99)
    )
    client = CloneReuploadClient([message(2, reply_to=reply)])
    client.destination_last_id = 1
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--plain"]) == 2

    cols = capsys.readouterr().out.rstrip().split("\t")
    assert cols[0] == "1"  # copied
    assert cols[4] == "0"  # reply_flattened
    assert cols[5] == "1"  # quote_flattened_count
    assert cols[10] == clone_state.clone_id


def test_clone_sync_clean_run_has_empty_quote_flattened(
    config_env, monkeypatch, capsys
):
    seed_clone()
    client = CloneSyncClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["quote_flattened"] == []
    assert sync["copied"] == 1


def test_clone_sync_reports_attributed_unmapped_reply_forward_fallback(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneSyncClient(
        [message(2, reply_to=types.MessageReplyHeader(reply_to_msg_id=1))]
    )
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.drop_author is False
    assert sync["reply_flattened"] == 1


def test_clone_sync_flattens_private_dialog_story_reply_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="dialog", title="Alex Smith")
    story_reply = types.MessageReplyStoryHeader(
        peer=types.PeerUser(42),
        story_id=261,
    )
    client = CloneSyncClient([message(2, reply_to=story_reply)])
    client.source = types.User(id=123, first_name="Alex", last_name="Smith")
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.drop_author is False
    assert sync["reply_flattened"] == 1


def test_clone_sync_reuploads_reply_to_mapped_parent_with_quote(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    quote_entities = [types.MessageEntityBold(offset=0, length=5)]
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1,
        quote_text="quote",
        quote_entities=quote_entities,
        quote_offset=2,
    )
    client = CloneReuploadClient([message(2, message="child", reply_to=reply)])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.reply_to.reply_to_msg_id == 1001
    assert request.reply_to.quote_text == "quote"
    assert request.reply_to.quote_entities == quote_entities
    assert request.reply_to.quote_offset == 2
    assert state.load(clone_state.clone_id).dest_for(2) == 1002


def test_clone_sync_reuploads_nested_reply_with_mapped_root(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.record_mapping(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    reply = types.MessageReplyHeader(reply_to_msg_id=2, reply_to_top_id=1)
    client = CloneReuploadClient([message(3, message="nested", reply_to=reply)])
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    [request] = client.requests
    assert request.reply_to.reply_to_msg_id == 1002
    assert request.reply_to.top_msg_id == 1001


def test_clone_sync_preserves_mapped_parent_when_nested_root_is_unmapped(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    reply = types.MessageReplyHeader(reply_to_msg_id=2, reply_to_top_id=1)
    client = CloneReuploadClient([message(3, message="nested", reply_to=reply)])
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.reply_to.reply_to_msg_id == 1002
    assert request.reply_to.top_msg_id is None
    assert sync["reply_flattened"] == 0


def test_clone_sync_blocks_reply_quote_entities_without_quote_text(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1,
        quote_entities=[types.MessageEntityBold(offset=0, length=1)],
    )
    client = CloneReuploadClient([message(2, reply_to=reply)])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "reply quote" in capsys.readouterr().err
    assert state.load(clone_state.clone_id).dest_for(2) is None
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_clone_sync_flattens_reply_without_mapped_parent(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneReuploadClient(
        [message(2, reply_to=types.MessageReplyHeader(reply_to_msg_id=1))]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["reply_flattened"] == 1
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.reply_to is None
    assert state.load(clone_state.clone_id).dest_for(2) == 2
