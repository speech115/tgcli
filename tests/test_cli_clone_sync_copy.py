"""clone sync: plain copy, author headers, replies and quote fallback."""

import json

from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone import (
    CloneReuploadClient,
    CloneSyncClient,
    legacy_group,
    message,
    seed_clone,
)
from tgcli.cli import main
from tgcli.clone import state


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
    assert [request.peer for request in client.history_requests] == [
        types.PeerChat(chat_id=123)
    ]
    assert state.load(clone_state.clone_id).dest_for(2) is not None


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
