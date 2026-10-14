"""clone sync: media reupload, by-reference sends and reforwarding."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.client.downloads import DownloadMethods
from telethon.tl import functions, types

from tests.conftest import (
    TRANSIENT_ERRORS,
    make_session_fake,
    transient_exit_code,
)
from tests.fakes import messages
from tests.fakes.clone import (
    CloneReuploadClient,
    CloneSyncClient,
    _inline_keyboard,
    channel,
    message,
    seed_clone,
)

# A fixture: the redundant alias marks an explicit re-export for ruff.
from tests.fakes.clone_env import config_env as config_env
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import reforward, state


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
    assert all(
        not path.exists() and not path.parent.exists() for path in client.downloads
    )
    assert state.load(clone_state.clone_id).dest_for(2) == 2


@pytest.mark.parametrize(
    "media",
    [
        types.MessageMediaPhoto(spoiler=True, photo=types.PhotoEmpty(id=7)),
        types.MessageMediaDocument(
            spoiler=True,
            document=SimpleNamespace(mime_type="video/mp4", attributes=[]),
        ),
    ],
)
def test_clone_sync_reupload_keeps_source_spoiler(
    config_env, monkeypatch, capsys, media
):
    """A blurred source media must not be republished uncovered."""
    seed_clone()
    client = CloneReuploadClient([message(2, media=media)], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert request.media.spoiler is True


def test_clone_sync_reupload_album_keeps_source_spoiler(
    config_env, monkeypatch, capsys
):
    seed_clone()
    covered = types.MessageMediaPhoto(spoiler=True, photo=types.PhotoEmpty(id=7))
    plain = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=8))
    client = CloneReuploadClient(
        [
            message(2, media=covered, grouped_id=5),
            message(3, media=plain, grouped_id=5),
        ],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [multi] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMultiMediaRequest)
    ]
    assert [item.media.spoiler for item in multi.multi_media] == [True, None]


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
                    id=self.destination_last_id,
                    out=True,
                    pts=1,
                    pts_count=1,
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
    document = types.MessageMediaDocument(
        document=SimpleNamespace(
            mime_type="text/plain",
            attributes=attributes,
        )
    )
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
    # A document Telegram never gave a thumb keeps today's no-thumb upload.
    assert request.media.thumb is None
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def document_media(*, thumbs, mime_type="application/pdf", name="paper.pdf"):
    return types.MessageMediaDocument(
        document=SimpleNamespace(
            mime_type=mime_type,
            attributes=[types.DocumentAttributeFilename(name)],
            thumbs=thumbs,
        )
    )


class CloneThumbClient(CloneReuploadClient):
    """Reupload fake whose thumb download runs Telethon's own size selection.

    ``_get_thumb`` sorts the sizes and drops ``PhotoPathSize`` before it
    indexes, which is exactly what makes a raw ``document.thumbs`` index blow
    up on animated stickers (issue #83).
    """

    def __init__(self, messages, *, protected=False):
        super().__init__(messages, protected=protected)
        self.thumb_downloads = []

    async def download_media(self, message, file=None, thumb=None):
        if thumb is None:
            return await super().download_media(message, file=file)
        size = DownloadMethods._get_thumb(message.media.document.thumbs, thumb)
        self.thumb_downloads.append(size)
        if size is None:
            return None
        path = Path(f"{file}.jpg")
        path.write_bytes(b"thumb")
        return str(path)


def test_clone_sync_reuploads_document_thumb_for_pdf_preview(
    config_env, monkeypatch, capsys
):
    """A still-image thumb is downloaded, uploaded, and attached so Telegram
    renders the native page preview instead of a bare download chip (#79)."""
    clone_state = seed_clone()
    stripped = types.PhotoStrippedSize(type="i", bytes=b"stripped")
    still = types.PhotoSize(type="m", w=320, h=320, size=2048)
    client = CloneThumbClient(
        [message(2, message="paper", media=document_media(thumbs=[stripped, still]))],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert client.thumb_downloads == [still]
    assert isinstance(request.media.thumb, types.InputFile)
    assert request.media.mime_type == "application/pdf"
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_reuploads_sticker_thumb_without_path_size_index_crash(
    config_env, monkeypatch, capsys
):
    """[PhotoPathSize, PhotoSize]: a raw index into document.thumbs would be
    out of range once Telethon drops the path size — the object must be
    selected instead (#83)."""
    clone_state = seed_clone()
    outline = types.PhotoPathSize(type="j", bytes=b"outline")
    still = types.PhotoSize(type="m", w=128, h=128, size=512)
    client = CloneThumbClient(
        [
            message(
                2,
                message="sticker",
                media=document_media(
                    thumbs=[outline, still],
                    mime_type="video/webm",
                    name="sticker.webm",
                ),
            )
        ],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert client.thumb_downloads == [still]
    assert isinstance(request.media.thumb, types.InputFile)
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_continues_when_the_document_thumb_download_fails(
    config_env, monkeypatch, capsys
):
    """A preview is fidelity, not content: a refused thumb omits the thumb and
    finishes the reupload (#83)."""
    clone_state = seed_clone()
    still = types.PhotoSize(type="m", w=320, h=320, size=2048)

    class BrokenThumbClient(CloneThumbClient):
        async def download_media(self, message, file=None, thumb=None):
            if thumb is None:
                return await CloneReuploadClient.download_media(
                    self, message, file=file
                )
            self.thumb_downloads.append(thumb)
            raise telethon_errors.FileReferenceExpiredError(request=None)

    client = BrokenThumbClient(
        [message(2, message="paper", media=document_media(thumbs=[still]))],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert client.thumb_downloads == [still]
    assert request.media.thumb is None
    assert state.load(clone_state.clone_id).dest_for(2) == 2


@pytest.mark.parametrize("make_error", TRANSIENT_ERRORS)
def test_clone_sync_stops_when_the_document_thumb_download_fails_transiently(
    config_env, monkeypatch, capsys, make_error
):
    """A thumb refused for good is dropped (#83), but a 500 is not a refusal:
    dropping it would copy the post without its preview forever."""
    clone_state = seed_clone()
    still = types.PhotoSize(type="m", w=320, h=320, size=2048)
    error = make_error()

    class FailingThumbClient(CloneThumbClient):
        async def download_media(self, message, file=None, thumb=None):
            if thumb is None:
                return await CloneReuploadClient.download_media(
                    self, message, file=file
                )
            raise error

    client = FailingThumbClient(
        [message(2, message="paper", media=document_media(thumbs=[still]))],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == transient_exit_code(error)

    assert client.requests == []
    assert state.load(clone_state.clone_id).dest_for(2) is None


def test_clone_sync_reuploads_protected_album_as_one_ordered_batch(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient(
        [
            message(2, message="first", media=photo, grouped_id=5),
            message(3, message="second", media=photo, grouped_id=5),
        ],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    uploads = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.UploadMediaRequest)
    ]
    [multi] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMultiMediaRequest)
    ]
    assert len(uploads) == 2
    assert [item.message for item in multi.multi_media] == ["first", "second"]
    assert len({item.random_id for item in multi.multi_media}) == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [2, 3]
    assert all(
        not path.exists() and not path.parent.exists() for path in client.downloads
    )


def test_clone_sync_reuploads_open_reply_album_to_mapped_parent(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    reply = types.MessageReplyHeader(reply_to_msg_id=1, quote_text="album")
    client = CloneReuploadClient(
        [
            message(2, media=photo, grouped_id=44, reply_to=reply),
            message(3, media=photo, grouped_id=44),
        ]
    )
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMultiMediaRequest)
    ]
    assert request.reply_to.reply_to_msg_id == 1001
    assert request.reply_to.quote_text == "album"
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [1002, 1003]


def real_photo(photo_id):
    return types.MessageMediaPhoto(
        photo=types.Photo(
            id=photo_id,
            access_hash=photo_id * 7,
            file_reference=b"ref",
            date=None,
            sizes=[],
            dc_id=2,
        )
    )


def test_clone_sync_sends_open_reply_media_by_reference(
    config_env, monkeypatch, capsys
):
    """An unprotected source lets Telegram reuse its file: no download, no
    upload, and the reply still lands on the mapped parent."""
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(reply_to_msg_id=1)
    client = CloneReuploadClient([message(2, media=real_photo(70), reply_to=reply)])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert request.media == types.InputMediaPhoto(
        id=types.InputPhoto(id=70, access_hash=490, file_reference=b"ref")
    )
    assert request.reply_to.reply_to_msg_id == 1001
    assert client.downloads == [] and client.uploads == []
    assert state.load(clone_state.clone_id).dest_for(2) == 1002


def test_clone_sync_sends_open_reply_album_by_reference(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(reply_to_msg_id=1)
    client = CloneReuploadClient(
        [
            message(2, media=real_photo(70), grouped_id=44, reply_to=reply),
            message(3, media=real_photo(71), grouped_id=44),
        ]
    )
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMultiMediaRequest)
    assert [item.media.id.id for item in request.multi_media] == [70, 71]
    assert client.downloads == [] and client.uploads == []


def test_clone_sync_falls_back_to_reupload_when_reference_is_refused(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)

    class RefusingReferenceClient(CloneReuploadClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.SendMediaRequest) and (
                isinstance(request.media, types.InputMediaPhoto)
            ):
                raise telethon_errors.MediaEmptyError(request=request)
            return await super().__call__(request)

    reply = types.MessageReplyHeader(reply_to_msg_id=1)
    client = RefusingReferenceClient([message(2, media=real_photo(70), reply_to=reply)])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    captured = capsys.readouterr()
    assert "media by reference refused" in captured.err
    [request] = client.requests
    assert isinstance(request.media, types.InputMediaUploadedPhoto)
    assert len(client.downloads) == 1
    assert state.load(clone_state.clone_id).dest_for(2) == 1002


@pytest.mark.parametrize("make_error", TRANSIENT_ERRORS)
def test_clone_sync_never_reuploads_after_a_transient_reference_send_failure(
    config_env, monkeypatch, capsys, make_error
):
    """A timeout or 500 on the by-reference send says nothing about whether
    the post landed. Falling back to a reupload could post it twice, so the
    run stops and the next one re-checks the destination tail."""
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    error = make_error()

    class FailingReferenceClient(CloneReuploadClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.SendMediaRequest) and (
                isinstance(request.media, types.InputMediaPhoto)
            ):
                raise error
            return await super().__call__(request)

    reply = types.MessageReplyHeader(reply_to_msg_id=1)
    client = FailingReferenceClient([message(2, media=real_photo(70), reply_to=reply)])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == transient_exit_code(error)

    assert "media by reference refused" not in capsys.readouterr().err
    assert client.requests == []
    assert client.downloads == []
    assert state.load(clone_state.clone_id).dest_for(2) is None


def test_clone_sync_prefixes_attributed_reply_album_caption_once(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="megagroup", title="Team chat")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    reply = types.MessageReplyHeader(reply_to_msg_id=1)
    client = CloneReuploadClient(
        [
            message(
                2,
                message="caption",
                media=photo,
                grouped_id=44,
                reply_to=reply,
                from_id=types.PeerUser(77),
            ),
            message(
                3,
                message="",
                media=photo,
                grouped_id=44,
                from_id=types.PeerUser(77),
            ),
        ]
    )
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMultiMediaRequest)
    ]
    assert [item.message for item in request.multi_media] == [
        "Alex: \n\ncaption",
        "",
    ]


def test_clone_sync_reupload_upload_issues_save_file_part_requests(
    config_env, monkeypatch, capsys
):
    seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))

    class MultiPartClient(CloneReuploadClient):
        async def download_media(self, message, file=None):
            path = Path(f"{file}.bin")
            path.write_bytes(b"x" * (128 * 1024 + 10))
            self.downloads.append(path)
            return str(path)

    client = MultiPartClient(
        [message(2, message="caption", media=photo)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    parts = [
        req
        for req in client.part_requests
        if isinstance(req, functions.upload.SaveFilePartRequest)
    ]
    assert [req.file_part for req in sorted(parts, key=lambda r: r.file_part)] == [0, 1]
    assert len({req.file_id for req in parts}) == 1
    [send] = [
        req
        for req in client.requests
        if isinstance(req, functions.messages.SendMediaRequest)
    ]
    assert isinstance(send.media.file, types.InputFile)
    assert send.media.file.parts == 2


def test_clone_sync_reupload_streams_large_media_in_one_pass(
    config_env, monkeypatch, capsys
):
    """ADR-0083: the download leg is serial and checkpointed, not striped.

    One `iter_download` from the resume offset, no stride: a parallel
    transfer scatters its stripes and no byte count describes what it has,
    which is what made a flood cost the whole file (#169).
    """
    from tgcli.transfer import CHUNK_SIZE

    seed_clone()
    document = types.MessageMediaDocument(
        document=messages.document(size=2 * CHUNK_SIZE)
    )
    msg = message(2, message="big", media=document)

    class StreamingClient(CloneReuploadClient):
        def __init__(self, messages, *, protected=False):
            super().__init__(messages, protected=protected)
            self.iter_download_calls = []

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            self.iter_download_calls.append(
                {"offset": offset, "request_size": request_size, "extra": kw}
            )
            position = offset
            while position < 2 * CHUNK_SIZE:
                yield b"N" * CHUNK_SIZE
                position += CHUNK_SIZE

        async def download_media(self, message, file=None):
            raise AssertionError("large media must not use sequential download_media")

    client = StreamingClient([msg], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    assert [call["offset"] for call in client.iter_download_calls] == [0]
    assert client.iter_download_calls[0]["request_size"] == CHUNK_SIZE
    assert client.iter_download_calls[0]["extra"] == {}
    assert client.part_requests


def test_clone_sync_reupload_download_flood_wait_exits_5(
    config_env, monkeypatch, capsys
):
    from tgcli.transfer import CHUNK_SIZE

    clone_state = seed_clone()
    document = types.MessageMediaDocument(
        document=messages.document(size=2 * CHUNK_SIZE)
    )
    msg = message(2, message="big", media=document)

    class FloodDownloadClient(CloneReuploadClient):
        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            raise telethon_errors.FloodWaitError(request=None, capture=61)
            yield  # pragma: no cover

        async def download_media(self, message, file=None):
            raise AssertionError("large media must not use sequential download_media")

    client = FloodDownloadClient([msg], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] == 61
    assert client.part_requests == []
    assert not any(
        isinstance(
            req,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMessageRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        )
        for req in client.requests
    )
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0


def test_clone_sync_reupload_part_flood_wait_exits_5_without_send(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))

    class FloodPartClient(CloneReuploadClient):
        async def download_media(self, message, file=None):
            path = Path(f"{file}.bin")
            path.write_bytes(b"x" * (128 * 1024 + 10))
            self.downloads.append(path)
            return str(path)

        async def __call__(self, request):
            if isinstance(request, functions.upload.SaveFilePartRequest):
                self.part_requests.append(request)
                raise telethon_errors.FloodWaitError(request=request, capture=90)
            return await super().__call__(request)

    client = FloodPartClient([message(2, media=photo)], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] == 90
    assert not any(
        isinstance(
            req,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMessageRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        )
        for req in client.requests
    )
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0


# --- ADR-0050 Part B: native re-forward of a proven original --------------


def seed_clone_with_discussion_source(*, discussion_source_peer_id=55):
    """A clone whose channel has a linked discussion group the reforward
    search may query, without turning on the (unrelated) comments leg."""
    clone_state = seed_clone()
    clone_state.discussion_source_peer_id = discussion_source_peer_id
    state.save(clone_state)
    return clone_state


class CloneReforwardClient(CloneReuploadClient):
    """A protected-broadcast clone whose source also has a discussion group
    reachable at ``source_group_id``, serving the ADR-0050 Part B search."""

    def __init__(
        self,
        messages,
        *,
        group_messages=(),
        group_noforwards=False,
        source_group_id=55,
        protected=True,
    ):
        super().__init__(messages, protected=protected)
        self.source_group = channel(
            source_group_id,
            "Source discussion",
            broadcast=False,
            megagroup=True,
            forum=False,
            noforwards=group_noforwards,
        )
        self.group_messages = list(group_messages)
        self.search_calls = []

    async def get_entity(self, ref):
        if (
            isinstance(ref, types.PeerChannel)
            and ref.channel_id == self.source_group.id
        ):
            return self.source_group
        return await super().get_entity(ref)

    async def get_messages(
        self, entity, *, limit=None, from_user=None, offset_date=None, ids=None
    ):
        if from_user is not None or offset_date is not None:
            self.search_calls.append(
                SimpleNamespace(
                    entity=entity,
                    from_user=from_user,
                    offset_date=offset_date,
                    limit=limit,
                )
            )
            return self.group_messages
        return await super().get_messages(entity, limit=limit)


REPOST_DATE = datetime(2026, 7, 25, 13, 55, tzinfo=UTC)


def _reposted_message(**overrides):
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    fields = {
        "message": "repost body",
        "media": photo,
        "fwd_from": types.MessageFwdHeader(
            date=REPOST_DATE, from_id=types.PeerUser(user_id=973293498)
        ),
    }
    fields.update(overrides)
    return message(2, **fields)


def test_clone_sync_reforwards_protected_repost_with_proven_original(
    config_env, monkeypatch, capsys
):
    """ADR-0050 Part B: an unedited group copy lets the clone forward the
    real original instead of describing it with a text prefix."""
    seed_clone_with_discussion_source()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    post = _reposted_message()
    group_original = message(501, message="repost body", media=photo, date=REPOST_DATE)
    client = CloneReforwardClient([post], group_messages=[group_original])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    forwards = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.ForwardMessagesRequest)
    ]
    sends = [
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
    assert sends == []
    [forward] = forwards
    assert forward.from_peer is client.source_group
    assert forward.id == [501]
    assert forward.drop_author is False
    assert forward.to_peer is client.destination
    [search] = client.search_calls
    assert search.entity is client.source_group
    assert search.from_user == types.PeerUser(user_id=973293498)
    assert search.offset_date == REPOST_DATE + timedelta(seconds=1)
    assert search.limit == reforward.SEARCH_LIMIT
    assert sync["copied"] == 1
    assert sync["forwarded"] == 1
    assert sync["reuploaded"] == 0
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert [record["action"] for record in audits] == ["clone-sync-reforward"]


def test_clone_sync_falls_back_to_reupload_when_group_copy_was_edited_afterwards(
    config_env, monkeypatch, capsys
):
    """The live `[икона]` 69 case: reposted 13:55, edited 16:18 — the group
    still holds the unedited text, so the clone must not forward it."""
    seed_clone_with_discussion_source()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    post = _reposted_message()
    group_original = message(
        501, message="repost body before the edit", media=photo, date=REPOST_DATE
    )
    client = CloneReforwardClient([post], group_messages=[group_original])

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser) and ref.user_id == 973293498:
            return types.User(id=973293498, first_name="Subscriber", username=None)
        return await CloneReforwardClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    forwards = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.ForwardMessagesRequest)
    ]
    assert forwards == []
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
    assert request.message.startswith("Переслано от ")
    assert sync["forwarded"] == 0
    assert sync["reuploaded"] == 1


def test_clone_sync_protected_repost_without_discussion_source_matches_part_a(
    config_env, monkeypatch, capsys
):
    """No linked source group at all: byte identical to Part A, and the
    search RPC is never spent (there is nowhere to spend it against)."""
    seed_clone()
    post = _reposted_message()
    client = CloneReuploadClient([post], protected=True)

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser) and ref.user_id == 973293498:
            return types.User(id=973293498, first_name="Subscriber", username=None)
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert not any(
        isinstance(item, functions.messages.ForwardMessagesRequest)
        for item in client.requests
    )
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
    assert request.message == "Переслано от Subscriber\n\nrepost body"
    lead_units = len("Переслано от ".encode("utf-16-le")) // 2
    assert request.entities == [
        types.MessageEntityMentionName(
            offset=lead_units,
            length=len("Subscriber".encode("utf-16-le")) // 2,
            user_id=973293498,
        )
    ]
    assert sync["forwarded"] == 0
    assert sync["reuploaded"] == 1


def test_clone_sync_reforward_search_flood_wait_exits_5_without_send(
    config_env, monkeypatch, capsys
):
    """A FloodWait from the reforward search RPC must not be swallowed into
    a Part A fallback — it arms the cooldown, saves state, and stops the
    run, same as any other cooldown-wrapped RPC (ADR-0045)."""
    clone_state = seed_clone_with_discussion_source()
    post = _reposted_message()

    class FloodSearchClient(CloneReforwardClient):
        async def get_messages(
            self, entity, *, limit=None, from_user=None, offset_date=None, ids=None
        ):
            if from_user is not None or offset_date is not None:
                raise telethon_errors.FloodWaitError(request=None, capture=61)
            return await super().get_messages(entity, limit=limit)

    client = FloodSearchClient([post])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] == 61
    assert not any(
        isinstance(
            item,
            (
                functions.messages.ForwardMessagesRequest,
                functions.messages.SendMediaRequest,
                functions.messages.SendMessageRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        )
        for item in client.requests
    )
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0


def test_clone_sync_reforward_group_entity_flood_wait_exits_5_without_send(
    config_env, monkeypatch, capsys
):
    """Same guarantee for the other new RPC: resolving the source discussion
    group itself can flood-wait before any search is even issued."""
    clone_state = seed_clone_with_discussion_source()
    post = _reposted_message()

    class FloodGroupEntityClient(CloneReforwardClient):
        async def get_entity(self, ref):
            if (
                isinstance(ref, types.PeerChannel)
                and ref.channel_id == self.source_group.id
            ):
                raise telethon_errors.FloodWaitError(request=None, capture=61)
            return await super().get_entity(ref)

    client = FloodGroupEntityClient([post])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] == 61
    assert client.search_calls == []
    assert not any(
        isinstance(
            item,
            (
                functions.messages.ForwardMessagesRequest,
                functions.messages.SendMediaRequest,
                functions.messages.SendMessageRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        )
        for item in client.requests
    )
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0


def test_clone_sync_declines_a_proven_original_whose_keyboard_differs(
    config_env, monkeypatch, capsys
):
    """#183: the group copy is the same post, but Telegram did not carry the
    channel's buttons into the group. Forwarding it would publish the wrong
    keyboard under a genuine header, and the native-forward path is the one
    ADR-0085 stays silent about — so the proof is declined, the batch falls
    back to the reupload prefix, and the loss is reported like any other."""
    seed_clone_with_discussion_source()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    post = _reposted_message(reply_markup=_inline_keyboard())
    group_original = message(501, message="repost body", media=photo, date=REPOST_DATE)
    client = CloneReforwardClient([post], group_messages=[group_original])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    captured = capsys.readouterr()
    sync = json.loads(captured.out)["sync"]
    assert [
        item
        for item in client.requests
        if isinstance(item, functions.messages.ForwardMessagesRequest)
    ] == []
    assert sync["forwarded"] == 0
    assert sync["reuploaded"] == 1
    assert [row["id"] for row in sync["markup_dropped"]] == [2]
    assert "source message 2" in captured.err
