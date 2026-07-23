import json
from types import SimpleNamespace

import pytest
from telethon.errors import MessageNotModifiedError

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli import safety
from tgcli.cli import main
from tgcli.errors import PolicyError
from tgcli import session


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
        self.edited_entities = []
        self.deleted = []

    async def edit_message(
        self, chat, message_id, text, *, formatting_entities=None, parse_mode=()
    ):
        self.edited.append((chat, message_id, text))
        self.edited_entities.append(formatting_entities)
        return ns(id=message_id)

    async def delete_messages(self, chat, ids, revoke=True):
        self.deleted.append((chat, ids, revoke))


class ForwardClient(MutateClient):
    async def __call__(self, request):
        from telethon.tl import types

        self.forwarded = request
        return SimpleNamespace(
            updates=[types.UpdateMessageID(id=99, random_id=request.random_id[0])]
        )


class ConvergedEditClient(MutateClient):
    async def edit_message(
        self, chat, message_id, text, *, formatting_entities=None, parse_mode=()
    ):
        self.edited.append((chat, message_id, text))
        self.edited_entities.append(formatting_entities)
        if len(self.edited) > 1:
            raise MessageNotModifiedError(request=None)
        return ns(id=message_id)


class FlakyForwardClient(ForwardClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fail_next_forward = True

    async def __call__(self, request):
        if self.fail_next_forward:
            self.fail_next_forward = False
            raise ConnectionError("connection dropped")
        return await super().__call__(request)


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


def make_forward_client(client_type=ForwardClient):
    return client_type(
        messages=[
            ns(
                id=2,
                date=None,
                sender_id=1,
                sender=None,
                text="old",
                media=None,
                reply_to_msg_id=None,
            )
        ],
        entities={"@chan": ns(id=5, title="Chan"), "@other": ns(id=6, title="Other")},
    )


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


def test_edit_preview_records_format(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert (
        main(
            [
                "edit",
                "@chan",
                "2",
                "до <tg-spoiler>секрет</tg-spoiler>",
                "--format",
                "html",
                "--preview",
                "--json",
            ]
        )
        == 0
    )

    preview = json.loads(capsys.readouterr().out)
    assert preview["format"] == "html"
    assert client.edited == []


def test_edit_commit_html_sends_entities(config_env, monkeypatch, capsys):
    from telethon.tl.types import MessageEntityBold, MessageEntitySpoiler

    preview = safety.create_preview(
        {
            "kind": "edit",
            "chat": "@chan",
            "message_id": 2,
            "old_text": "old",
            "text": "<b>жир</b> <tg-spoiler>секрет</tg-spoiler>",
            "format": "html",
        }
    )
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["edit", "--commit", preview["preview_id"], "--json"]) == 0
    assert client.edited == [("@chan", 2, "жир секрет")]
    entities = client.edited_entities[0]
    assert [type(e) for e in entities] == [MessageEntityBold, MessageEntitySpoiler]


def test_edit_commit_plain_sends_no_entities(config_env, monkeypatch):
    preview = safety.create_preview(
        {
            "kind": "edit",
            "chat": "@chan",
            "message_id": 2,
            "old_text": "old",
            "text": "**literal** <b>tags</b>",
            "format": "plain",
        }
    )
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["edit", "--commit", preview["preview_id"], "--json"]) == 0
    assert client.edited == [("@chan", 2, "**literal** <b>tags</b>")]
    assert client.edited_entities[0] is None


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


def test_edit_commit_retry_converges_after_ambiguous_success(config_env, monkeypatch):
    preview = safety.create_preview(
        {
            "kind": "edit",
            "chat": "@chan",
            "message_id": 2,
            "old_text": "old",
            "text": "new",
        }
    )
    base = make_client()
    client = ConvergedEditClient(messages=base._messages, entities=base._entities)
    make_session_fake(monkeypatch, client)
    original_append_audit = safety.append_audit

    def fail_first_result_audit(action, account, details):
        if action == "edit-result" and len(client.edited) == 1:
            raise PolicyError("cannot write audit record: disk full")
        original_append_audit(action, account, details)

    monkeypatch.setattr(safety, "append_audit", fail_first_result_audit)

    assert main(["edit", "--commit", preview["preview_id"]]) == 2
    assert main(["edit", "--commit", preview["preview_id"]]) == 0
    assert client.edited == [("@chan", 2, "new"), ("@chan", 2, "new")]
    assert not (safety.previews_dir() / f"{preview['preview_id']}.pending").exists()


def test_delete_commit_revokes(config_env, monkeypatch, capsys):
    preview = safety.create_preview(
        {"kind": "delete", "chat": "@chan", "message_id": 2, "text": "old"}
    )
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["delete", "--commit", preview["preview_id"], "--json"]) == 0
    assert client.deleted == [("@chan", [2], True)]


def test_forward_commit_uses_stored_random_id(config_env, monkeypatch, capsys):
    preview = safety.create_preview(
        {
            "kind": "forward",
            "source": "@chan",
            "message_id": 2,
            "destination": "@other",
            "text": "old",
            "random_id": 555,
        }
    )
    client = make_forward_client()
    make_session_fake(monkeypatch, client)

    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["message_id"] == 99
    assert client.forwarded.from_peer == "@chan"
    assert client.forwarded.to_peer == "@other"
    assert client.forwarded.random_id == [555]
    assert client.forwarded.id == [2]
    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["forward", "forward-result"]


def test_forward_preview_stores_chat_refs_and_random_id(
    config_env, monkeypatch, capsys
):
    client = make_forward_client()
    make_session_fake(monkeypatch, client)

    assert main(["forward", "@chan", "2", "@other", "--preview", "--json"]) == 0

    preview = json.loads(capsys.readouterr().out)
    stored = safety.begin_commit(preview["preview_id"], expected_kind="forward")
    assert preview == {
        "preview_id": preview["preview_id"],
        "source": "@chan",
        "message_id": 2,
        "destination": "@other",
        "text": "old",
        "expires_at": preview["expires_at"],
    }
    assert stored["source"] == "@chan"
    assert stored["destination"] == "@other"
    assert isinstance(stored["random_id"], int)
    assert stored["random_id"] > 0


def test_forward_commit_retries_pending_preview_after_network_failure(
    config_env, monkeypatch, capsys
):
    preview = safety.create_preview(
        {
            "kind": "forward",
            "source": "@chan",
            "message_id": 2,
            "destination": "@other",
            "text": "old",
            "random_id": 555,
        }
    )
    client = make_forward_client(FlakyForwardClient)
    make_session_fake(monkeypatch, client)

    with pytest.raises(ConnectionError, match="connection dropped"):
        main(["forward", "--commit", preview["preview_id"]])
    pending = safety.previews_dir() / f"{preview['preview_id']}.pending"
    assert pending.exists()

    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["message_id"] == 99
    assert not pending.exists()
    assert pending.with_suffix(".used").exists()


def test_mark_read_needs_no_preview_but_respects_readonly(
    config_env, monkeypatch, capsys
):
    client = make_client()

    async def ack(entity):
        client.acked = entity

    client.send_read_acknowledge = ack
    make_session_fake(monkeypatch, client)

    assert main(["mark-read", "@chan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["marked_read"] is True
    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["mark-read"]
    assert main(["--readonly", "mark-read", "@chan"]) == 2


@pytest.mark.parametrize("flag", ["--readonly", "TGCLI_READONLY", "TGCLI_NO_SEND"])
def test_mark_read_gates_before_config_or_session(monkeypatch, flag):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, "1")
        argv = ["mark-read", "@chan"]
    else:
        argv = [flag, "mark-read", "@chan"]

    assert main(argv) == 2


def test_mark_unread_records_request_and_audit(config_env, monkeypatch, capsys):
    from telethon.tl import functions, types

    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["mark-unread", "@chan", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"dialog": {"id": 5}, "marked_unread": True}
    assert len(client.call_requests) == 1
    request = client.call_requests[0]
    assert isinstance(request, functions.messages.MarkDialogUnreadRequest)
    assert request.unread is True
    assert isinstance(request.peer, types.InputDialogPeer)
    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["mark-unread"]
    assert main(["--readonly", "mark-unread", "@chan"]) == 2


@pytest.mark.parametrize("flag", ["--readonly", "TGCLI_READONLY", "TGCLI_NO_SEND"])
def test_mark_unread_gates_before_config_or_session(monkeypatch, flag):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, "1")
        argv = ["mark-unread", "@chan"]
    else:
        argv = [flag, "mark-unread", "@chan"]

    assert main(argv) == 2


def test_kind_mismatch_is_blocked_without_consuming_preview(monkeypatch):
    from tgcli import cli

    preview = safety.create_preview(
        {"kind": "delete", "chat": "@chan", "message_id": 2, "text": "old"}
    )
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
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
        session, "client", lambda account: pytest.fail("session opened")
    )
    if flag == "TGCLI_NO_SEND":
        monkeypatch.setenv(flag, "1")
        argv = [command, "--commit", preview["preview_id"]]
    else:
        argv = [flag, command, "--commit", preview["preview_id"]]

    assert main(argv) == 2
    assert safety.begin_commit(preview["preview_id"])["kind"] == command
