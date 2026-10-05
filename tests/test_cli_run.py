"""`tg run`: a script with the authenticated client, read-only unless --write."""

import io
import json
from datetime import UTC, datetime

from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli.cli import main
from tgcli.session import state_dir

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 1
api_hash = "hash"
session = "main"
"""


class ScriptClient:
    """Routes ``client(request)`` through ``_call`` as Telethon does."""

    def __init__(self):
        self.sent = []

    async def _call(self, sender, request, ordered=False):
        self.sent.append(request)
        return True

    async def __call__(self, request):
        return await self._call(None, request)

    async def get_messages(self, entity, limit=None):
        return [
            types.Message(
                id=7,
                peer_id=types.PeerChannel(1),
                date=datetime(2026, 10, 5, tzinfo=UTC),
                message="hello",
            )
        ]


def setup(tmp_path, monkeypatch, script: str):
    config = tmp_path / "config.toml"
    config.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    client = ScriptClient()
    make_session_fake(monkeypatch, client)
    path = tmp_path / "script.py"
    path.write_text(script)
    return client, str(path)


def audit_lines():
    path = state_dir() / "audit.jsonl"
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if (path.exists())
        else []
    )


SEND = (
    "await client(functions.messages.SendMessageRequest("
    "peer=types.InputPeerSelf(), message='hi', random_id=1))\n"
)


def test_a_script_reads_with_the_client_and_owns_stdout(tmp_path, monkeypatch, capsys):
    client, path = setup(
        tmp_path,
        monkeypatch,
        "import json, sys\n"
        "[m] = await client.get_messages('@chan', limit=1)\n"
        "print(json.dumps({'account': account, 'argv': sys.argv[1:], "
        "'text': msg(m)['text']}))\n"
        "await client(functions.messages.GetHistoryRequest("
        "peer=types.InputPeerSelf(), offset_id=0, offset_date=None, add_offset=0, "
        "limit=1, max_id=0, min_id=0, hash=0))\n",
    )

    assert main(["run", path, "--flag", "x"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "account": "main",
        "argv": ["--flag", "x"],
        "text": "hello",
    }
    assert [type(r) for r in client.sent] == [functions.messages.GetHistoryRequest]


def test_a_write_without_flag_is_blocked_before_it_leaves(
    tmp_path, monkeypatch, capsys
):
    client, path = setup(tmp_path, monkeypatch, SEND)

    assert main(["--json", "run", path]) == 2

    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "BLOCKED"
    assert "messages.sendMessage writes; re-run with --write" in error["message"]
    assert client.sent == []
    assert audit_lines() == []


def test_a_wrapped_write_is_still_seen(tmp_path, monkeypatch):
    client, path = setup(
        tmp_path,
        monkeypatch,
        "await client(functions.InvokeWithLayerRequest(layer=1, query="
        "functions.messages.SendMessageRequest(peer=types.InputPeerSelf(), "
        "message='hi', random_id=1)))\n",
    )

    assert main(["run", path]) == 2
    assert client.sent == []


def test_write_flag_audits_then_sends(tmp_path, monkeypatch):
    client, path = setup(tmp_path, monkeypatch, SEND)

    assert main(["run", "--write", path]) == 0

    assert [type(r) for r in client.sent] == [functions.messages.SendMessageRequest]
    [record] = audit_lines()
    assert (record["action"], record["account"], record["method"]) == (
        "run-write",
        "main",
        "messages.sendMessage",
    )


def test_account_writes_are_denied_even_with_the_flag(tmp_path, monkeypatch):
    client, path = setup(
        tmp_path,
        monkeypatch,
        "await client(functions.account.UpdateProfileRequest(about='x'))\n",
    )

    assert main(["run", "--write", path]) == 2
    assert client.sent == []


def test_readonly_and_no_send_refuse_the_write_flag(tmp_path, monkeypatch):
    _, path = setup(tmp_path, monkeypatch, SEND)

    assert main(["--readonly", "run", "--write", path]) == 2
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    assert main(["run", "--write", path]) == 2


def test_a_script_bug_prints_its_traceback_and_exits_1(tmp_path, monkeypatch, capsys):
    _, path = setup(tmp_path, monkeypatch, "x = 1\nraise ValueError('boom')\n")

    assert main(["run", path]) == 1

    err = capsys.readouterr().err
    assert "Traceback" in err
    assert f'File "{path}", line 2' in err
    assert "boom" in err


def test_a_script_from_stdin_and_a_nonzero_exit(tmp_path, monkeypatch, capsys):
    setup(tmp_path, monkeypatch, "")
    monkeypatch.setattr("sys.stdin", io.StringIO("print(account)\n"))
    assert main(["run", "-"]) == 0
    assert capsys.readouterr().out == "main\n"

    monkeypatch.setattr("sys.stdin", io.StringIO("import sys\nsys.exit(4)\n"))
    assert main(["run", "-"]) == 1


def test_an_empty_or_broken_script_fails_before_any_session(tmp_path, monkeypatch):
    client, path = setup(tmp_path, monkeypatch, "  \n")
    assert main(["run", path]) == 3
    (tmp_path / "broken.py").write_text("def (\n")
    assert main(["run", str(tmp_path / "broken.py")]) == 3
    assert client.sent == []


def test_reads_that_only_look_like_reads_are_refused(tmp_path, monkeypatch):
    """ADR-0010: these getters have side effects or touch credentials."""
    client, path = setup(
        tmp_path,
        monkeypatch,
        "import sys\n"
        "request = {\n"
        "  'password': functions.auth.CheckPasswordRequest(\n"
        "      password=types.InputCheckPasswordEmpty()),\n"
        "  'inline': functions.messages.GetInlineBotResultsRequest(\n"
        "      bot=types.InputUserSelf(), peer=types.InputPeerSelf(),\n"
        "      query='q', offset=''),\n"
        "  'sponsored': functions.messages.GetSponsoredMessagesRequest(\n"
        "      peer=types.InputPeerSelf()),\n"
        "}[sys.argv[1]]\n"
        "await client(request)\n",
    )

    assert main(["run", path, "password"]) == 2
    assert main(["run", "--write", path, "password"]) == 2
    assert main(["run", path, "inline"]) == 2
    assert main(["run", path, "sponsored"]) == 2
    assert client.sent == []


def test_cross_dc_download_plumbing_is_allowed(tmp_path, monkeypatch):
    client, path = setup(
        tmp_path,
        monkeypatch,
        "await client(functions.auth.ExportAuthorizationRequest(dc_id=4))\n",
    )

    assert main(["run", path]) == 0
    assert [type(r) for r in client.sent] == [functions.auth.ExportAuthorizationRequest]
