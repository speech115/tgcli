import hashlib
import json
import mimetypes
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import RandomIdDuplicateError
from telethon.extensions import markdown
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli import safety
from tgcli.cli import main

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


class SendClient:
    def __init__(self):
        self.requests = []
        self.uploaded = []
        self.file_names = []

    async def get_entity(self, chat):
        assert chat == "@alice"
        return SimpleNamespace(id=7, title="Alice")

    async def get_input_entity(self, chat):
        return f"input:{chat}"

    async def upload_file(self, path, file_name=None):
        self.uploaded.append(path)
        self.file_names.append(file_name)
        return SimpleNamespace(name=path)

    async def _parse_message_text(self, message, parse_mode):
        assert parse_mode == ()
        return markdown.parse(message)

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            updates=[types.UpdateMessageID(id=42, random_id=request.random_id)]
        )


class UnknownChatClient(SendClient):
    """Telethon raises ValueError when a chat reference resolves to nothing."""

    async def get_entity(self, chat):
        raise ValueError(f"Cannot find any entity corresponding to {chat!r}")

    async def get_input_entity(self, chat):
        raise ValueError(f"Cannot find any entity corresponding to {chat!r}")


class AlreadySentClient(SendClient):
    """An earlier attempt was delivered: Telegram refuses the reused random_id."""

    async def __call__(self, request):
        self.requests.append(request)
        raise RandomIdDuplicateError(request=request)


def file_preview(path: Path, *, random_id: int) -> dict:
    return safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "look",
            "file": str(path),
            "file_size": len(path.read_bytes()),
            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": random_id,
            "to": {"id": 7, "name": "Alice"},
        }
    )


def test_send_preview_persists_payload_without_sending(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "@alice", "hello", "--preview", "--json"]) == 0

    preview = json.loads(capsys.readouterr().out)
    assert preview["to"] == {"id": 7, "name": "Alice"}
    assert preview["text"] == "hello"
    assert preview["expires_at"]
    assert client.requests == []

    stored = safety.begin_commit(preview["preview_id"])
    random_id = stored.pop("random_id")
    assert stored == {
        "kind": "send",
        "chat": "@alice",
        "text": "hello",
        "format": "md",
        "file": None,
        "file_size": None,
        "file_sha256": None,
        "reply_to": None,
        "topic": None,
        "silent": False,
        "to": {"id": 7, "name": "Alice"},
    }
    assert isinstance(random_id, int)


@pytest.mark.parametrize("flag", ["--readonly", "TGCLI_READONLY", "TGCLI_NO_SEND"])
def test_send_preview_is_allowed_by_mutation_gates(config_env, monkeypatch, flag):
    client = SendClient()
    make_session_fake(monkeypatch, client)
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, "1")
        argv = ["send", "@alice", "hello", "--preview"]
    else:
        argv = [flag, "send", "@alice", "hello", "--preview"]

    assert main(argv) == 0
    assert client.requests == []


def test_send_preview_with_file_and_caption(config_env, monkeypatch, capsys, tmp_path):
    photo = tmp_path / "pic.jpg"
    photo.write_bytes(b"\xff\xd8fake!!")
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert (
        main(
            [
                "send",
                "@alice",
                "--file",
                str(photo),
                "--caption",
                "look",
                "--preview",
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["file"] == str(photo)
    assert preview["file_size"] == 8
    assert preview["file_sha256"] == hashlib.sha256(photo.read_bytes()).hexdigest()
    assert preview["text"] == "look"

    stored = safety.begin_commit(preview["preview_id"])
    assert stored["kind"] == "send"
    assert stored["random_id"] > 0


def test_send_preview_normalizes_relative_file_path(
    config_env, monkeypatch, capsys, tmp_path
):
    photo = tmp_path / "pic.jpg"
    photo.write_bytes(b"file")
    monkeypatch.chdir(tmp_path)
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "@alice", "--file", "pic.jpg", "--preview", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["file"] == str(photo)
    assert safety.begin_commit(preview["preview_id"])["file"] == str(photo)


def test_send_file_rejects_positional_text(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)
    assert main(["send", "@alice", "hi", "--file", "x.jpg", "--preview"]) == 2


def test_send_caption_requires_file(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)
    assert main(["send", "@alice", "hi", "--caption", "look", "--preview"]) == 2


def test_send_missing_file_is_not_found(config_env, monkeypatch, capsys, tmp_path):
    client = SendClient()
    make_session_fake(monkeypatch, client)
    assert (
        main(
            [
                "send",
                "@alice",
                "--file",
                str(tmp_path / "nope.jpg"),
                "--preview",
            ]
        )
        == 4
    )


def test_send_preview_reports_unknown_dialog_as_not_found(
    config_env, monkeypatch, capsys
):
    client = UnknownChatClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "@gone", "hello", "--preview", "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NOT_FOUND"
    assert client.requests == []


def test_send_commit_reports_unknown_dialog_as_not_found(
    config_env, monkeypatch, capsys
):
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@gone",
            "text": "hello",
            "file": None,
            "file_size": None,
            "file_sha256": None,
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 787,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = UnknownChatClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NOT_FOUND"
    assert client.requests == []


@pytest.mark.parametrize(
    ("reply_to", "topic", "header"),
    [
        (5, None, types.InputReplyToMessage(reply_to_msg_id=5)),
        (None, 9, types.InputReplyToMessage(reply_to_msg_id=9)),
        (5, 9, types.InputReplyToMessage(reply_to_msg_id=5, top_msg_id=9)),
        (None, None, None),
    ],
    ids=["reply", "topic", "reply-in-topic", "neither"],
)
def test_send_commit_places_the_message_by_reply_and_topic(
    config_env, monkeypatch, capsys, reply_to, topic, header
):
    """A reply inside a forum topic needs both ids: without `top_msg_id` it
    lands outside the topic. Only the reply id was ever asserted, so the
    topic could be dropped with every test green."""
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "hello",
            "file": None,
            "file_size": None,
            "reply_to": reply_to,
            "topic": topic,
            "silent": False,
            "random_id": 777,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0

    [request] = client.requests
    assert request.reply_to == header


def test_send_commit_sends_raw_media(config_env, monkeypatch, capsys, tmp_path):
    photo = tmp_path / "pic.jpg"
    photo.write_bytes(b"file")
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "look",
            "file": str(photo),
            "file_size": 4,
            "file_sha256": hashlib.sha256(photo.read_bytes()).hexdigest(),
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 778,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0

    [request] = client.requests
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert isinstance(request.media, types.InputMediaUploadedPhoto)
    assert request.message == "look"
    assert request.random_id == 778
    [uploaded_path] = client.uploaded
    assert Path(uploaded_path) != photo
    assert not Path(uploaded_path).exists()
    # The snapshot has no extension; Telegram rejects a photo upload without one.
    assert client.file_names == ["pic.jpg"]


def test_send_commit_rejects_same_size_file_replacement_before_upload(
    config_env, monkeypatch, tmp_path
):
    photo = tmp_path / "pic.jpg"
    photo.write_bytes(b"file")
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "look",
            "file": str(photo),
            "file_size": 4,
            "file_sha256": hashlib.sha256(photo.read_bytes()).hexdigest(),
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 782,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    photo.write_bytes(b"swap")
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 2
    assert client.uploaded == []
    assert client.requests == []


def test_send_media_commit_reports_already_sent_on_duplicate_random_id(
    config_env, monkeypatch, capsys, tmp_path
):
    path = tmp_path / "photo.png"
    path.write_bytes(b"png")
    preview = file_preview(path, random_id=782)
    client = AlreadySentClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "preview_id": preview["preview_id"],
        "message_id": None,
        "already_sent": True,
    }
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert request.random_id == 782
    used = safety.previews_dir() / f"{preview['preview_id']}.used"
    assert used.exists()
    result = json.loads(safety.audit_path().read_text().splitlines()[-1])
    assert result["action"] == "send-result"
    assert result["already_sent"] is True


def test_send_commit_refuses_while_another_process_commits_the_preview(
    config_env, monkeypatch, capsys
):
    client = SendClient()
    make_session_fake(monkeypatch, client)
    assert main(["send", "@alice", "hello", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    first = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from tgcli import safety; "
            "safety.begin_commit(sys.argv[1]); print('begun', flush=True); "
            "sys.stdin.read()",
            preview_id,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert first.stdout.readline() == "begun\n"
        exit_code = main(["send", "--commit", preview_id, "--json"])
    finally:
        first.communicate("")

    assert exit_code == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "BLOCKED"
    assert "already in progress" in error["message"]
    assert not any(
        isinstance(request, functions.messages.SendMessageRequest)
        for request in client.requests
    )
    assert not safety.audit_path().exists()

    assert main(["send", "--commit", preview_id, "--json"]) == 0
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)


def _box(kind: bytes, payload: bytes) -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _mp4(
    *, timescale: int, duration: int, width: int, height: int, turned: bool = False
) -> bytes:
    mvhd = bytes(4) + bytes(8) + timescale.to_bytes(4, "big")
    mvhd += duration.to_bytes(4, "big") + bytes(80)
    audio_tkhd = bytes(4) + bytes(20) + bytes(52) + bytes(8)
    # Display matrix: a quarter turn swaps a=1 for b=1 (16.16 fixed point).
    unit = (1 << 16).to_bytes(4, "big")
    matrix = bytes(4) + unit if turned else unit + bytes(4)
    video_tkhd = bytes(4) + bytes(20) + bytes(16) + matrix + bytes(28)
    video_tkhd += (width << 16).to_bytes(4, "big") + (height << 16).to_bytes(4, "big")
    moov = _box(b"mvhd", mvhd)
    moov += _box(b"trak", _box(b"tkhd", audio_tkhd))
    moov += _box(b"trak", _box(b"tkhd", video_tkhd))
    # moov after mdat, as in a recording that was never made fast-start.
    return (
        _box(b"ftyp", b"isom" + bytes(4))
        + _box(b"mdat", bytes(64))
        + _box(b"moov", moov)
    )


def _video_request(client, clip, *, random_id, mime, video):
    """The exact SendMediaRequest a video upload must produce."""
    return functions.messages.SendMediaRequest(
        peer="input:@alice",
        message="look",
        entities=[],
        random_id=random_id,
        media=types.InputMediaUploadedDocument(
            file=SimpleNamespace(name=client.uploaded[0]),
            mime_type=mime,
            attributes=[video, types.DocumentAttributeFilename(clip.name)],
            # Unset, Telegram may turn a video without sound into a GIF.
            nosound_video=True,
        ),
    )


def test_send_commit_sends_mp4_as_streamable_video(config_env, monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(_mp4(timescale=1000, duration=12_500, width=1280, height=720))
    preview = file_preview(clip, random_id=790)
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 0

    assert client.requests == [
        _video_request(
            client,
            clip,
            random_id=790,
            mime="video/mp4",
            video=types.DocumentAttributeVideo(
                duration=12.5, w=1280, h=720, supports_streaming=True
            ),
        )
    ]


@pytest.mark.parametrize("name", ["clip.webm", "clip.avi", "clip.mkv"])
def test_send_commit_sends_non_mp4_video_as_video_without_streaming(
    config_env, monkeypatch, tmp_path, name
):
    # Telegram streams only MP4-like containers; claiming it for others
    # breaks playback instead of falling back to a download.
    mime = mimetypes.guess_type(name)[0]  # host tables differ, e.g. avi
    assert mime.startswith("video/")
    clip = tmp_path / name
    clip.write_bytes(b"not an iso media file")
    preview = file_preview(clip, random_id=791)
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 0

    assert client.requests == [
        _video_request(
            client,
            clip,
            random_id=791,
            mime=mime,
            video=types.DocumentAttributeVideo(duration=0, w=1, h=1),
        )
    ]
