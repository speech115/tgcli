import json
from types import SimpleNamespace

import pytest
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

    async def get_entity(self, chat):
        assert chat == "@alice"
        return SimpleNamespace(id=7, title="Alice")

    async def get_input_entity(self, chat):
        return f"input:{chat}"

    async def upload_file(self, path):
        self.uploaded.append(path)
        return SimpleNamespace(name=path)

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            updates=[types.UpdateMessageID(id=42, random_id=request.random_id)]
        )


class FailingClient(SendClient):
    async def __call__(self, request):
        raise OSError("connection reset")


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
        "file": None,
        "file_size": None,
        "reply_to": None,
        "topic": None,
        "silent": False,
        "to": {"id": 7, "name": "Alice"},
    }
    assert isinstance(random_id, int)


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


def test_send_preview_records_reply_topic_silent(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert (
        main(
            [
                "send",
                "@alice",
                "hi",
                "--reply-to",
                "5",
                "--topic",
                "9",
                "--silent",
                "--preview",
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    stored = safety.begin_commit(preview["preview_id"])
    assert (stored["reply_to"], stored["topic"], stored["silent"]) == (5, 9, True)


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


def test_send_commit_sends_raw_with_stored_random_id(config_env, monkeypatch, capsys):
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "hello",
            "file": None,
            "file_size": None,
            "reply_to": 5,
            "topic": None,
            "silent": True,
            "random_id": 777,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "preview_id": preview["preview_id"],
        "message_id": 42,
    }
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.random_id == 777
    assert request.silent is True
    assert request.reply_to.reply_to_msg_id == 5

    assert main(["send", "--commit", preview["preview_id"]]) == 2

    lines = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert lines[-2]["action"] == "send"
    assert lines[-2]["random_id"] == 777
    assert lines[-1]["action"] == "send-result"
    assert lines[-1]["message_id"] == 42


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
    assert client.uploaded == [str(photo)]


def test_send_commit_is_retryable_after_network_failure(
    config_env, monkeypatch, capsys
):
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "hello",
            "file": None,
            "file_size": None,
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 779,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    make_session_fake(monkeypatch, FailingClient())

    with pytest.raises(OSError, match="connection reset"):
        main(["send", "--commit", preview["preview_id"], "--json"])

    working = SendClient()
    make_session_fake(monkeypatch, working)
    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0
    assert working.requests[0].random_id == 779


def test_send_commit_rejects_a_non_send_preview_before_session(monkeypatch):
    from tgcli import cli

    preview = safety.create_preview({"kind": "clone-init"})
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["send", "--commit", preview["preview_id"]]) == 2
    assert not safety.audit_path().exists()


def test_send_commit_with_extra_args_returns_usage_error(capsys):
    assert main(["send", "--commit", "p_x", "@alice"]) == 1
    assert "send --commit accepts only a preview id" in capsys.readouterr().err


def test_send_without_required_args_returns_usage_error(capsys):
    assert main(["send", "@alice"]) == 1
    assert (
        "send requires CHAT (TEXT | --file PATH) --preview or --commit PREVIEW_ID"
        in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    "flag, value",
    [("--readonly", None), ("TGCLI_READONLY", "1"), ("TGCLI_NO_SEND", "1")],
)
def test_send_commit_is_blocked_before_config_or_session(monkeypatch, flag, value):
    from tgcli import cli

    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "hello",
            "file": None,
            "file_size": None,
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 780,
            "to": {},
        }
    )
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, value)
        argv = ["send", "--commit", preview["preview_id"]]
    else:
        argv = [flag, "send", "--commit", preview["preview_id"]]

    assert main(argv) == 2
    assert not safety.audit_path().exists()
