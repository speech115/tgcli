import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import state


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


def channel(channel_id, title, **overrides):
    values = {
        "id": channel_id,
        "title": title,
        "broadcast": True,
        "megagroup": False,
        "noforwards": False,
        "creator": False,
        "username": None,
        "usernames": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def message(message_id, **overrides):
    values = {
        "id": message_id,
        "message": f"message {message_id}",
        "action": None,
        "media": None,
        "reply_to": None,
        "grouped_id": None,
        "noforwards": False,
        "entities": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def seed_clone():
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    return clone_state


class CloneSyncClient:
    def __init__(self, messages):
        self.source = channel(123, "Source channel")
        self.destination = channel(999, "Source channel", creator=True)
        self.messages = list(messages)
        self.destination_last_id = 1
        self.requests = []
        self.iter_messages_calls = []

    async def get_entity(self, ref):
        if isinstance(ref, types.PeerChannel):
            assert ref.channel_id == 999
            return self.destination
        assert ref == "@source"
        return self.source

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_messages(self, entity, limit=None):
        assert entity is self.destination
        assert limit == 1
        return [SimpleNamespace(id=self.destination_last_id)]

    async def iter_messages(self, entity, *, min_id=0, reverse=False):
        assert entity is self.source
        self.iter_messages_calls.append((min_id, reverse))
        for item in sorted(self.messages, key=lambda value: value.id):
            if item.id > min_id:
                yield item

    async def __call__(self, request):
        assert isinstance(request, functions.messages.ForwardMessagesRequest)
        self.requests.append(request)
        updates = []
        for random_id in request.random_id:
            self.destination_last_id += 1
            updates.append(
                types.UpdateMessageID(id=self.destination_last_id, random_id=random_id)
            )
        return SimpleNamespace(updates=updates)


class CloneReuploadClient(CloneSyncClient):
    def __init__(self, messages, *, protected=False):
        super().__init__(messages)
        self.source.noforwards = protected
        self.downloads = []
        self.uploads = []

    async def download_media(self, message, file=None):
        path = Path(f"{file}.bin")
        path.write_bytes(b"payload")
        self.downloads.append(path)
        return str(path)

    async def upload_file(self, path):
        self.uploads.append(path)
        return types.InputFile(id=len(self.uploads), parts=1,
                               name=Path(path).name, md5_checksum="")

    async def __call__(self, request):
        if isinstance(request, (functions.messages.SendMessageRequest,
                                functions.messages.SendMediaRequest)):
            self.requests.append(request)
            self.destination_last_id += 1
            return SimpleNamespace(updates=[types.UpdateMessageID(
                id=self.destination_last_id, random_id=request.random_id
            )])
        if isinstance(request, functions.messages.UploadMediaRequest):
            self.requests.append(request)
            index = len([item for item in self.requests
                         if isinstance(item, functions.messages.UploadMediaRequest)])
            return types.MessageMediaPhoto(photo=types.Photo(
                id=index, access_hash=index * 11, file_reference=b"ref", date=None,
                sizes=[], dc_id=2,
            ))
        if isinstance(request, functions.messages.SendMultiMediaRequest):
            self.requests.append(request)
            updates = []
            for item in request.multi_media:
                self.destination_last_id += 1
                updates.append(types.UpdateMessageID(
                    id=self.destination_last_id, random_id=item.random_id
                ))
            return SimpleNamespace(updates=updates)
        return await super().__call__(request)


def test_clone_sync_copies_plain_text_oldest_first_and_reruns_idempotently(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(3), message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"] == {
        "copied": 2,
        "skipped_service": 0,
        "skipped_unsupported": [],
        "cursor": 3,
        "more": False,
    }
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
    assert client.iter_messages_calls == [(0, True), (3, True)]


def test_clone_sync_blocks_unexpected_destination_tail_before_copy(
    config_env, monkeypatch, capsys
):
    seed_clone()
    client = CloneSyncClient([message(2)])
    client.destination_last_id = 2
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)["error"]
    assert error["unexpected"] == 1
    assert "unexpected tail" in error["message"]
    assert client.requests == []
    assert client.iter_messages_calls == []
    assert not safety.audit_path().exists()


def test_clone_sync_skips_service_and_reports_unsupported_messages(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    poll_media = type("MessageMediaPoll", (), {})()
    dice_media = type("MessageMediaDice", (), {})()
    client = CloneSyncClient(
        [
            message(1, action=object()),
            message(2, media=poll_media),
            message(3, media=dice_media),
            message(4),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync == {
        "copied": 1,
        "skipped_service": 1,
        "skipped_unsupported": [
            {"id": 2, "kind": "MessageMediaPoll"},
            {"id": 3, "kind": "MessageMediaDice"},
        ],
        "cursor": 4,
        "more": False,
    }
    assert [request.id for request in client.requests] == [[4]]
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 4
    assert saved.dest_for(4) == 2


@pytest.mark.parametrize(
    "media",
    [
        types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=6)),
        types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7)),
        types.MessageMediaDocument(document=types.DocumentEmpty(id=8)),
    ],
)
def test_clone_sync_forwards_native_media_without_flattening_caption(
    media, config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2, media=media, message="photo caption")])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"]["copied"] == 1
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 2
    assert saved.dest_for(2) == 2
    assert [request.id for request in client.requests] == [[2]]
    assert client.requests[0].drop_media_captions is None


def test_clone_sync_keeps_grouped_id_zero_album_atomic_and_in_position(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    document = types.MessageMediaDocument(document=types.DocumentEmpty(id=8))
    client = CloneSyncClient(
        [
            message(2),
            message(3, media=photo, grouped_id=0),
            message(4, media=document, grouped_id=0),
            message(5),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"] == {
        "copied": 4,
        "skipped_service": 0,
        "skipped_unsupported": [],
        "cursor": 5,
        "more": False,
    }
    assert [request.id for request in client.requests] == [[2], [3, 4], [5]]
    assert len(client.requests[1].random_id) == 2
    assert len(set(client.requests[1].random_id)) == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(source_id) for source_id in (2, 3, 4, 5)] == [2, 3, 4, 5]


def test_clone_sync_rejects_duplicate_album_confirmation_atomically(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class DuplicateConfirmationClient(CloneSyncClient):
        async def __call__(self, request):
            response = await super().__call__(request)
            response.updates.append(response.updates[0])
            return response

    client = DuplicateConfirmationClient(
        [message(2, grouped_id=44), message(3, grouped_id=44)]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "complete cloned batch" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.id_map == {}


def test_clone_sync_reuploads_reply_to_mapped_parent_with_quote(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    quote_entities = [types.MessageEntityBold(offset=0, length=5)]
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1, quote_text="quote", quote_entities=quote_entities,
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


def test_clone_sync_blocks_reply_without_mapped_parent_before_audit_or_write(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneReuploadClient([
        message(2, reply_to=types.MessageReplyHeader(reply_to_msg_id=1))
    ])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "parent is not confirmed" in capsys.readouterr().err
    assert state.load(clone_state.clone_id).cursor == 0
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_clone_sync_reuploads_protected_photo_with_caption_and_cleans_tempfile(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient(
        [message(2, message="caption", media=photo)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert isinstance(request.media, types.InputMediaUploadedPhoto)
    assert request.message == "caption"
    assert len(client.uploads) == 1
    assert all(not path.exists() and not path.parent.exists() for path in client.downloads)
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_download_failure_keeps_protected_batch_retryable(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class DownloadFailureClient(CloneReuploadClient):
        async def download_media(self, message, file=None):
            return None

    media = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = DownloadFailureClient([message(2, media=media)], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "media download failed" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.id_map == {}
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_clone_sync_accepts_short_sent_confirmation_for_protected_text(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class ShortSentClient(CloneReuploadClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.SendMessageRequest):
                self.requests.append(request)
                self.destination_last_id += 1
                return types.UpdateShortSentMessage(
                    id=self.destination_last_id, out=True, pts=1, pts_count=1,
                    date=None,
                )
            return await super().__call__(request)

    client = ShortSentClient([message(2, message="protected")], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_reuploads_protected_document_preserving_metadata(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    attributes = [types.DocumentAttributeFilename("report.txt")]
    document = types.MessageMediaDocument(document=SimpleNamespace(
        mime_type="text/plain", attributes=attributes,
    ))
    client = CloneReuploadClient(
        [message(2, message="document", media=document)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert isinstance(request.media, types.InputMediaUploadedDocument)
    assert request.media.mime_type == "text/plain"
    assert request.media.attributes == attributes
    assert request.message == "document"
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_reuploads_protected_album_as_one_ordered_batch(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient(
        [message(2, message="first", media=photo, grouped_id=5),
         message(3, message="second", media=photo, grouped_id=5)],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    uploads = [item for item in client.requests
               if isinstance(item, functions.messages.UploadMediaRequest)]
    [multi] = [item for item in client.requests
               if isinstance(item, functions.messages.SendMultiMediaRequest)]
    assert len(uploads) == 2
    assert [item.message for item in multi.multi_media] == ["first", "second"]
    assert len({item.random_id for item in multi.multi_media}) == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [2, 3]
    assert all(not path.exists() and not path.parent.exists() for path in client.downloads)


def test_clone_sync_reuploads_open_reply_album_to_mapped_parent(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    reply = types.MessageReplyHeader(reply_to_msg_id=1, quote_text="album")
    client = CloneReuploadClient([
        message(2, media=photo, grouped_id=44, reply_to=reply),
        message(3, media=photo, grouped_id=44),
    ])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    [request] = [item for item in client.requests
                 if isinstance(item, functions.messages.SendMultiMediaRequest)]
    assert request.reply_to.reply_to_msg_id == 1001
    assert request.reply_to.quote_text == "album"
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [1002, 1003]


def test_clone_sync_limit_reports_more_and_next_run_resumes(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(4), message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "2", "--json"]) == 0

    first = json.loads(capsys.readouterr().out)["sync"]
    assert first["copied"] == 2
    assert first["cursor"] == 3
    assert first["more"] is True
    assert [request.id for request in client.requests] == [[2], [3]]

    assert main(["clone", "sync", "@source", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)["sync"]
    assert second["copied"] == 1
    assert second["cursor"] == 4
    assert second["more"] is False
    assert [request.id for request in client.requests] == [[2], [3], [4]]
    assert state.load(clone_state.clone_id).dest_for(4) == 4


def test_clone_sync_flood_wait_persists_cooldown_without_advancing(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class FloodClient(CloneSyncClient):
        async def __call__(self, request):
            self.requests.append(request)
            raise telethon_errors.FloodWaitError(request=request, capture=600)

    client = FloodClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.dest_for(2) is None
    assert saved.cooldown_deadline() is not None
    assert len(client.requests) == 1
    assert client.session_mutation_safe is True


def test_clone_sync_readonly_blocks_before_config_or_session(monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["--readonly", "clone", "sync", "@source"]) == 2
    assert not safety.audit_path().exists()


def test_clone_sync_unmatched_confirmation_does_not_advance_state(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class UnmatchedClient(CloneSyncClient):
        async def __call__(self, request):
            response = await super().__call__(request)
            response.updates[0].random_id += 1
            return response

    client = UnmatchedClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "did not confirm" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.id_map == {}
    assert client.destination_last_id == 2


def test_clone_sync_plain_output_has_frozen_columns(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--plain"]) == 0

    assert capsys.readouterr().out.rstrip().split("\t") == [
        "1", "0", "0", "2", clone_state.clone_id, "123", "999", "False"
    ]
