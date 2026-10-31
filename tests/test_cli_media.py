import json
from pathlib import Path

import pytest

from tests.conftest import FakeClient, make_session_fake
from tgcli.cli import main
from tgcli.commands import media as media_cmd

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""

RESULT = {
    "source": "@channel:42",
    "path": "/tmp/clip.bin",
    "bytes": 6,
    "resumed": False,
    "parallel": 1,
}


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def test_media_download_rejects_codec_on_message_source(
    config_env, monkeypatch, capsys
):
    """ADR-0078: --codec is story-only; a message source must not silently ignore it."""
    make_session_fake(monkeypatch, FakeClient())

    assert (
        main(
            [
                "--json",
                "media",
                "download",
                "https://t.me/chan/42",
                "--codec",
                "h264",
            ]
        )
        == 2
    )
    assert "story sources only" in capsys.readouterr().err


def test_media_download_json_reports_progress_only_on_stderr(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, FakeClient())

    async def fake_download(tg, source, account_alias, **kwargs):
        assert source.chat == "@channel"
        assert source.message_id == 42
        assert account_alias == "main"
        kwargs["progress"](3, 6)
        return RESULT

    monkeypatch.setattr(media_cmd, "download_media", fake_download)

    assert main(["--json", "media", "download", "@channel", "42"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == RESULT
    assert captured.err == "downloaded 3/6 bytes\n"


def test_media_download_accepts_complete_tme_link(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient())

    async def fake_download(tg, source, account_alias, **kwargs):
        assert source.chat == "@channel"
        assert source.message_id == 42
        return RESULT

    monkeypatch.setattr(media_cmd, "download_media", fake_download)

    assert main(["--json", "media", "download", "t.me/channel/42"]) == 0
    assert json.loads(capsys.readouterr().out) == RESULT


def _media_message(message_id, kind, *, date=None, size=10, mime=None, filename=None):
    from datetime import UTC, datetime

    from tests.conftest import ns

    file = ns(name=filename, mime_type=mime, size=size)
    markers = {
        "photo": {
            "photo": object(),
            "video": None,
            "audio": None,
            "voice": None,
            "document": None,
        },
        "video": {
            "photo": None,
            "video": object(),
            "audio": None,
            "voice": None,
            "document": None,
        },
        "audio": {
            "photo": None,
            "video": None,
            "audio": object(),
            "voice": None,
            "document": None,
        },
        "voice": {
            "photo": None,
            "video": None,
            "audio": None,
            "voice": object(),
            "document": None,
        },
        "document": {
            "photo": None,
            "video": None,
            "audio": None,
            "voice": None,
            "document": object(),
        },
        "video_note": {
            "photo": None,
            "video": None,
            "audio": None,
            "voice": None,
            "document": None,
            "video_note": object(),
        },
    }[kind]
    return ns(
        id=message_id,
        date=date or datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        sender_id=1,
        sender=None,
        text="",
        media=object(),
        reply_to_msg_id=None,
        file=file,
        **markers,
    )


def test_media_manifest_lists_media_without_downloading(
    config_env, monkeypatch, capsys
):
    from datetime import UTC, datetime

    from tests.conftest import ns

    entity = ns(id=-1001, title="Channel", username="channel")
    messages = [
        _media_message(3, "photo", filename="a.jpg", mime="image/jpeg", size=11),
        ns(
            id=2,
            date=datetime(2026, 7, 20, 11, 0, tzinfo=UTC),
            sender_id=1,
            sender=None,
            text="plain",
            media=None,
            reply_to_msg_id=None,
            file=None,
            photo=None,
            video=None,
            audio=None,
            voice=None,
            document=None,
        ),
        _media_message(1, "video", filename="b.mp4", mime="video/mp4", size=22),
    ]
    client = FakeClient(messages=messages, entities={"@channel": entity})

    async def boom(*args, **kwargs):
        raise AssertionError("download must not run for media manifest")

    client.download_media = boom
    make_session_fake(monkeypatch, client)

    assert main(["--json", "media", "manifest", "@channel"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dialog"] == {"id": -1001, "name": "Channel"}
    assert payload["count"] == 2
    assert payload["items"] == [
        {
            "message_id": 3,
            "type": "photo",
            "size": 11,
            "mime": "image/jpeg",
            "filename": "a.jpg",
        },
        {
            "message_id": 1,
            "type": "video",
            "size": 22,
            "mime": "video/mp4",
            "filename": "b.mp4",
        },
    ]


def test_media_manifest_type_filter(config_env, monkeypatch, capsys):
    from tests.conftest import ns

    entity = ns(id=-1001, title="Channel")
    client = FakeClient(
        messages=[
            _media_message(3, "photo"),
            _media_message(2, "video"),
            _media_message(1, "audio"),
        ],
        entities={"@channel": entity},
    )
    make_session_fake(monkeypatch, client)

    assert main(["--json", "media", "manifest", "@channel", "--type", "video"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["items"][0]["type"] == "video"
    assert payload["items"][0]["message_id"] == 2


def test_media_manifest_type_filter_accepts_video_note(config_env, monkeypatch, capsys):
    from tests.conftest import ns

    entity = ns(id=-1001, title="Channel")
    client = FakeClient(
        messages=[
            _media_message(3, "video"),
            _media_message(2, "video_note"),
            _media_message(1, "audio"),
        ],
        entities={"@channel": entity},
    )
    make_session_fake(monkeypatch, client)

    assert (
        main(["--json", "media", "manifest", "@channel", "--type", "video_note"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["items"][0]["type"] == "video_note"
    assert payload["items"][0]["message_id"] == 2


def test_media_manifest_since_excludes_older(config_env, monkeypatch, capsys):
    from datetime import UTC, datetime

    from tests.conftest import ns

    entity = ns(id=-1001, title="Channel")
    client = FakeClient(
        messages=[
            _media_message(3, "photo", date=datetime(2026, 7, 22, 12, 0, tzinfo=UTC)),
            _media_message(2, "video", date=datetime(2026, 7, 20, 12, 0, tzinfo=UTC)),
            _media_message(1, "audio", date=datetime(2026, 7, 18, 12, 0, tzinfo=UTC)),
        ],
        entities={"@channel": entity},
    )
    make_session_fake(monkeypatch, client)

    assert (
        main(
            [
                "--json",
                "media",
                "manifest",
                "@channel",
                "--since",
                "2026-07-20T00:00:00+00:00",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert [item["message_id"] for item in payload["items"]] == [3, 2]


def test_media_manifest_rejects_bogus_type(capsys):
    assert main(["media", "manifest", "@channel", "--type", "bogus"]) == 1
    assert "invalid choice" in capsys.readouterr().err


HOSTILE_FILENAME = "cl\x1bip\rX\x08Y\nZ\tW.mp4"


def _hostile_manifest_client():
    from tests.conftest import ns

    return FakeClient(
        messages=[_media_message(3, "video", filename=HOSTILE_FILENAME, size=11)],
        entities={"@channel": ns(id=-1001, title="Channel")},
    )


def test_media_manifest_plain_strips_control_characters_from_the_filename(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, _hostile_manifest_client())

    assert main(["--plain", "media", "manifest", "@channel"]) == 0

    out = capsys.readouterr().out
    assert out.endswith("\tclipXYZW.mp4\n")
    assert not any(ch in out[:-1] for ch in "\x1b\r\x08\n")


def test_media_manifest_json_passes_control_characters_through(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, _hostile_manifest_client())

    assert main(["--json", "media", "manifest", "@channel"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["filename"] == HOSTILE_FILENAME


class _StreamingClient(FakeClient):
    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        yield b"abc"[offset:]


def _real_message(message_id, media):
    from datetime import UTC, datetime

    from telethon.tl import types

    return types.Message(
        id=message_id,
        peer_id=types.PeerChannel(5),
        date=datetime(2026, 7, 20, tzinfo=UTC),
        message="",
        media=media,
    )


def _photo_media():
    from datetime import UTC, datetime

    from telethon.tl import types

    photo = types.Photo(
        id=7,
        access_hash=1,
        file_reference=b"",
        date=datetime(2026, 7, 20, tzinfo=UTC),
        sizes=[types.PhotoSize(type="y", w=1, h=1, size=3)],
        dc_id=2,
    )
    return types.MessageMediaPhoto(photo=photo)


def _voice_media():
    from datetime import UTC, datetime

    from telethon.tl import types

    document = types.Document(
        id=8,
        access_hash=1,
        file_reference=b"",
        date=datetime(2026, 7, 20, tzinfo=UTC),
        mime_type="audio/ogg",
        size=3,
        dc_id=2,
        attributes=[types.DocumentAttributeAudio(duration=1, voice=True)],
    )
    return types.MessageMediaDocument(document=document)


@pytest.mark.parametrize(
    ("media", "expected"),
    [(_photo_media, "media-42.jpg"), (_voice_media, "media-42.oga")],
)
def test_media_download_names_an_unnamed_file_by_its_type(
    config_env, monkeypatch, tmp_path, capsys, media, expected
):
    from tests.conftest import ns

    monkeypatch.setenv("HOME", str(tmp_path))
    client = _StreamingClient(
        messages=[_real_message(42, media())],
        entities={"@chan": ns(id=5, title="C")},
    )
    make_session_fake(monkeypatch, client)

    assert main(["--json", "media", "download", "@chan", "42"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == str(tmp_path / "Downloads" / expected)
    assert (tmp_path / "Downloads" / expected).read_bytes() == b"abc"


def test_media_download_resumes_a_checkpoint_from_the_bin_name(
    config_env, monkeypatch, tmp_path, capsys
):
    from tests.conftest import ns

    monkeypatch.setenv("HOME", str(tmp_path))
    media = _photo_media()
    client = _StreamingClient(
        messages=[_real_message(42, media)],
        entities={"@chan": ns(id=5, title="C")},
    )
    make_session_fake(monkeypatch, client)
    # A download from before the typed name was interrupted after one byte.
    source = media_cmd.MediaSource(chat="@chan", message_id=42, private_channel_id=None)
    state_path, part_path = media_cmd._state_paths(source)
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(b"a")
    target = media_cmd._DownloadTarget(media=media, filename="media-42.bin", size=3)
    downloads = tmp_path / "Downloads"
    media_cmd._write_state(state_path, source, downloads / "media-42.bin", target, 1)

    assert main(["--json", "media", "download", "@chan", "42"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["path"] == str(downloads / "media-42.jpg")
    assert data["resumed"] is True
    assert (downloads / "media-42.jpg").read_bytes() == b"abc"


def _interrupted_photo(monkeypatch, tmp_path, destination):
    """A photo download stopped after one byte, recorded for ``destination``."""
    from tests.conftest import ns

    monkeypatch.setenv("HOME", str(tmp_path))
    media = _photo_media()
    client = _StreamingClient(
        messages=[_real_message(42, media)],
        entities={"@chan": ns(id=5, title="C")},
    )
    make_session_fake(monkeypatch, client)
    source = media_cmd.MediaSource(chat="@chan", message_id=42, private_channel_id=None)
    state_path, part_path = media_cmd._state_paths(source)
    part_path.parent.mkdir(parents=True)
    part_path.write_bytes(b"a")
    target = media_cmd._DownloadTarget(media=media, filename="media-42.bin", size=3)
    media_cmd._write_state(state_path, source, destination, target, 1)
    return state_path


def test_media_download_refuses_a_checkpoint_whose_destination_is_not_a_path(
    config_env, monkeypatch, tmp_path, capsys
):
    state_path = _interrupted_photo(monkeypatch, tmp_path, tmp_path / "unused")
    state = json.loads(state_path.read_text())
    state["destination"] = ["not", "a", "path"]
    state_path.write_text(json.dumps(state))

    assert main(["--json", "media", "download", "@chan", "42"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "BLOCKED"
    assert "does not match requested output" in error["error"]["message"]


def test_media_download_explicit_output_does_not_adopt_a_bin_checkpoint(
    config_env, monkeypatch, tmp_path, capsys
):
    out = tmp_path / "out"
    _interrupted_photo(monkeypatch, tmp_path, out / "media-42.bin")

    argv = ["--json", "media", "download", "@chan", "42"]
    assert main([*argv, "--output", str(out / "media-42.png")]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "BLOCKED"
    assert not (out / "media-42.png").exists()


def test_media_download_restarts_when_the_checkpoint_has_no_partial_file(
    config_env, monkeypatch, tmp_path, capsys
):
    downloads = tmp_path / "Downloads"
    state_path = _interrupted_photo(monkeypatch, tmp_path, downloads / "media-42.jpg")
    source = media_cmd.MediaSource(chat="@chan", message_id=42, private_channel_id=None)
    _, part_path = media_cmd._state_paths(source)
    part_path.unlink()

    assert main(["--json", "media", "download", "@chan", "42"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["resumed"] is False
    assert Path(data["path"]).read_bytes() == b"abc"
    assert "restarting it from the beginning" in captured.err
    assert not state_path.exists()
