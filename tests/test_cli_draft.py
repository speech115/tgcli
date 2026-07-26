"""CLI coverage for `tg draft` (ADR-0039)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from telethon.errors import MessageNotModifiedError
from telethon.tl import functions, types

from tests.conftest import FakeClient, make_session_fake, ns
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


def _user(user_id=5, username="alice", first_name="Alice"):
    return ns(
        id=user_id,
        username=username,
        first_name=first_name,
        last_name=None,
        title=None,
        bot=False,
        contact=True,
    )


def _peer_dialogs(draft_tl, entity):
    return ns(
        dialogs=[ns(peer=types.PeerUser(user_id=entity.id), draft=draft_tl)],
        users=[entity],
        chats=[],
    )


def _all_drafts(items):
    updates = []
    users = []
    chats = []
    for entity, draft_tl in items:
        updates.append(ns(peer=types.PeerUser(user_id=entity.id), draft=draft_tl))
        users.append(entity)
    return ns(updates=updates, users=users, chats=chats)


def test_draft_show_empty(config_env, monkeypatch, capsys):
    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(types.DraftMessageEmpty(), entity),
    )
    make_session_fake(monkeypatch, client)

    assert main(["draft", "show", "@alice", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data == {
        "draft": {
            "chat": {"id": 5, "name": "Alice"},
            "text": "",
            "custom_emoji": [],
            "reply_to_msg_id": None,
            "topic_id": None,
            "date": None,
            "is_empty": True,
        }
    }
    assert any(
        isinstance(request, functions.messages.GetPeerDialogsRequest)
        for request in client.call_requests
    )
    request = next(
        request
        for request in client.call_requests
        if isinstance(request, functions.messages.GetPeerDialogsRequest)
    )
    assert len(request.peers) == 1
    assert isinstance(request.peers[0], types.InputDialogPeer)


def test_draft_show_with_text_reply_and_topic(config_env, monkeypatch, capsys):
    entity = _user()
    when = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    draft_tl = types.DraftMessage(
        message="hello",
        date=when,
        reply_to=types.InputReplyToMessage(reply_to_msg_id=42, top_msg_id=7),
        entities=[
            types.MessageEntityCustomEmoji(offset=0, length=2, document_id=99),
        ],
    )
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(draft_tl, entity),
    )
    make_session_fake(monkeypatch, client)

    assert main(["draft", "show", "@alice", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)["draft"]
    assert data["text"] == "hello"
    assert data["reply_to_msg_id"] == 42
    assert data["topic_id"] == 7
    assert data["date"] == "2026-07-23T12:00:00+00:00"
    assert data["is_empty"] is False
    assert data["custom_emoji"] == [
        {"id": "99", "emoji": "he", "offset": 0, "length": 2}
    ]


def test_draft_list(config_env, monkeypatch, capsys):
    alice = _user(5, "alice", "Alice")
    bob = _user(6, "bob", "Bob")
    client = FakeClient(
        all_drafts_result=_all_drafts(
            [
                (alice, types.DraftMessage(message="a", date=None)),
                (bob, types.DraftMessage(message="b", date=None)),
            ]
        )
    )
    make_session_fake(monkeypatch, client)

    assert main(["draft", "list", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert [draft["chat"]["id"] for draft in data["drafts"]] == [5, 6]
    assert [draft["text"] for draft in data["drafts"]] == ["a", "b"]
    assert any(
        isinstance(request, functions.messages.GetAllDraftsRequest)
        for request in client.call_requests
    )


def test_draft_show_plain_rows(config_env, monkeypatch, capsys):
    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(
            types.DraftMessage(message="hi", date=None), entity
        ),
    )
    make_session_fake(monkeypatch, client)

    assert main(["draft", "show", "@alice", "--plain"]) == 0
    assert capsys.readouterr().out == "5\tAlice\thi\tFalse\n"


def test_draft_set_preview_and_commit_saves_exact_request(
    config_env, monkeypatch, capsys
):
    entity = _user()

    class CaptureClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.save_results: list[object] = []

        async def __call__(self, request):
            result = await super().__call__(request)
            if isinstance(request, functions.messages.SaveDraftRequest):
                self.save_results.append(result)
            return result

    client = CaptureClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(
            types.DraftMessage(message="old", date=None), entity
        ),
    )

    async def input_peer(key):
        return types.InputPeerUser(user_id=5, access_hash=7)

    client.get_input_entity = input_peer  # type: ignore[method-assign]
    make_session_fake(monkeypatch, client)

    assert (
        main(
            [
                "draft",
                "set",
                "@alice",
                "**hi**",
                "--reply-to",
                "42",
                "--topic",
                "7",
                "--preview",
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["old_text"] == "old"
    assert preview["text"] == "**hi**"
    assert preview["format"] == "md"
    assert preview["reply_to"] == 42
    assert preview["topic"] == 7
    assert preview["to"] == {"id": 5, "name": "Alice"}
    assert "old_state" not in preview

    assert main(["draft", "set", "--commit", preview["preview_id"], "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["preview_id"] == preview["preview_id"]
    assert result["draft"]["text"] == "hi"
    assert result["draft"]["is_empty"] is False
    assert result["draft"]["date"] is not None
    assert result["draft"]["reply_to_msg_id"] == 42
    assert result["draft"]["topic_id"] == 7

    saves = [
        request
        for request in client.call_requests
        if isinstance(request, functions.messages.SaveDraftRequest)
    ]
    assert len(saves) == 1
    request = saves[0]
    assert isinstance(request.peer, types.InputPeerUser)
    assert request.peer.user_id == 5
    assert request.message == "hi"
    assert isinstance(request.entities, list)
    assert isinstance(request.reply_to, types.InputReplyToMessage)
    assert request.reply_to.reply_to_msg_id == 42
    assert request.reply_to.top_msg_id == 7
    assert len(client.save_results) == 1
    assert type(client.save_results[0]) is bool
    assert client.save_results[0] is True


def test_draft_clear_preview_and_commit(config_env, monkeypatch, capsys):
    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(
            types.DraftMessage(message="wipe me", date=None), entity
        ),
    )

    async def input_peer(key):
        return types.InputPeerUser(user_id=5, access_hash=7)

    client.get_input_entity = input_peer  # type: ignore[method-assign]
    make_session_fake(monkeypatch, client)

    assert main(["draft", "clear", "@alice", "--preview", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["old_text"] == "wipe me"
    assert "text" not in preview
    assert "old_state" not in preview

    assert main(["draft", "clear", "--commit", preview["preview_id"], "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["draft"]["is_empty"] is True
    assert result["draft"]["text"] == ""

    saves = [
        request
        for request in client.call_requests
        if isinstance(request, functions.messages.SaveDraftRequest)
    ]
    assert len(saves) == 1
    assert saves[0].message == ""
    assert saves[0].reply_to is None


def test_draft_set_commit_treats_not_modified_as_success(
    config_env, monkeypatch, capsys
):
    """Telegram reports an identical draft with MessageNotModifiedError."""
    entity = _user()

    class NoOpClient(FakeClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.SaveDraftRequest):
                raise MessageNotModifiedError(request=request)
            return await super().__call__(request)

    client = NoOpClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(
            types.DraftMessage(message="same", date=None), entity
        ),
    )
    make_session_fake(monkeypatch, client)

    assert main(["draft", "set", "@alice", "same", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["draft", "set", "--commit", preview_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["draft"]["text"] == "same"


@pytest.mark.parametrize(
    ("draft_command", "preview_args"),
    [("set", ["agent text"]), ("clear", [])],
)
def test_draft_commit_rejects_a_draft_changed_after_preview(
    config_env, monkeypatch, capsys, draft_command, preview_args
):
    entity = _user()
    peer_dialogs = _peer_dialogs(types.DraftMessage(message="old", date=None), entity)
    client = FakeClient(entities={"@alice": entity}, peer_dialogs_result=peer_dialogs)
    make_session_fake(monkeypatch, client)

    assert (
        main(["draft", draft_command, "@alice", *preview_args, "--preview", "--json"])
        == 0
    )
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    peer_dialogs.dialogs[0].draft = types.DraftMessage(
        message="human update", date=None
    )

    assert main(["draft", draft_command, "--commit", preview_id, "--json"]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "BLOCKED"
    assert not any(
        isinstance(request, functions.messages.SaveDraftRequest)
        for request in client.call_requests
    )


def test_draft_set_commit_rejects_same_text_reply_change(
    config_env, monkeypatch, capsys
):
    entity = _user()
    peer_dialogs = _peer_dialogs(
        types.DraftMessage(
            message="same",
            date=None,
            reply_to=types.InputReplyToMessage(reply_to_msg_id=42),
        ),
        entity,
    )
    client = FakeClient(entities={"@alice": entity}, peer_dialogs_result=peer_dialogs)
    make_session_fake(monkeypatch, client)

    assert main(["draft", "set", "@alice", "same", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    peer_dialogs.dialogs[0].draft = types.DraftMessage(
        message="same",
        date=None,
        reply_to=types.InputReplyToMessage(reply_to_msg_id=43),
    )

    assert main(["draft", "set", "--commit", preview_id, "--json"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "BLOCKED"
    assert not any(
        isinstance(request, functions.messages.SaveDraftRequest)
        for request in client.call_requests
    )


def test_draft_set_commit_rejects_same_text_topic_change(
    config_env, monkeypatch, capsys
):
    entity = _user()
    peer_dialogs = _peer_dialogs(
        types.DraftMessage(
            message="same",
            date=None,
            reply_to=types.InputReplyToMessage(reply_to_msg_id=42, top_msg_id=7),
        ),
        entity,
    )
    client = FakeClient(entities={"@alice": entity}, peer_dialogs_result=peer_dialogs)
    make_session_fake(monkeypatch, client)

    assert main(["draft", "set", "@alice", "same", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    peer_dialogs.dialogs[0].draft = types.DraftMessage(
        message="same",
        date=None,
        reply_to=types.InputReplyToMessage(reply_to_msg_id=42, top_msg_id=8),
    )

    assert main(["draft", "set", "--commit", preview_id, "--json"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "BLOCKED"
    assert not any(
        isinstance(request, functions.messages.SaveDraftRequest)
        for request in client.call_requests
    )


def test_draft_set_commit_rejects_same_text_entity_change(
    config_env, monkeypatch, capsys
):
    entity = _user()
    peer_dialogs = _peer_dialogs(
        types.DraftMessage(
            message="same",
            date=None,
            entities=[types.MessageEntityBold(offset=0, length=4)],
        ),
        entity,
    )
    client = FakeClient(entities={"@alice": entity}, peer_dialogs_result=peer_dialogs)
    make_session_fake(monkeypatch, client)

    assert main(["draft", "set", "@alice", "same", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    peer_dialogs.dialogs[0].draft = types.DraftMessage(
        message="same",
        date=None,
        entities=[types.MessageEntityItalic(offset=0, length=4)],
    )

    assert main(["draft", "set", "--commit", preview_id, "--json"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "BLOCKED"
    assert not any(
        isinstance(request, functions.messages.SaveDraftRequest)
        for request in client.call_requests
    )


def test_draft_preview_serializes_formatted_date_entity(
    config_env, monkeypatch, capsys
):
    entity = _user()
    when = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    formatted_date = getattr(types, "MessageEntityFormattedDate")
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(
            types.DraftMessage(
                message="when",
                date=None,
                entities=[formatted_date(offset=0, length=4, date=when)],
            ),
            entity,
        ),
    )
    make_session_fake(monkeypatch, client)

    assert main(["draft", "set", "@alice", "next", "--preview", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    stored = json.loads(
        (safety.previews_dir() / f"{preview['preview_id']}.json").read_text()
    )
    assert stored["payload"]["old_state"]["entities"][0]["date"] == when.isoformat()


def test_draft_set_commit_rejects_extra_flags(config_env, monkeypatch, capsys):
    stored = safety.create_preview(
        {
            "kind": "draft-set",
            "chat": "@alice",
            "old_text": "",
            "text": "x",
            "format": "md",
            "reply_to": None,
            "topic": None,
            "to": {"id": 5, "name": "Alice"},
        }
    )
    assert main(["draft", "set", "--commit", stored["preview_id"], "@alice", "x"]) == 1
    assert "draft set --commit accepts only a preview id" in capsys.readouterr().err


def test_draft_set_commit_rejects_format_flag(config_env, capsys):
    stored = safety.create_preview(
        {
            "kind": "draft-set",
            "chat": "@alice",
            "old_text": "",
            "text": "x",
            "format": "md",
            "reply_to": None,
            "topic": None,
            "to": {"id": 5, "name": "Alice"},
        }
    )

    assert (
        main(["draft", "set", "--commit", stored["preview_id"], "--format", "plain"])
        == 1
    )
    assert "draft set --commit accepts only a preview id" in capsys.readouterr().err


def test_draft_set_rejects_wrong_preview_kind(config_env, monkeypatch, capsys):
    stored = safety.create_preview(
        {
            "kind": "edit",
            "chat": "@alice",
            "message_id": 1,
            "old_text": "",
            "text": "x",
            "format": "plain",
        }
    )
    assert main(["draft", "set", "--commit", stored["preview_id"], "--json"]) == 2


@pytest.mark.parametrize("flag", ["--readonly", "TGCLI_READONLY", "TGCLI_NO_SEND"])
def test_draft_set_commit_blocked_by_mutation_gates(
    config_env, monkeypatch, capsys, flag
):
    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(types.DraftMessageEmpty(), entity),
    )
    make_session_fake(monkeypatch, client)

    assert main(["draft", "set", "@alice", "x", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, "1")
        argv = ["draft", "set", "--commit", preview_id, "--json"]
    else:
        argv = [flag, "draft", "set", "--commit", preview_id, "--json"]
    assert main(argv) == 2


def test_draft_show_allowed_under_readonly(config_env, monkeypatch, capsys):
    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(types.DraftMessageEmpty(), entity),
    )
    make_session_fake(monkeypatch, client)
    assert main(["--readonly", "draft", "show", "@alice", "--json"]) == 0


def test_draft_set_commit_audits(config_env, monkeypatch, capsys):
    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(types.DraftMessageEmpty(), entity),
    )

    async def input_peer(key):
        return types.InputPeerUser(user_id=5, access_hash=7)

    client.get_input_entity = input_peer  # type: ignore[method-assign]
    make_session_fake(monkeypatch, client)

    assert main(["draft", "set", "@alice", "hi", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    assert main(["draft", "set", "--commit", preview_id, "--json"]) == 0
    assert main(["draft", "set", "--commit", preview_id]) == 2

    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["draft-set", "draft-set-result"]


def test_draft_clear_commit_audits(config_env, monkeypatch, capsys):
    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(
            types.DraftMessage(message="wipe", date=None), entity
        ),
    )

    async def input_peer(key):
        return types.InputPeerUser(user_id=5, access_hash=7)

    client.get_input_entity = input_peer  # type: ignore[method-assign]
    make_session_fake(monkeypatch, client)

    assert main(["draft", "clear", "@alice", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    assert main(["draft", "clear", "--commit", preview_id, "--json"]) == 0

    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["draft-clear", "draft-clear-result"]


def test_draft_set_keeps_preview_pending_when_result_audit_fails(
    config_env, monkeypatch, capsys
):
    from tgcli.errors import PolicyError

    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(types.DraftMessageEmpty(), entity),
    )

    async def input_peer(key):
        return types.InputPeerUser(user_id=5, access_hash=7)

    client.get_input_entity = input_peer  # type: ignore[method-assign]
    make_session_fake(monkeypatch, client)

    assert main(["draft", "set", "@alice", "hi", "--preview", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    original_append_audit = safety.append_audit

    def fail_result_audit(action, account, details):
        if action == "draft-set-result":
            raise PolicyError("cannot write audit record: disk full")
        original_append_audit(action, account, details)

    monkeypatch.setattr(safety, "append_audit", fail_result_audit)
    assert main(["draft", "set", "--commit", preview_id]) == 2
    pending = safety.previews_dir() / f"{preview_id}.pending"
    assert pending.exists()
    assert not pending.with_suffix(".used").exists()

    monkeypatch.setattr(safety, "append_audit", original_append_audit)
    assert main(["draft", "set", "--commit", preview_id, "--json"]) == 0


def test_draft_set_topic_only_retry_recognizes_applied_save(
    config_env, monkeypatch, capsys
):
    from tgcli.errors import PolicyError

    entity = _user()
    client = FakeClient(
        entities={"@alice": entity},
        peer_dialogs_result=_peer_dialogs(types.DraftMessageEmpty(), entity),
    )

    async def input_peer(key):
        return types.InputPeerUser(user_id=5, access_hash=7)

    client.get_input_entity = input_peer  # type: ignore[method-assign]
    make_session_fake(monkeypatch, client)

    assert (
        main(["draft", "set", "@alice", "hi", "--topic", "7", "--preview", "--json"])
        == 0
    )
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    original_append_audit = safety.append_audit

    def fail_result_audit(action, account, details):
        if action == "draft-set-result":
            raise PolicyError("cannot write audit record: disk full")
        original_append_audit(action, account, details)

    monkeypatch.setattr(safety, "append_audit", fail_result_audit)
    assert main(["draft", "set", "--commit", preview_id]) == 2

    monkeypatch.setattr(safety, "append_audit", original_append_audit)
    assert main(["draft", "set", "--commit", preview_id, "--json"]) == 0
