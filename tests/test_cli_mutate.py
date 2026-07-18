import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli import safety
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


class MutateClient(FakeClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.edited = []
        self.deleted = []

    async def edit_message(self, chat, message_id, text):
        self.edited.append((chat, message_id, text))
        return ns(id=message_id)

    async def delete_messages(self, chat, ids, revoke=True):
        self.deleted.append((chat, ids, revoke))


def make_client():
    message = ns(
        id=2,
        date=None,
        sender_id=1,
        sender=None,
        text="old",
        media=None,
        reply_to_msg_id=None,
    )
    return MutateClient(messages=[message], entities={"@chan": ns(id=5, title="Chan")})


def test_edit_preview_shows_old_and_new_text(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["edit", "@chan", "2", "new", "--preview", "--json"]) == 0

    preview = json.loads(capsys.readouterr().out)
    assert (preview["old_text"], preview["text"]) == ("old", "new")
    assert client.edited == []


@pytest.mark.parametrize(
    ("command", "arguments", "mutation"),
    [
        ("edit", ["@chan", "2", "new"], "edited"),
        ("delete", ["@chan", "2"], "deleted"),
    ],
)
@pytest.mark.parametrize("flag", ["--readonly", "TGCLI_READONLY", "TGCLI_NO_SEND"])
def test_mutation_preview_is_allowed_by_mutation_gates(
    config_env, monkeypatch, command, arguments, mutation, flag
):
    client = make_client()
    make_session_fake(monkeypatch, client)
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, "1")
        argv = [command, *arguments, "--preview"]
    else:
        argv = [flag, command, *arguments, "--preview"]

    assert main(argv) == 0
    assert getattr(client, mutation) == []


def test_edit_commit_edits_and_audits(config_env, monkeypatch, capsys):
    preview = safety.create_preview(
        {
            "kind": "edit",
            "chat": "@chan",
            "message_id": 2,
            "old_text": "old",
            "text": "new",
        }
    )
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["edit", "--commit", preview["preview_id"], "--json"]) == 0
    assert client.edited == [("@chan", 2, "new")]
    assert main(["edit", "--commit", preview["preview_id"]]) == 2

    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["edit", "edit-result"]


def test_edit_commit_keeps_preview_pending_when_result_audit_fails(
    config_env, monkeypatch
):
    preview = safety.create_preview(
        {
            "kind": "edit",
            "chat": "@chan",
            "message_id": 2,
            "old_text": "old",
            "text": "new",
        }
    )
    client = make_client()
    make_session_fake(monkeypatch, client)
    original_append_audit = safety.append_audit

    def fail_result_audit(action, account, details):
        if action == "edit-result":
            raise PolicyError("cannot write audit record: disk full")
        original_append_audit(action, account, details)

    monkeypatch.setattr(safety, "append_audit", fail_result_audit)

    assert main(["edit", "--commit", preview["preview_id"]]) == 2
    pending = safety.previews_dir() / f"{preview['preview_id']}.pending"
    assert pending.exists()
    assert not pending.with_suffix(".used").exists()

    monkeypatch.setattr(safety, "append_audit", original_append_audit)
    assert main(["edit", "--commit", preview["preview_id"]]) == 0
    assert client.edited == [("@chan", 2, "new"), ("@chan", 2, "new")]


def test_delete_commit_revokes(config_env, monkeypatch, capsys):
    preview = safety.create_preview(
        {"kind": "delete", "chat": "@chan", "message_id": 2, "text": "old"}
    )
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["delete", "--commit", preview["preview_id"], "--json"]) == 0
    assert client.deleted == [("@chan", [2], True)]


def test_kind_mismatch_is_blocked_without_consuming_preview(monkeypatch):
    from tgcli import cli

    preview = safety.create_preview(
        {"kind": "delete", "chat": "@chan", "message_id": 2, "text": "old"}
    )
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["edit", "--commit", preview["preview_id"]]) == 2
    assert safety.consume_preview(preview["preview_id"])["kind"] == "delete"


@pytest.mark.parametrize("argv", [["--readonly", "edit"], ["delete"]])
def test_mutation_preview_requires_complete_positionals(argv, capsys):
    assert main([*argv, "--preview"]) == 1
    assert "requires" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (
            "edit",
            {
                "kind": "edit",
                "chat": "@chan",
                "message_id": 2,
                "old_text": "old",
                "text": "new",
            },
        ),
        (
            "delete",
            {"kind": "delete", "chat": "@chan", "message_id": 2, "text": "old"},
        ),
    ],
)
@pytest.mark.parametrize("flag", ["--readonly", "TGCLI_NO_SEND"])
def test_mutation_commit_gates_before_config_or_session(
    monkeypatch, command, payload, flag
):
    from tgcli import cli

    preview = safety.create_preview(payload)
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )
    if flag == "TGCLI_NO_SEND":
        monkeypatch.setenv(flag, "1")
        argv = [command, "--commit", preview["preview_id"]]
    else:
        argv = [flag, command, "--commit", preview["preview_id"]]

    assert main(argv) == 2
    assert safety.begin_commit(preview["preview_id"])["kind"] == command
