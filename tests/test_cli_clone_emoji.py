"""Clone sync drops custom emoji Telegram can no longer resolve (#316)."""

import json

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.test_cli_clone_sync import (
    SAMPLE,
    CloneReuploadClient,
    message,
    seed_clone,
)
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import state


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def custom(offset, document_id):
    return types.MessageEntityCustomEmoji(
        offset=offset, length=2, document_id=document_id
    )


def emoji_set(set_id):
    return types.InputStickerSetID(id=set_id, access_hash=0)


class EmojiClient(CloneReuploadClient):
    """Telegram refuses a send carrying a ``rejects`` emoji with DOCUMENT_INVALID.

    ``documents`` maps each emoji document Telegram still returns to its set;
    any other id comes back as DocumentEmpty, as live Telegram answers. A set
    in ``invalid_sets`` answers STICKERSET_INVALID.
    """

    def __init__(self, messages, *, rejects, documents, invalid_sets=()):
        super().__init__(messages, protected=True)
        self.rejects = set(rejects)
        self.documents = documents
        self.invalid_sets = set(invalid_sets)
        self.rejected_entities = []

    async def __call__(self, request):
        if isinstance(request, functions.messages.GetCustomEmojiDocumentsRequest):
            self.requests.append(request)
            return [
                types.DocumentEmpty(id=document_id)
                if document_id not in self.documents
                else types.Document(
                    id=document_id,
                    access_hash=0,
                    file_reference=b"",
                    date=None,
                    mime_type="application/x-tgsticker",
                    size=1,
                    dc_id=2,
                    attributes=[
                        types.DocumentAttributeCustomEmoji(
                            alt="*", stickerset=emoji_set(self.documents[document_id])
                        )
                    ],
                )
                for document_id in request.document_id
            ]
        if isinstance(request, functions.messages.GetStickerSetRequest):
            self.requests.append(request)
            if request.stickerset.id in self.invalid_sets:
                raise telethon_errors.StickersetInvalidError(request=None)
            return types.messages.StickerSetNotModified()
        if isinstance(
            request,
            (
                functions.messages.SendMessageRequest,
                functions.messages.SendMediaRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        ):
            bodies = getattr(request, "multi_media", None) or [request]
            entities = [list(body.entities or []) for body in bodies]
            if any(
                isinstance(entity, types.MessageEntityCustomEmoji)
                and entity.document_id in self.rejects
                for body in entities
                for entity in body
            ):
                self.requests.append(request)
                self.rejected_entities.append(entities)
                raise telethon_errors.DocumentInvalidError(request=None)
        return await super().__call__(request)


def audits():
    return [json.loads(line) for line in safety.audit_path().read_text().splitlines()]


def test_clone_sync_drops_only_unavailable_custom_emoji_and_retries(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    bold = types.MessageEntityBold(offset=0, length=4)
    entities = [bold, custom(5, 501), custom(8, 502), custom(11, 503)]
    client = EmojiClient(
        [message(2, message="post 🔥 🎉 ⭐", entities=entities)],
        # 501 is gone, 503's set was deleted, 502 still resolves.
        rejects={501, 503},
        documents={502: 9, 503: 8},
        invalid_sets={8},
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    rejected, lookup, kept_set, gone_set, sent = client.requests
    assert client.rejected_entities == [[entities]]
    assert lookup == functions.messages.GetCustomEmojiDocumentsRequest(
        document_id=[501, 502, 503]
    )
    assert kept_set == functions.messages.GetStickerSetRequest(
        stickerset=emoji_set(9), hash=0
    )
    assert gone_set == functions.messages.GetStickerSetRequest(
        stickerset=emoji_set(8), hash=0
    )
    assert isinstance(sent, functions.messages.SendMessageRequest)
    assert sent.message == "post 🔥 🎉 ⭐"
    assert sent.entities == [bold, custom(8, 502)]
    assert sent.random_id == rejected.random_id
    assert [record["action"] for record in audits()] == [
        "clone-sync-reupload",
        "clone-sync-emoji-fallback",
    ]
    fallback = audits()[-1]
    assert fallback["clone_id"] == clone_state.clone_id
    assert fallback["source_message_ids"] == [2]
    assert fallback["document_ids"] == [501, 503]
    captured = capsys.readouterr()
    assert json.loads(captured.out)["sync"]["copied"] == 1
    assert (
        "warning: clone source message 2: 2 unavailable custom emoji "
        "sent as plain text" in captured.err
    )
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_keeps_document_invalid_when_every_custom_emoji_resolves(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = EmojiClient(
        [message(2, message="post 🔥", entities=[custom(5, 501)])],
        rejects={501},
        documents={501: 9},
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 1

    assert [type(request) for request in client.requests] == [
        functions.messages.SendMessageRequest,
        functions.messages.GetCustomEmojiDocumentsRequest,
        functions.messages.GetStickerSetRequest,
    ]
    assert "document file was invalid" in capsys.readouterr().err
    assert "clone-sync-emoji-fallback" not in safety.audit_path().read_text()
    assert state.load(clone_state.clone_id).dest_for(2) is None


def test_clone_sync_drops_unavailable_custom_emoji_per_album_caption(
    config_env, monkeypatch, capsys
):
    seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = EmojiClient(
        [
            message(
                2,
                grouped_id=77,
                media=photo,
                message="one 🔥",
                entities=[custom(4, 501)],
            ),
            message(
                3,
                grouped_id=77,
                media=photo,
                message="two 🎉",
                entities=[custom(4, 502)],
            ),
        ],
        rejects={501},
        documents={502: 9},
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    rejected, sent = [
        request
        for request in client.requests
        if isinstance(request, functions.messages.SendMultiMediaRequest)
    ]
    assert sent is rejected
    assert [item.entities for item in sent.multi_media] == [[], [custom(4, 502)]]
    assert [item.message for item in sent.multi_media] == ["one 🔥", "two 🎉"]
    assert audits()[-1]["source_message_ids"] == [2]
    captured = capsys.readouterr()
    assert "clone source message 2: 1 unavailable" in captured.err
    assert "clone source message 3" not in captured.err
