"""`tg run`: a script with the authenticated client, read-only unless --write."""

import io
import json
from datetime import UTC, datetime
from pathlib import Path

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli.cli import main
from tgcli.state_paths import state_dir

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 1
api_hash = "hash"
session = "main"
"""


class ScriptClient:
    """Routes ``client(request)`` through ``_call`` as Telethon does."""

    def __init__(self, floods=()):
        self.sent = []
        self.floods = list(floods)

    async def _call(self, sender, request, ordered=False):
        self.sent.append(request)
        if self.floods:
            raise telethon_errors.FloodWaitError(
                request=request, capture=self.floods.pop(0)
            )
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


def setup(tmp_path, monkeypatch, script: str, floods=()):
    config = tmp_path / "config.toml"
    config.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    client = ScriptClient(floods)
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


def test_writes_past_the_first_five_are_spaced(tmp_path, monkeypatch, pace_clock):
    client, path = setup(tmp_path, monkeypatch, SEND * 7)

    assert main(["run", "--write", path]) == 0

    assert len(client.sent) == 7
    assert pace_clock.sleeps == [3.0, 3.0]


def test_a_script_write_hit_by_two_floods_exits_5(
    tmp_path, monkeypatch, capsys, pace_clock
):
    client, path = setup(tmp_path, monkeypatch, SEND, floods=[5, 6])

    assert main(["--json", "run", "--write", path]) == 5

    assert len(client.sent) == 2
    assert json.loads(capsys.readouterr().err)["error"]["retry_after"] == 6


def test_account_writes_are_denied_even_with_the_flag(tmp_path, monkeypatch):
    client, path = setup(
        tmp_path,
        monkeypatch,
        "await client(functions.account.UpdateProfileRequest(about='x'))\n",
    )

    assert main(["run", "--write", path]) == 2
    assert client.sent == []


def test_readonly_refuses_any_script(tmp_path, monkeypatch, capsys):
    """A script holds the authorized client: it can undo the write gate or read
    the session key, so no script is read-only and readonly refuses them all."""
    client, path = setup(tmp_path, monkeypatch, "await client.get_messages(1)\n")

    assert main(["--readonly", "run", path]) == 2
    monkeypatch.setenv("TGCLI_READONLY", "1")
    assert main(["run", path]) == 2
    assert client.sent == []
    assert "tg run" in capsys.readouterr().err


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


def test_cross_dc_download_plumbing_is_allowed(tmp_path, monkeypatch):
    client, path = setup(
        tmp_path,
        monkeypatch,
        "await client(functions.auth.ExportAuthorizationRequest(dc_id=4))\n",
    )

    assert main(["run", path]) == 0
    assert [type(r) for r in client.sent] == [functions.auth.ExportAuthorizationRequest]


def test_a_script_write_on_a_session_role_records_the_role(tmp_path, monkeypatch):
    _, path = setup(tmp_path, monkeypatch, SEND)

    assert main(["--session-role", "job", "run", "--write", path]) == 0

    [record] = audit_lines()
    assert (record["method"], record["role"]) == ("messages.sendMessage", "job")


def test_every_method_tg_run_lets_through_as_a_read_was_reviewed():
    """The read classification is a heuristic over names. A Telethon bump
    that adds a getter with a side effect must fail here until a person
    reviews it: add it to the list, or to SIDE_EFFECT_GETTERS."""

    from tgcli.commands import run

    reviewed = Path(__file__).with_name("run_read_methods.txt").read_text()
    assert run.read_methods() == reviewed.split()


def test_root_session_requests_are_denied_even_with_the_flag(tmp_path, monkeypatch):
    """DestroyAuthKey/DestroySession end the session like a logout."""
    for script in (
        "await client(functions.DestroyAuthKeyRequest())\n",
        "await client(functions.DestroySessionRequest(session_id=1))\n",
        "await client(functions.InvokeWithLayerRequest(layer=1, "
        "query=functions.DestroyAuthKeyRequest()))\n",
    ):
        client, path = setup(tmp_path, monkeypatch, script)

        assert main(["run", "--write", path]) == 2
        assert client.sent == []
        assert audit_lines() == []


def test_ping_stays_a_read(tmp_path, monkeypatch):
    client, path = setup(
        tmp_path, monkeypatch, "await client(functions.PingRequest(ping_id=1))\n"
    )

    assert main(["run", path]) == 0
    assert [type(r) for r in client.sent] == [functions.PingRequest]


def test_a_timeout_error_after_the_deadline_is_still_the_deadline(
    tmp_path, monkeypatch, capsys
):
    # Past --timeout, inside the grace window, a socket-style TimeoutError is
    # the deadline expiring (CONTRACT §4 TIMEOUT), not a RUNTIME failure.
    _, path = setup(
        tmp_path,
        monkeypatch,
        "import asyncio\nawait asyncio.sleep(0.4)\nraise TimeoutError('late')\n",
    )

    main(["--json", "--timeout", "0.1", "run", path])

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["error"]["code"] == "TIMEOUT", envelope
