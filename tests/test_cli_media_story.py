"""Story media download tests (ADR-0076): links, resolution, codecs, output."""

import json

import pytest
from telethon.tl import functions, types

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli.cli import main
from tgcli.commands import media as media_cmd
from tgcli.commands.media import MediaSource, parse_source, resolve_message
from tgcli.errors import NotFoundError

CHANNEL = types.Channel(
    id=3817664407,
    title="Socrates",
    photo=None,
    date=None,
    broadcast=True,
    access_hash=123,
)

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


def make_video_document(doc_id, codec, size, name):
    return types.Document(
        id=doc_id,
        access_hash=2,
        file_reference=b"ref",
        date=None,
        mime_type="video/mp4",
        size=size,
        dc_id=1,
        attributes=[
            types.DocumentAttributeVideo(
                duration=3.0, w=720, h=1280, video_codec=codec
            ),
            types.DocumentAttributeFilename(file_name=name),
        ],
        thumbs=None,
    )


def make_story(story_id, *, alts=(), main_codec="h265"):
    main_doc = make_video_document(1, main_codec, 13, "story.mp4")
    alt_docs = [
        make_video_document(10 + index, codec, size, name)
        for index, (codec, size, name) in enumerate(alts)
    ]
    media = types.MessageMediaDocument(document=main_doc, alt_documents=alt_docs)
    return types.StoryItem(id=story_id, date=None, expire_date=None, media=media)


def make_photo_story(story_id):
    media = types.MessageMediaPhoto(
        photo=types.Photo(
            id=1, access_hash=2, file_reference=b"r", date=None, sizes=[], dc_id=1
        )
    )
    return types.StoryItem(id=story_id, date=None, expire_date=None, media=media)


class StoryTelegram:
    """Fake client for story resolution and download."""

    def __init__(self, story):
        self.story = story
        self.requests = []
        self.iter_download_calls = []

    async def get_entity(self, chat):
        assert chat == "@kazbeksocrates"
        return CHANNEL

    async def get_input_entity(self, entity):
        return types.InputPeerChannel(
            channel_id=CHANNEL.id, access_hash=CHANNEL.access_hash
        )

    async def __call__(self, request):
        self.requests.append(request)
        return types.stories.Stories(count=1, stories=[self.story], chats=[], users=[])

    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        self.iter_download_calls.append(media)
        for chunk in (b"ab", b"c"):
            yield chunk


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def test_parse_source_accepts_public_story_link():
    assert parse_source("https://t.me/kazbeksocrates/s/937", None) == MediaSource(
        chat="@kazbeksocrates",
        message_id=None,
        private_channel_id=None,
        story_id=937,
    )


def test_parse_source_accepts_private_story_link():
    assert parse_source("https://t.me/c/3817664407/s/937", None) == MediaSource(
        chat=None, message_id=None, private_channel_id=3817664407, story_id=937
    )


def test_parse_source_accepts_story_link_with_trailing_slash():
    assert parse_source("t.me/kazbeksocrates/s/937/", None).story_id == 937


def test_parse_source_rejects_story_link_with_message_id():
    with pytest.raises(NotFoundError, match="media source"):
        parse_source("https://t.me/kazbeksocrates/s/937", 5)


def test_is_story_link_distinguishes_story_and_message_urls():
    assert media_cmd.is_story_link("t.me/chan/s/937")
    assert media_cmd.is_story_link("https://t.me/c/123/s/937")
    assert not media_cmd.is_story_link("https://t.me/chan/937")
    assert not media_cmd.is_story_link("https://t.me/c/123/937")


def make_text_story(story_id):
    media = types.MessageMediaEmpty()
    return types.StoryItem(id=story_id, date=None, expire_date=None, media=media)


async def test_resolve_text_story_raises_no_downloadable_media():
    """A text/emoji story has no downloadable media: clean NOT_FOUND, not a
    runtime failure later in the download path."""
    fake = StoryTelegram(make_text_story(937))

    with pytest.raises(NotFoundError, match="no downloadable media"):
        await resolve_message(
            fake, parse_source("https://t.me/kazbeksocrates/s/937", None), "main"
        )


async def test_resolve_text_story_with_codec_raises_missing_encoding():
    fake = StoryTelegram(make_text_story(937))

    with pytest.raises(NotFoundError, match="no h264 encoding"):
        await resolve_message(
            fake,
            parse_source("https://t.me/kazbeksocrates/s/937", None),
            "main",
            codec="h264",
        )


async def test_resolve_story_issues_exact_telethon_request():
    fake = StoryTelegram(make_story(937))

    entity, target = await resolve_message(
        fake, parse_source("https://t.me/kazbeksocrates/s/937", None), "main"
    )

    assert entity is CHANNEL
    request = fake.requests[0]
    assert isinstance(request, functions.stories.GetStoriesByIDRequest)
    assert request.id == [937]
    assert isinstance(request.peer, types.InputPeerChannel)
    assert request.peer.channel_id == 3817664407
    assert request.peer.access_hash == 123
    assert target.media is fake.story.media


async def test_resolve_story_selects_alt_document_by_codec():
    fake = StoryTelegram(make_story(937, alts=[("h264", 3, "story_h264.mp4")]))

    _, target = await resolve_message(
        fake,
        parse_source("https://t.me/kazbeksocrates/s/937", None),
        "main",
        codec="h264",
    )

    assert target.codec == "h264"
    assert target.filename == "story_h264.mp4"
    assert target.size == 3


async def test_resolve_story_hevc_alias_matches_h265_main():
    fake = StoryTelegram(make_story(937))

    _, target = await resolve_message(
        fake,
        parse_source("https://t.me/kazbeksocrates/s/937", None),
        "main",
        codec="hevc",
    )

    assert target.codec == "hevc"
    assert target.filename == "story.mp4"


async def test_resolve_story_missing_codec_raises():
    fake = StoryTelegram(make_story(937, alts=[("h264", 3, "story_h264.mp4")]))

    with pytest.raises(NotFoundError, match="no av1 encoding"):
        await resolve_message(
            fake,
            parse_source("https://t.me/kazbeksocrates/s/937", None),
            "main",
            codec="av1",
        )


async def test_resolve_photo_story_with_codec_raises():
    """ADR-0076: `--codec` on a non-video story reports the missing encoding
    instead of silently downloading the photo."""
    fake = StoryTelegram(make_photo_story(937))

    with pytest.raises(NotFoundError, match="no h264 encoding"):
        await resolve_message(
            fake,
            parse_source("https://t.me/kazbeksocrates/s/937", None),
            "main",
            codec="h264",
        )


async def test_resolve_photo_story_without_codec_uses_photo():
    fake = StoryTelegram(make_photo_story(937))

    _, target = await resolve_message(
        fake, parse_source("https://t.me/kazbeksocrates/s/937", None), "main"
    )

    assert target.codec is None
    assert target.filename == "story-937.jpg"


async def test_resolve_story_without_codec_uses_main_document():
    fake = StoryTelegram(make_story(937, alts=[("h264", 3, "story_h264.mp4")]))

    _, target = await resolve_message(
        fake, parse_source("https://t.me/kazbeksocrates/s/937", None), "main"
    )

    assert target.codec is None
    assert target.filename == "story.mp4"
    assert target.size == 13


def make_photo_story_with_sizes(story_id):
    media = types.MessageMediaPhoto(
        photo=types.Photo(
            id=1,
            access_hash=2,
            file_reference=b"r",
            date=None,
            dc_id=1,
            sizes=[
                types.PhotoSize(type="m", w=320, h=480, size=1024),
                types.PhotoSizeProgressive(
                    type="p", w=1080, h=1920, sizes=[4096, 8192]
                ),
            ],
        )
    )
    return types.StoryItem(id=story_id, date=None, expire_date=None, media=media)


async def test_parallel_photo_story_download_uses_progressive_size(
    tmp_path, monkeypatch
):
    """A photo story must report a countable size so --parallel works
    (mirrors Telethon's own byte-count computation)."""
    fake = StoryTelegram(make_photo_story_with_sizes(937))
    calls = []

    async def fake_striped(tg, media, path, *, size, parallel, progress):
        calls.append((size, parallel))
        path.write_bytes(b"data")

    monkeypatch.setattr(media_cmd, "download_striped", fake_striped)
    source = parse_source("https://t.me/kazbeksocrates/s/937", None)

    result = await media_cmd.download_media(
        fake, source, "main", parallel=2, output=str(tmp_path / "s.jpg")
    )

    assert result["parallel"] == 2
    assert result["bytes"] == 4
    assert calls == [(8192, 2)]


async def test_story_download_writes_file_and_reports_codec(tmp_path):
    fake = StoryTelegram(make_story(937, alts=[("h264", 3, "story_h264.mp4")]))
    source = parse_source("https://t.me/kazbeksocrates/s/937", None)

    result = await media_cmd.download_media(
        fake, source, "main", codec="h264", output=str(tmp_path / "s.mp4")
    )

    assert result == {
        "source": "story:@kazbeksocrates:937",
        "path": str(tmp_path / "s.mp4"),
        "bytes": 3,
        "resumed": False,
        "parallel": 1,
        "codec": "h264",
    }
    assert (tmp_path / "s.mp4").read_bytes() == b"abc"
    # The h264 alt document was downloaded, not the main document.
    assert fake.iter_download_calls[0].id == 10


def test_media_download_story_json_output(config_env, monkeypatch, capsys):
    client = FakeClient(entities={"@chan": ns(id=5, title="C")})
    make_session_fake(monkeypatch, client)
    calls = []

    async def fake_download(tg, source, account_alias, **kwargs):
        calls.append((source, kwargs.get("codec")))
        return {
            "source": "story:@chan:937",
            "path": "/tmp/story.mp4",
            "bytes": 3,
            "resumed": False,
            "parallel": 1,
            "codec": "h264",
        }

    monkeypatch.setattr(media_cmd, "download_media", fake_download)

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "https://t.me/chan/s/937",
                "--codec",
                "h264",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["codec"] == "h264"
    source, codec = calls[0]
    assert source.story_id == 937
    assert codec == "h264"


def test_media_download_story_rejects_bulk_flags(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient())

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "https://t.me/chan/s/937",
                "--message-ids",
                "1,2",
            ]
        )
        == 2
    )
    assert "single download only" in capsys.readouterr().err
