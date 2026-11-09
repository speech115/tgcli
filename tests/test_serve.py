"""tg-serve: many `tg` invocations over one held session (ADR-0124)."""

import asyncio
import datetime as dt
import io
import json
import threading

import pytest
from telethon import errors as telethon_errors

from tests.conftest import FakeClient, ns
from tgcli import serve, session
from tgcli.config import Account

CONFIG = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


class ConnectingClient(FakeClient):
    """FakeClient plus the connection surface `session.client` drives."""

    def __init__(self, *args, authorized=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.authorized = authorized
        self.connected = False
        self.connects = 0
        self._request_retries = 5
        self.retries_seen = []

    async def connect(self):
        self.connected = True
        self.connects += 1

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def disconnect(self):
        self.connected = False

    async def _call(self, sender, request, *args, **kwargs):
        return None


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def dialog(name: str, id_: int):
    return ns(
        id=id_,
        name=name,
        is_channel=False,
        is_group=False,
        entity=ns(username=None),
        unread_count=0,
        date=dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC),
    )


def install(monkeypatch, client: ConnectingClient) -> list:
    made = []

    def make(path, account, *, mutation_safe=False):
        made.append(path)
        return client

    monkeypatch.setattr(session, "_make_client", make)
    return made


def run_serve(requests: list, role=None) -> tuple[int, list[dict]]:
    lines = "".join(
        (r if isinstance(r, str) else json.dumps(r)) + "\n" for r in requests
    )
    replies = io.StringIO()
    code = serve.serve(None, role, io.StringIO(lines), replies)
    return code, [json.loads(line) for line in replies.getvalue().splitlines()]


def test_serves_several_commands_over_one_connection(env, monkeypatch):
    client = ConnectingClient(dialogs=[dialog("Alice", 1), dialog("Bob", 2)])
    made = install(monkeypatch, client)

    code, replies = run_serve(
        [{"argv": ["dialogs", "--limit", "5"]}, {"argv": ["dialogs", "--limit", "1"]}]
    )

    assert code == 0
    assert replies[0] == {"ready": True, "account": "main", "role": None}
    first, second = (json.loads(r["stdout"]) for r in replies[1:])
    assert [d["name"] for d in first["dialogs"]] == ["Alice", "Bob"]
    assert [d["name"] for d in second["dialogs"]] == ["Alice"]
    assert all(r["exit_code"] == 0 for r in replies[1:])
    assert len(made) == 1 and client.connects == 1, "one client, connected once"
    assert client.connected is False, "the held session is closed when stdin ends"


def test_session_stays_locked_while_served(env, monkeypatch):
    client = ConnectingClient(dialogs=[dialog("Alice", 1)])
    install(monkeypatch, client)
    path = session.session_path(Account("main", 12345, "h", "main"))
    seen = []
    original = serve.handle

    def handle(line, prefix, parser):
        seen.append(session.lock_held(path))
        return original(line, prefix, parser)

    monkeypatch.setattr(serve, "handle", handle)

    run_serve([{"argv": ["dialogs"]}])

    assert seen == [True]
    assert session.lock_held(path) is False


def test_a_request_cannot_choose_another_account_or_role(env, monkeypatch):
    install(monkeypatch, ConnectingClient())

    _, replies = run_serve(
        [
            {"argv": ["--account", "other", "dialogs"]},
            {"argv": ["dialogs", "--session-role=job"]},
            {"argv": ["search", "--all", "--", "--account"]},
        ]
    )

    for reply in replies[1:3]:
        assert reply["exit_code"] == 1
        assert "fixed by tg-serve" in json.loads(reply["stdout"])["error"]["message"]
    assert "fixed by tg-serve" not in replies[3]["stdout"], "after -- it is text"


def test_bad_request_lines_answer_and_the_server_keeps_going(env, monkeypatch):
    install(monkeypatch, ConnectingClient(dialogs=[dialog("Alice", 1)]))

    _, replies = run_serve(["not json", {"argv": []}, {"argv": ["dialogs"]}])

    assert [r["exit_code"] for r in replies[1:]] == [1, 1, 0]
    assert json.loads(replies[1]["stdout"])["error"]["code"] == "USAGE"


def test_a_command_reads_the_request_stdin_not_the_request_pipe(env, monkeypatch):
    install(monkeypatch, ConnectingClient(dialogs=[dialog("Alice", 1)]))
    script = "import json, sys\nsys.stdout.write(json.dumps({'ok': account}))\n"

    _, replies = run_serve(
        [{"argv": ["run", "-"], "stdin": script}, {"argv": ["dialogs"]}]
    )

    assert json.loads(replies[1]["stdout"]) == {"ok": "main"}
    assert replies[2]["exit_code"] == 0, "the next request line was not eaten"


def test_mutations_on_the_held_client_are_never_retried_blind(env, monkeypatch):
    client = ConnectingClient()
    install(monkeypatch, client)
    account = Account("main", 12345, "h", "main")

    async def scenario():
        async with session.hold(account):
            async with session.client(account, mutation_safe=True) as tg:
                client.retries_seen.append(tg._request_retries)
            async with session.client(account) as tg:
                client.retries_seen.append(tg._request_retries)

    asyncio.run(scenario())

    assert client.retries_seen == [0, 5]


def test_held_client_reconnects_after_a_drop(env, monkeypatch):
    client = ConnectingClient()
    install(monkeypatch, client)
    account = Account("main", 12345, "h", "main")

    async def scenario():
        async with session.hold(account):
            client.connected = False
            async with session.client(account) as tg:
                assert tg.is_connected()

    asyncio.run(scenario())

    assert client.connects == 2


def test_revoked_held_session_reads_as_reauthentication(env, monkeypatch):
    client = ConnectingClient()
    install(monkeypatch, client)
    account = Account("main", 12345, "h", "main")

    async def scenario():
        async with session.hold(account):
            async with session.client(account):
                raise telethon_errors.AuthKeyUnregisteredError(request=None)

    with pytest.raises(session.ConfigError, match="needs reauthentication"):
        asyncio.run(scenario())


def test_failed_start_answers_on_stdout_with_the_contract_error(
    env, monkeypatch, capsys
):
    install(monkeypatch, ConnectingClient(authorized=False))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    code = serve.main([])

    error = json.loads(capsys.readouterr().out)["error"]
    assert code == 3 and error["code"] == "CONFIG"
    assert "not authorized" in error["message"]


def test_what_a_command_installs_on_the_client_ends_with_it(env, monkeypatch):
    client = ConnectingClient(dialogs=[dialog("Alice", 1)])
    install(monkeypatch, client)
    calls = []
    original = serve.handle

    def handle(line, prefix, parser):
        reply = original(line, prefix, parser)
        calls.append(client._call)
        return reply

    monkeypatch.setattr(serve, "handle", handle)

    _, replies = run_serve(
        [{"argv": ["dialogs"]}, {"argv": ["run", "-"], "stdin": "x = 1\n"}]
    )

    assert [r["exit_code"] for r in replies[1:]] == [0, 0]
    assert calls[1] is calls[0], "tg run's read-only gate did not stay on the client"


def test_a_timed_out_step_finishes_its_cleanup_before_the_next_starts(monkeypatch):
    from tgcli import cli, loop
    from tgcli.errors import CommandTimeoutError

    monkeypatch.setattr(cli, "DEADLINE_GRACE", 0.01)
    cleaned = []

    async def slow():
        try:
            await asyncio.sleep(30)
        finally:
            await asyncio.sleep(0.05)
            cleaned.append(True)

    event_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=event_loop.run_forever, daemon=True)
    thread.start()
    try:
        with loop.running_on(event_loop), pytest.raises(CommandTimeoutError):
            cli._run_with_deadline(slow(), 0.05)
        assert cleaned == [True]
    finally:
        event_loop.call_soon_threadsafe(event_loop.stop)
        thread.join(5)
