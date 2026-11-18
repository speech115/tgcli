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


def _video_document(*extra):
    return types.MessageMediaDocument(
        document=SimpleNamespace(
            mime_type="video/mp4",
            attributes=[
                types.DocumentAttributeVideo(
                    duration=12.5, w=1280, h=720, supports_streaming=True
                ),
                types.DocumentAttributeFilename("clip.mp4"),
                *extra,
            ],
        )
    )


def test_clone_sync_reuploaded_video_stays_a_video(config_env, monkeypatch, capsys):
    """Unset, Telegram may turn a reuploaded video without sound into a GIF;
    `tg send --file` already marks a video `nosound_video`, and so does the
    clone reupload."""
    seed_clone()
    client = CloneReuploadClient(
        [message(2, message="clip", media=_video_document())], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert isinstance(request.media, types.InputMediaUploadedDocument)
    assert request.media.nosound_video is True
    assert request.media.attributes == [
        types.DocumentAttributeVideo(
            duration=12.5, w=1280, h=720, supports_streaming=True
        ),
        types.DocumentAttributeFilename("clip.mp4"),
    ]


def test_clone_sync_reuploaded_gif_stays_a_gif(config_env, monkeypatch, capsys):
    """A source GIF is an MPEG-4 video marked animated: it must not be forced
    into a video."""
    seed_clone()
    client = CloneReuploadClient(
        [message(2, media=_video_document(types.DocumentAttributeAnimated()))],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert request.media.nosound_video is None
    assert types.DocumentAttributeAnimated() in request.media.attributes


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


def test_clone_sync_downloads_large_media_in_four_parallel_streams(
    config_env, monkeypatch, capsys
):
    """Four chunk requests in flight, strided over the file, and the
    reassembled bytes are what gets uploaded."""
    from tgcli.transfer import CHUNK_SIZE

    seed_clone()
    size = 8 * CHUNK_SIZE
    document = types.MessageMediaDocument(document=messages.document(size=size))
    msg = message(2, message="big", media=document)
    calls = []

    class StripedClient(CloneReuploadClient):
        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            calls.append((offset, kw.get("stride"), request_size))
            position = offset
            while position < size:
                yield bytes([position // CHUNK_SIZE]) * CHUNK_SIZE
                position += kw["stride"]

        async def download_media(self, message, file=None):
            raise AssertionError("large media must not use sequential download_media")

    client = StripedClient([msg], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    stride = 4 * CHUNK_SIZE
    assert sorted(calls) == [(i * CHUNK_SIZE, stride, CHUNK_SIZE) for i in range(4)]
    parts = sorted(
        (req for req in client.part_requests),
        key=lambda req: req.file_part,
    )
    assert b"".join(req.bytes for req in parts) == b"".join(
        bytes([index]) * CHUNK_SIZE for index in range(8)
    )


def test_clone_sync_max_runtime_stops_a_large_download_between_chunks(
    config_env, monkeypatch, capsys
):
    """--max-runtime is checked between download chunks, not only between
    batches: a big video stops mid-file and the next run resumes it."""
    from tgcli import pace
    from tgcli.transfer import CHUNK_SIZE

    clone_state = seed_clone()
    size = 8 * CHUNK_SIZE
    document = types.MessageMediaDocument(document=messages.document(size=size))
    msg = message(2, message="big", media=document)
    served = []
    monkeypatch.setattr(pace, "remaining", lambda: 1.0 if len(served) < 2 else 0.0)

    class SlowClient(CloneReuploadClient):
        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            position = offset
            while position < size:
                served.append(position)
                yield b"N" * CHUNK_SIZE
                position += kw.get("stride") or CHUNK_SIZE

        async def download_media(self, message, file=None):
            raise AssertionError("large media must not use sequential download_media")

    client = SlowClient([msg], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--max-runtime", "100", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["stop_reason"] == "wall_clock_cap"
    assert len(served) < 8
    assert client.part_requests == []
    assert state.load(clone_state.clone_id).cursor == 0
    [part] = (state.clones_dir() / f"{clone_state.clone_id}-media").glob("*.part")
    kept = part.stat().st_size
    assert 0 < kept < size and kept % CHUNK_SIZE == 0


def seed_clone_with_discussion_source(*, discussion_source_peer_id=55):
    """A clone whose channel has a linked discussion group the reforward
    search may query, without turning on the (unrelated) comments leg."""
    clone_state = seed_clone()
    clone_state.discussion_source_peer_id = discussion_source_peer_id
    state.save(clone_state)
    return clone_state


class CloneReforwardClient(CloneReuploadClient):
    """A protected-broadcast clone whose source also has a discussion group
    reachable at ``source_group_id``, serving the re-forward proof search."""

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
    """an unedited group copy lets the clone forward the
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


def test_clone_sync_declines_a_proven_original_whose_keyboard_differs(
    config_env, monkeypatch, capsys
):
    """#183: the group copy is the same post, but Telegram did not carry the
    channel's buttons into the group. Forwarding it would publish the wrong
    keyboard under a genuine header, and the native-forward path is the one
    no transport reports — so the proof is declined, the batch falls
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
