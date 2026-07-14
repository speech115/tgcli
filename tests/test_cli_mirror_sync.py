import asyncio
import json
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli import safety
from tgcli.cli import main
from tgcli.mirror.store import MirrorStore, cooldown_deadline


SAMPLE = '''
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
'''


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def channel(channel_id, title, *, creator=False, noforwards=False):
    return SimpleNamespace(
        id=channel_id,
        title=title,
        creator=creator,
        broadcast=True,
        megagroup=False,
        username=None,
        noforwards=noforwards,
    )


def mirror_message(
    message_id,
    text="caption",
    *,
    media=None,
    action=None,
    noforwards=False,
    grouped_id=None,
    reply_to=None,
):
    return SimpleNamespace(
        id=message_id,
        message=text,
        media=media,
        action=action,
        noforwards=noforwards,
        grouped_id=grouped_id,
        reply_to=reply_to,
    )


def text_message(message_id, text, *, noforwards=False):
    return mirror_message(message_id, text, noforwards=noforwards)


def media_message(message_id):
    return mirror_message(
        message_id,
        media=SimpleNamespace(kind="unknown"),
    )


def document_media(*attributes):
    return types.MessageMediaDocument(
        document=SimpleNamespace(attributes=list(attributes))
    )


class MirrorSyncClient:
    def __init__(self, messages, *, noforwards=False):
        self.source = channel(123, "Source channel", noforwards=noforwards)
        self.destination = channel(999, "Source channel", creator=True)
        self.messages = list(messages)
        self.requests = []
        self.iter_messages_calls = []
        self.input_entity_calls = []
        self.events = []
        self.download_media_calls = []
        self.upload_file_calls = []
        self.send_file_calls = []

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_entity(self, ref):
        if ref == "@source":
            return self.source
        if getattr(ref, "channel_id", None) == self.destination.id:
            return self.destination
        raise ValueError(f"unknown entity: {ref!r}")

    async def get_input_entity(self, entity):
        self.input_entity_calls.append(entity)
        return SimpleNamespace(channel_id=entity.id, access_hash=entity.id * 10)

    async def get_messages(self, entity, ids=None, limit=None):
        del entity, limit
        self.events.append(f"get:{ids}")
        return next((message for message in self.messages if message.id == ids), None)

    async def iter_messages(self, entity, *, min_id=0, reverse=False):
        self.iter_messages_calls.append((entity, min_id, reverse))
        self.events.append(f"iter:{min_id}")
        assert reverse is True
        for message in sorted(self.messages, key=lambda item: item.id):
            if message.id > min_id:
                yield message

    async def download_media(self, *args, **kwargs):
        self.download_media_calls.append((args, kwargs))

    async def upload_file(self, *args, **kwargs):
        self.upload_file_calls.append((args, kwargs))

    async def send_file(self, *args, **kwargs):
        self.send_file_calls.append((args, kwargs))

    async def __call__(self, request):
        assert isinstance(request, functions.messages.ForwardMessagesRequest)
        source_message_id = request.id[0]
        store = MirrorStore()
        store.create(42, 123, "Source channel")
        operation = store.prepare_copy(source_message_id)
        assert operation.random_id == request.random_id[0]
        assert operation.destination_message_id is None
        self.events.append(f"forward:{source_message_id}")
        self.requests.append(request)
        return SimpleNamespace(
            updates=[
                types.UpdateMessageID(
                    id=1000 + source_message_id,
                    random_id=request.random_id[0],
                )
            ]
        )


class UnmatchedConfirmationClient(MirrorSyncClient):
    async def __call__(self, request):
        await super().__call__(request)
        return SimpleNamespace(
            updates=[types.UpdateMessageID(id=1001, random_id=request.random_id[0] + 1)]
        )


class ConfirmationEnvelopeClient(MirrorSyncClient):
    def __init__(self, messages, envelope):
        super().__init__(messages)
        self.envelope = envelope

    async def __call__(self, request):
        response = await super().__call__(request)
        update = response.updates[0]
        if self.envelope == "updates":
            return types.Updates(
                updates=[update], users=[], chats=[], date=None, seq=1
            )
        if self.envelope == "updates_combined":
            return types.UpdatesCombined(
                updates=[update],
                users=[],
                chats=[],
                date=None,
                seq_start=1,
                seq=1,
            )
        return types.UpdateShort(update=update, date=None)


class CancellingMirrorSyncClient(MirrorSyncClient):
    async def __call__(self, request):
        await super().__call__(request)
        raise asyncio.CancelledError


class FloodingMirrorSyncClient(MirrorSyncClient):
    async def __call__(self, request):
        await super().__call__(request)
        raise telethon_errors.FloodWaitError(request=None, capture=600)


def authorize_mirror():
    store = MirrorStore()
    store.create(42, 123, "Source channel")
    store.authorize(999)
    return store


def test_mirror_sync_forwards_text_oldest_first_and_restart_adds_no_duplicates(
    config_env, monkeypatch, capsys
):
    store = authorize_mirror()
    client = MirrorSyncClient(
        [text_message(3, "three"), text_message(1, "one"), text_message(2, "two")]
    )
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"] == {"copied": 3, "last_confirmed_message_id": 3}
    assert client.session_mutation_safe is True
    assert [request.id for request in client.requests] == [[1], [2], [3]]
    assert all(request.drop_author is True for request in client.requests)
    assert store.pending_copies() == []
    assert store.last_confirmed_message_id() == 3
    assert [store.prepare_copy(message_id).destination_message_id for message_id in (1, 2, 3)] == [
        1001,
        1002,
        1003,
    ]
    first_random_ids = [request.random_id[0] for request in client.requests]
    assert len(set(first_random_ids)) == 3
    assert len(safety.audit_path().read_text().splitlines()) == 3

    assert main(["mirror", "sync", "@source", "--json"]) == 0

    second = json.loads(capsys.readouterr().out)
    assert second["sync"] == {"copied": 0, "last_confirmed_message_id": 3}
    assert [request.random_id[0] for request in client.requests] == first_random_ids
    assert client.iter_messages_calls[-1][1:] == (3, True)


def test_mirror_sync_recovers_pending_copy_first_with_the_same_random_id(
    config_env, monkeypatch, capsys
):
    store = authorize_mirror()
    store.prepare_copy(1, random_id=111)
    store.confirm_copy(1, destination_message_id=1001)
    pending = store.prepare_copy(2, random_id=-222)
    client = MirrorSyncClient([text_message(2, "two"), text_message(3, "three")])
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"] == {"copied": 2, "last_confirmed_message_id": 3}
    assert client.events[:3] == ["get:2", "forward:2", "iter:2"]
    assert [request.id for request in client.requests] == [[2], [3]]
    assert client.requests[0].random_id == [pending.random_id]
    assert store.pending_copies() == []


@pytest.mark.parametrize("envelope", ["updates", "updates_combined", "update_short"])
def test_mirror_sync_correlates_confirmation_across_update_envelopes(
    config_env, monkeypatch, capsys, envelope
):
    store = authorize_mirror()
    client = ConfirmationEnvelopeClient([text_message(1, "one")], envelope)
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"] == {"copied": 1, "last_confirmed_message_id": 1}
    assert store.pending_copies() == []
    assert store.prepare_copy(1).destination_message_id == 1001


@pytest.mark.asyncio
async def test_mirror_sync_cancellation_reuses_pending_random_id_on_restart(
    config_env,
):
    from tgcli.commands.mirror import sync_text

    store = authorize_mirror()
    cancelled = CancellingMirrorSyncClient([text_message(1, "one")])

    with pytest.raises(asyncio.CancelledError):
        await sync_text(cancelled, "@source", "main")

    [pending] = store.pending_copies()
    assert pending.random_id == cancelled.requests[0].random_id[0]

    restarted = MirrorSyncClient([text_message(1, "one")])
    result = await sync_text(restarted, "@source", "main")

    assert result["sync"] == {"copied": 1, "last_confirmed_message_id": 1}
    assert restarted.requests[0].random_id == [pending.random_id]
    assert store.pending_copies() == []


def test_sync_flood_wait_persists_account_cooldown_and_blocks_next_sync_before_audit_or_write(
    config_env, monkeypatch, capsys
):
    store = authorize_mirror()
    flooded = FloodingMirrorSyncClient([text_message(1, "one")])
    make_session_fake(monkeypatch, flooded)

    assert main(["mirror", "sync", "@source", "--json"]) == 5

    first_error = json.loads(capsys.readouterr().err)
    assert first_error["error"]["code"] == "FLOOD_WAIT"
    assert first_error["error"]["retry_after"] == 600
    deadline = cooldown_deadline(42)
    assert deadline is not None
    [pending] = store.pending_copies()
    assert pending.random_id == flooded.requests[0].random_id[0]
    audit_before_retry = safety.audit_path().read_text()

    blocked = MirrorSyncClient([text_message(1, "one")])
    make_session_fake(monkeypatch, blocked)

    assert main(["mirror", "sync", "@source", "--json"]) == 5

    second_error = json.loads(capsys.readouterr().err)
    assert second_error["error"]["code"] == "FLOOD_WAIT"
    assert second_error["error"]["retry_after"] > 0
    assert blocked.input_entity_calls == []
    assert blocked.events == []
    assert blocked.requests == []
    assert safety.audit_path().read_text() == audit_before_retry
    assert store.pending_copies() == [pending]


def test_mirror_timeout_selection(config_env, monkeypatch, capsys):
    from tgcli import cli

    authorize_mirror()
    client = MirrorSyncClient([])
    make_session_fake(monkeypatch, client)
    observed = []
    original_wait_for = cli.asyncio.wait_for

    async def record_timeout(awaitable, timeout):
        observed.append(timeout)
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(cli.asyncio, "wait_for", record_timeout)

    assert main(["mirror", "sync", "@source", "--json"]) == 0
    capsys.readouterr()
    assert observed == []

    assert main(["mirror", "init", "@source", "--json"]) == 0
    capsys.readouterr()
    assert observed == [60.0]

    assert main(["mirror", "sync", "@source", "--timeout", "12", "--json"]) == 0
    assert observed == [60.0, 12.0]


def test_mirror_sync_reports_resolved_destination_title(
    config_env, monkeypatch, capsys
):
    authorize_mirror()
    client = MirrorSyncClient([])
    client.destination.title = "Manually renamed destination"
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["mirror"]["destination"] == {
        "id": 999,
        "title": "Manually renamed destination",
    }


@pytest.mark.parametrize(
    "media",
    [
        pytest.param(None, id="no-media"),
        pytest.param(
            types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=71)),
            id="webpage",
        ),
        pytest.param(
            types.MessageMediaPhoto(photo=types.PhotoEmpty(id=72)),
            id="photo",
        ),
        pytest.param(document_media(), id="generic-document"),
        pytest.param(
            document_media(types.DocumentAttributeVideo(duration=1, w=16, h=9)),
            id="video-document",
        ),
        pytest.param(
            document_media(types.DocumentAttributeAudio(duration=1, voice=True)),
            id="voice-document",
        ),
        pytest.param(
            document_media(
                types.DocumentAttributeSticker(
                    alt="sticker",
                    stickerset=types.InputStickerSetEmpty(),
                )
            ),
            id="sticker-document",
        ),
    ],
)
def test_mirror_sync_uses_native_forward_for_explicit_single_content_allowlist(
    config_env, monkeypatch, capsys, media
):
    store = authorize_mirror()
    client = MirrorSyncClient([mirror_message(7, media=media)])
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"] == {"copied": 1, "last_confirmed_message_id": 7}
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.id == [7]
    assert request.drop_author is True
    assert request.drop_media_captions is not True
    assert client.download_media_calls == []
    assert client.upload_file_calls == []
    assert client.send_file_calls == []
    assert store.pending_copies() == []
    assert store.destination_message_id(7) == 1007


@pytest.mark.parametrize(
    "message",
    [
        pytest.param(
            mirror_message(1, action=types.MessageActionEmpty()),
            id="service",
        ),
        pytest.param(
            mirror_message(
                1,
                media=types.MessageMediaPaidMedia(
                    stars_amount=1,
                    extended_media=[],
                ),
            ),
            id="paid-media",
        ),
        pytest.param(
            mirror_message(
                1,
                media=types.MessageMediaStory(peer=types.PeerChannel(123), id=9),
            ),
            id="story",
        ),
        pytest.param(
            mirror_message(
                1,
                media=types.MessageMediaPoll(
                    poll=SimpleNamespace(),
                    results=types.PollResults(),
                ),
            ),
            id="poll",
        ),
        pytest.param(
            mirror_message(1, media=SimpleNamespace(kind="unknown-wrapper")),
            id="unknown-wrapper",
        ),
        pytest.param(mirror_message(1, noforwards=True), id="protected"),
        pytest.param(mirror_message(1, grouped_id=0), id="album-zero-group-id"),
        pytest.param(
            mirror_message(1, reply_to=SimpleNamespace(reply_to_msg_id=9)),
            id="reply",
        ),
    ],
)
def test_mirror_sync_blocks_non_allowlisted_or_deferred_content_before_prepare_audit_or_write(
    config_env, monkeypatch, capsys, message
):
    store = authorize_mirror()
    client = MirrorSyncClient([message])
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "BLOCKED"
    assert store.pending_copies() == []
    assert store.last_confirmed_message_id() == 0
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_mirror_sync_stops_at_media_without_marking_it_copied(
    config_env, monkeypatch, capsys
):
    store = authorize_mirror()
    client = MirrorSyncClient(
        [text_message(1, "one"), media_message(2), text_message(3, "three")]
    )
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "BLOCKED"
    assert "not supported" in error["error"]["message"]
    assert [request.id for request in client.requests] == [[1]]
    assert store.last_confirmed_message_id() == 1
    assert store.pending_copies() == []
    assert len(safety.audit_path().read_text().splitlines()) == 1


def test_mirror_sync_refuses_protected_source_before_destination_write(
    config_env, monkeypatch, capsys
):
    store = authorize_mirror()
    client = MirrorSyncClient([text_message(1, "one")], noforwards=True)
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)
    assert "protected" in error["error"]["message"]
    assert client.input_entity_calls == []
    assert client.requests == []
    assert store.last_confirmed_message_id() == 0
    assert not safety.audit_path().exists()


def test_mirror_sync_refuses_protected_new_history_before_prepare_or_write(
    config_env, monkeypatch, capsys
):
    store = authorize_mirror()
    client = MirrorSyncClient([text_message(1, "one", noforwards=True)])
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "BLOCKED"
    assert "protected" in error["error"]["message"]
    assert client.requests == []
    assert store.pending_copies() == []
    assert store.last_confirmed_message_id() == 0
    assert not safety.audit_path().exists()


def test_mirror_sync_keeps_protected_pending_copy_without_replay_or_write(
    config_env, monkeypatch, capsys
):
    store = authorize_mirror()
    pending = store.prepare_copy(1, random_id=-321)
    client = MirrorSyncClient([text_message(1, "one", noforwards=True)])
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "BLOCKED"
    assert "protected" in error["error"]["message"]
    assert client.events == ["get:1"]
    assert client.requests == []
    assert store.pending_copies() == [pending]
    assert store.last_confirmed_message_id() == 0
    assert not safety.audit_path().exists()


def test_mirror_sync_refuses_missing_authorization_before_destination_write(
    config_env, monkeypatch, capsys
):
    store = MirrorStore()
    store.create(42, 123, "Source channel")
    client = MirrorSyncClient([text_message(1, "one")])
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)
    assert "not authorized" in error["error"]["message"]
    assert client.input_entity_calls == []
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_mirror_sync_keeps_operation_pending_without_matching_confirmation(
    config_env, monkeypatch, capsys
):
    store = authorize_mirror()
    client = UnmatchedConfirmationClient([text_message(1, "one")])
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)
    assert "did not confirm" in error["error"]["message"]
    assert store.last_confirmed_message_id() == 0
    pending = store.pending_copies()
    assert len(pending) == 1
    assert pending[0].random_id == client.requests[0].random_id[0]
    assert pending[0].destination_message_id is None


@pytest.mark.parametrize(
    "flag,value",
    [("--readonly", None), ("TGCLI_READONLY", "1"), ("TGCLI_NO_SEND", "1")],
)
def test_mirror_sync_is_blocked_before_config_or_session(monkeypatch, flag, value):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, value)
        argv = ["mirror", "sync", "@source"]
    else:
        argv = [flag, "mirror", "sync", "@source"]

    assert main(argv) == 2
    assert not safety.audit_path().exists()
