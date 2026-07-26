import json

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
