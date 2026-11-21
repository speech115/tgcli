import datetime as dt
import json

import pytest
from telethon.errors import ChatForwardsRestrictedError, RandomIdDuplicateError
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

WHEN = dt.datetime(2026, 11, 18, 9, 0, tzinfo=dt.UTC)
CHAN_PEER = types.InputPeerChannel(channel_id=5, access_hash=50)
ALICE_PEER = types.InputPeerUser(user_id=7, access_hash=70)


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


class ForwardClient(FakeClient):
    """Telegram confirms a forward by echoing its random_id with the new id."""

    peers = {"@chan": CHAN_PEER, "@alice": ALICE_PEER}

    def __init__(self, *, outcomes=(), **kwargs):
        super().__init__(**kwargs)
        self.forwards = []
        self._outcomes = list(outcomes)

    async def get_input_entity(self, key):
        if key not in self.peers:
            raise ValueError(f"Cannot find any entity corresponding to {key!r}")
        return self.peers[key]

    async def __call__(self, request):
        if not isinstance(request, functions.messages.ForwardMessagesRequest):
            return await super().__call__(request)
        self.forwards.append(request)
        if self._outcomes:
            raise self._outcomes.pop(0)
        return ns(
            updates=[types.UpdateMessageID(id=77, random_id=request.random_id[0])]
        )


def make_client(*, protected=False, outcomes=()):
    report = ns(
        id=42,
        date=WHEN,
        sender_id=1,
        sender=None,
        text="Q3 report",
        media=ns(),
        document=ns(),
        file=ns(
            name="report.pdf",
            mime_type="application/pdf",
            size=1234,
            duration=None,
            width=None,
            height=None,
        ),
        reply_to_msg_id=None,
    )
    return ForwardClient(
        messages=[report],
        entities={
            "@chan": ns(id=5, title="Chan", noforwards=protected),
            "@alice": ns(id=7, first_name="Alice"),
        },
        outcomes=outcomes,
    )


def forward_preview(*, copy=False, silent=False, random_id=123, chat="@alice"):
    return safety.create_preview(
        {
            "kind": "forward",
            "from_chat": "@chan",
            "message_id": 42,
            "chat": chat,
            "copy": copy,
            "silent": silent,
            "random_id": random_id,
            "from": {"id": 5, "name": "Chan"},
            "to": {"id": 7, "name": "Alice"},
            "text": "Q3 report",
            "media_kind": "document",
            "media_info": None,
        }
    )


def test_forward_preview_shows_what_moves_where_without_forwarding(
    config_env, monkeypatch, capsys
):
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["forward", "@chan", "42", "@alice", "--preview", "--json"]) == 0

    preview = json.loads(capsys.readouterr().out)
    assert preview["preview_id"] and preview["expires_at"]
    assert {
        key: value
        for key, value in preview.items()
        if key not in ("preview_id", "expires_at")
    } == {
        "from": {"id": 5, "name": "Chan"},
        "to": {"id": 7, "name": "Alice"},
        "message_id": 42,
        "text": "Q3 report",
        "media_kind": "document",
        "media_info": {
            "name": "report.pdf",
            "mime": "application/pdf",
            "size": 1234,
            "duration": None,
            "width": None,
            "height": None,
        },
        "copy": False,
        "silent": False,
    }
    assert client.forwards == []
    stored = safety.begin_commit(preview["preview_id"], expected_kind="forward")
    assert isinstance(stored["random_id"], int) and stored["random_id"] > 0
    assert (stored["from_chat"], stored["message_id"], stored["chat"]) == (
        "@chan",
        42,
        "@alice",
    )


@pytest.mark.parametrize("flag", ["--readonly", "TGCLI_READONLY", "TGCLI_NO_SEND"])
def test_forward_preview_is_allowed_by_mutation_gates(config_env, monkeypatch, flag):
    client = make_client()
    make_session_fake(monkeypatch, client)
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, "1")
        argv = ["forward", "@chan", "42", "@alice", "--preview"]
    else:
        argv = [flag, "forward", "@chan", "42", "@alice", "--preview"]

    assert main(argv) == 0
    assert client.forwards == []


def test_forward_commit_sends_one_forward_request(config_env, monkeypatch, capsys):
    preview = forward_preview(random_id=123)
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "preview_id": preview["preview_id"],
        "message_id": 77,
        "already_sent": False,
    }
    [request] = client.forwards
    assert request == functions.messages.ForwardMessagesRequest(
        from_peer=CHAN_PEER,
        id=[42],
        to_peer=ALICE_PEER,
        random_id=[123],
        silent=None,
        drop_author=None,
    )
    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["forward", "forward-result"]
    # The preview is spent: a second commit is refused, not forwarded again.
    assert main(["forward", "--commit", preview["preview_id"]]) == 2
    assert len(client.forwards) == 1


def test_forward_copy_and_silent_reach_the_request(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)
    argv = ["forward", "@chan", "42", "@alice", "--copy", "--silent", "--preview"]

    assert main([*argv, "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (preview["copy"], preview["silent"]) == (True, True)

    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 0
    [request] = client.forwards
    assert (request.drop_author, request.silent) == (True, True)


def test_forward_commit_retry_reports_already_sent(config_env, monkeypatch, capsys):
    # The first attempt reached Telegram but its answer was lost; the retry
    # reuses the preview's random_id, so Telegram refuses a second copy.
    preview = forward_preview(random_id=123)
    client = make_client(
        outcomes=[ConnectionError("lost"), RandomIdDuplicateError(request=None)]
    )
    make_session_fake(monkeypatch, client)

    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 1
    capsys.readouterr()
    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "preview_id": preview["preview_id"],
        "message_id": None,
        "already_sent": True,
    }
    assert [request.random_id for request in client.forwards] == [[123], [123]]


@pytest.mark.parametrize("flag", ["--readonly", "TGCLI_READONLY", "TGCLI_NO_SEND"])
def test_forward_commit_is_blocked_by_mutation_gates(
    config_env, monkeypatch, capsys, flag
):
    preview = forward_preview()
    client = make_client()
    make_session_fake(monkeypatch, client)
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, "1")
        argv = ["forward", "--commit", preview["preview_id"], "--json"]
    else:
        argv = [flag, "forward", "--commit", preview["preview_id"], "--json"]

    assert main(argv) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "BLOCKED"
    assert client.forwards == []


@pytest.mark.parametrize(
    "argv",
    [
        ["forward", "@chan", "99", "@alice", "--preview"],
        ["forward", "@gone", "42", "@alice", "--preview"],
        ["forward", "@chan", "42", "@gone", "--preview"],
    ],
    ids=["message", "source", "destination"],
)
def test_forward_preview_reports_missing_targets_as_not_found(
    config_env, monkeypatch, capsys, argv
):
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main([*argv, "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NOT_FOUND"
    assert list(safety.previews_dir().glob("*")) == []


def test_forward_commit_reports_unknown_dialog_as_not_found(
    config_env, monkeypatch, capsys
):
    preview = forward_preview(chat="@gone")
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NOT_FOUND"
    assert client.forwards == []


def test_forward_preview_refuses_a_protected_source(config_env, monkeypatch, capsys):
    client = make_client(protected=True)
    make_session_fake(monkeypatch, client)

    argv = ["forward", "@chan", "42", "@alice", "--preview", "--json"]
    assert main(argv) == 2

    assert json.loads(capsys.readouterr().out)["error"]["code"] == "BLOCKED"
    assert list(safety.previews_dir().glob("*")) == []


def test_forward_commit_refused_by_telegram_is_one_error_envelope(
    config_env, monkeypatch, capsys
):
    # A chat protected after its preview: Telegram's refusal is reported, not
    # a traceback.
    preview = forward_preview()
    client = make_client(outcomes=[ChatForwardsRestrictedError(request=None)])
    make_session_fake(monkeypatch, client)

    assert main(["forward", "--commit", preview["preview_id"], "--json"]) == 1

    out = capsys.readouterr()
    error = json.loads(out.out)["error"]
    assert error["code"] == "RUNTIME"
    assert "protected chat" in error["message"]
    assert "Traceback" not in out.err


@pytest.mark.parametrize(
    "argv",
    [
        ["forward", "--preview"],
        ["forward", "@chan", "--preview"],
        ["forward", "@chan", "42", "--preview"],
        ["forward", "@chan", "42", "@alice"],
    ],
)
def test_forward_preview_requires_source_message_and_destination(argv, capsys):
    assert main(argv) == 1
    assert "forward requires" in capsys.readouterr().err
