import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.extensions import markdown
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli import safety, session
from tgcli.cli import main
from tgcli.errors import PolicyError

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


class FailingClient(SendClient):
    async def __call__(self, request):
        raise OSError("connection reset")


class SnapshotClient(SendClient):
    def __init__(self, original: Path, failure: str | None = None):
        super().__init__()
        self.original = original
        self.failure = failure
        self.snapshot_path = None
        self.uploaded_bytes = None

    async def upload_file(self, path):
        self.snapshot_path = Path(path)
        self.original.write_bytes(b"swap")
        self.uploaded_bytes = self.snapshot_path.read_bytes()
        if self.failure == "upload":
            raise OSError("upload failed")
        return await super().upload_file(path)

    async def __call__(self, request):
        if self.failure == "request":
            raise OSError("request failed")
        if self.failure == "confirmation":
            self.requests.append(request)
            return SimpleNamespace(updates=[])
        return await super().__call__(request)


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


def test_send_commit_parses_default_markdown_entities(config_env, monkeypatch):
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "**bold**",
            "file": None,
            "file_size": None,
            "file_sha256": None,
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 783,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 0
    [request] = client.requests
    assert request.message == "bold"
    assert request.entities == [types.MessageEntityBold(offset=0, length=4)]


def test_send_preview_records_format(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert (
        main(["send", "@alice", "<b>hi</b>", "--format", "html", "--preview", "--json"])
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["format"] == "html"
    assert safety.begin_commit(preview["preview_id"])["format"] == "html"
    assert client.requests == []


def test_send_preview_blocks_truncating_html(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert (
        main(
            [
                "send",
                "@alice",
                "if a<b then c",
                "--format",
                "html",
                "--preview",
                "--json",
            ]
        )
        == 2
    )

    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "BLOCKED"
    assert "unterminated html markup" in error["message"]
    assert client.requests == []
    assert list(safety.previews_dir().glob("*")) == []


def test_send_commit_html_sends_entities(config_env, monkeypatch):
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "<b>жир</b> <tg-spoiler>секрет</tg-spoiler>",
            "format": "html",
            "file": None,
            "file_size": None,
            "file_sha256": None,
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 784,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 0
    [request] = client.requests
    assert request.message == "жир секрет"
    assert [type(e) for e in request.entities] == [
        types.MessageEntityBold,
        types.MessageEntitySpoiler,
    ]


def test_send_commit_plain_sends_text_verbatim(config_env, monkeypatch):
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "**literal** <b>tags</b>",
            "format": "plain",
            "file": None,
            "file_size": None,
            "file_sha256": None,
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 785,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 0
    [request] = client.requests
    assert request.message == "**literal** <b>tags</b>"
    assert request.entities is None


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


def test_send_commit_uploads_verified_snapshot_when_original_changes(
    config_env, monkeypatch, tmp_path
):
    document = tmp_path / "report.txt"
    document.write_bytes(b"file")
    preview = file_preview(document, random_id=785)
    client = SnapshotClient(document)
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 0

    assert client.snapshot_path != document
    assert client.uploaded_bytes == b"file"
    assert document.read_bytes() == b"swap"
    assert client.snapshot_path is not None
    assert not client.snapshot_path.exists()
    [request] = client.requests
    assert isinstance(request.media, types.InputMediaUploadedDocument)
    assert request.media.mime_type == "text/plain"
    assert request.media.attributes == [types.DocumentAttributeFilename("report.txt")]


@pytest.mark.parametrize("failure", ["upload", "request", "confirmation"])
def test_send_commit_cleans_verified_snapshot_on_failure(
    config_env, monkeypatch, tmp_path, capsys, failure
):
    document = tmp_path / "report.txt"
    document.write_bytes(b"file")
    preview = file_preview(document, random_id=786)
    client = SnapshotClient(document, failure)
    make_session_fake(monkeypatch, client)

    if failure == "confirmation":
        assert main(["send", "--commit", preview["preview_id"]]) == 2
    else:
        assert main(["send", "--commit", preview["preview_id"]]) == 1
        assert capsys.readouterr().err == f"error: {failure} failed\n"

    assert client.snapshot_path != document
    assert client.uploaded_bytes == b"file"
    assert client.snapshot_path is not None
    assert not client.snapshot_path.exists()


def test_send_commit_parses_default_markdown_caption(config_env, monkeypatch, tmp_path):
    photo = tmp_path / "pic.jpg"
    photo.write_bytes(b"file")
    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "**bold**",
            "file": str(photo),
            "file_size": 4,
            "file_sha256": hashlib.sha256(photo.read_bytes()).hexdigest(),
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 784,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 0
    [request] = client.requests
    assert request.message == "bold"
    assert request.entities == [types.MessageEntityBold(offset=0, length=4)]


def test_send_commit_rejects_file_with_changed_size_before_upload(
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
            "random_id": 781,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    photo.write_bytes(b"changed")
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"]]) == 2
    assert client.uploaded == []
    assert client.requests == []


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

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == {
        "code": "RUNTIME",
        "message": "connection reset",
    }

    working = SendClient()
    make_session_fake(monkeypatch, working)
    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0
    assert working.requests[0].random_id == 779


def test_send_commit_keeps_preview_pending_when_result_audit_fails(
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
            "random_id": 780,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)
    original_append_audit = safety.append_audit

    def fail_result_audit(action, account, details):
        if action == "send-result":
            raise PolicyError("cannot write audit record: disk full")
        original_append_audit(action, account, details)

    monkeypatch.setattr(safety, "append_audit", fail_result_audit)

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 2
    pending = safety.previews_dir() / f"{preview['preview_id']}.pending"
    assert pending.exists()
    assert not pending.with_suffix(".used").exists()

    monkeypatch.setattr(safety, "append_audit", original_append_audit)
    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0
    assert len(client.requests) == 2
    assert {request.random_id for request in client.requests} == {780}


def test_send_commit_rejects_a_non_send_preview_before_session(monkeypatch):
    from tgcli import cli

    preview = safety.create_preview({"kind": "clone-init"})
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["send", "--commit", preview["preview_id"]]) == 2
    assert not safety.audit_path().exists()
    preview_path = safety.previews_dir() / f"{preview['preview_id']}.json"
    assert preview_path.exists()
    assert safety.begin_commit(preview["preview_id"])["kind"] == "clone-init"


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
        session, "client", lambda account: pytest.fail("session opened")
    )
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, value)
        argv = ["send", "--commit", preview["preview_id"]]
    else:
        argv = [flag, "send", "--commit", preview["preview_id"]]

    assert main(argv) == 2
    assert not safety.audit_path().exists()
