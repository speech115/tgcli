import json
from types import SimpleNamespace

import pytest

from tests.conftest import make_session_fake
from tgcli import safety
from tgcli.cli import main


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


class SendClient:
    def __init__(self):
        self.sent = []

    async def get_entity(self, chat):
        assert chat == "@alice"
        return SimpleNamespace(id=7, title="Alice")

    async def send_message(self, chat, text):
        self.sent.append((chat, text))
        return SimpleNamespace(id=42)


def test_send_preview_persists_payload_without_sending(config_env, monkeypatch, capsys):
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "@alice", "hello", "--preview", "--json"]) == 0

    preview = json.loads(capsys.readouterr().out)
    assert preview["to"] == {"id": 7, "name": "Alice"}
    assert preview["text"] == "hello"
    assert preview["expires_at"]
    assert client.sent == []

    stored = safety.consume_preview(preview["preview_id"])
    assert stored == {"chat": "@alice", "text": "hello", "to": {"id": 7, "name": "Alice"}}


def test_send_commit_replays_stored_payload_once(config_env, monkeypatch, capsys):
    preview = safety.create_preview(
        {"chat": "@alice", "text": "hello", "to": {"id": 7, "name": "Alice"}}
    )
    client = SendClient()
    make_session_fake(monkeypatch, client)

    assert main(["send", "--commit", preview["preview_id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "preview_id": preview["preview_id"],
        "message_id": 42,
    }
    assert client.sent == [("@alice", "hello")]

    assert main(["send", "--commit", preview["preview_id"]]) == 2


def test_send_commit_with_extra_args_returns_usage_error(capsys):
    assert main(["send", "--commit", "p_x", "@alice"]) == 1
    assert "send --commit accepts only a preview id" in capsys.readouterr().err


def test_send_without_required_args_returns_usage_error(capsys):
    assert main(["send", "@alice"]) == 1
    assert "send requires CHAT TEXT --preview or --commit PREVIEW_ID" in capsys.readouterr().err


@pytest.mark.parametrize("flag, value", [("--readonly", None), ("TGCLI_READONLY", "1"), ("TGCLI_NO_SEND", "1")])
def test_send_commit_is_blocked_before_config_or_session(monkeypatch, flag, value):
    from tgcli import cli

    preview = safety.create_preview({"chat": "@alice", "text": "hello", "to": {}})
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(cli.session, "client", lambda account: pytest.fail("session opened"))
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, value)
        argv = ["send", "--commit", preview["preview_id"]]
    else:
        argv = [flag, "send", "--commit", preview["preview_id"]]

    assert main(argv) == 2
    assert not safety.audit_path().exists()
