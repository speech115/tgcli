"""tg-mcp's in-process runner: one held session across calls, released when idle."""

from __future__ import annotations

import datetime as dt
import json
import time

import pytest

from tests.conftest import ns
from tests.fakes.connecting import ConnectingClient, install
from tgcli import session
from tgcli.config import Account
from tgcli.mcp import held
from tgcli.mcp.server import Server
from tgcli.mcp.tools import Allowlist

CONFIG = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


def _session_file():
    return session.session_path(Account("main", 12345, "h", "main"))


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    # The runner forces both for the whole process; teardown restores them.
    monkeypatch.setenv("TGCLI_READONLY", "0")
    monkeypatch.setenv("TGCLI_NO_SEND", "0")
    return tmp_path


@pytest.fixture
def runner_factory():
    made: list[held.HeldRunner] = []

    def make(**kwargs) -> held.HeldRunner:
        runner = held.HeldRunner(**kwargs)
        made.append(runner)
        return runner

    yield make
    for runner in made:
        runner.close()


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


def _wait_until(predicate, seconds: float = 3.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_calls_share_one_connection_and_keep_the_lock(env, monkeypatch, runner_factory):
    client = ConnectingClient(dialogs=[dialog("Alice", 1), dialog("Bob", 2)])
    made = install(monkeypatch, client)
    run = runner_factory(idle=60)

    first = run(["dialogs", "--limit", "5"])
    locked_between = session.lock_held(_session_file())
    second = run(["dialogs", "--limit", "1"])

    assert first[0] == second[0] == 0
    assert [d["name"] for d in json.loads(first[1])["dialogs"]] == ["Alice", "Bob"]
    assert [d["name"] for d in json.loads(second[1])["dialogs"]] == ["Alice"]
    assert len(made) == 1 and client.connects == 1, "one client, connected once"
    assert locked_between is True


def test_the_session_is_released_when_idle_and_reopened_on_the_next_call(
    env, monkeypatch, runner_factory
):
    client = ConnectingClient(dialogs=[dialog("Alice", 1)])
    install(monkeypatch, client)
    run = runner_factory(idle=0.05)

    assert run(["dialogs"])[0] == 0
    assert _wait_until(lambda: not run.holding)
    assert session.lock_held(_session_file()) is False, "a tg process may open it now"
    assert client.connected is False

    assert run(["dialogs"])[0] == 0
    assert client.connects == 2


def test_a_stale_idle_timer_does_not_release_a_newer_burst(
    env, monkeypatch, runner_factory
):
    install(monkeypatch, ConnectingClient(dialogs=[dialog("Alice", 1)]))
    run = runner_factory(idle=60)
    run(["dialogs"])
    stale = run._generation - 1

    run._on_idle(stale)

    assert run.holding


def test_every_call_is_readonly(env, monkeypatch, runner_factory):
    install(monkeypatch, ConnectingClient())
    run = runner_factory()

    code, stdout = run(["run", "--write", "-"])

    assert code == 2
    assert json.loads(stdout)["error"]["code"] == "BLOCKED"
    assert held.os.environ["TGCLI_READONLY"] == "1"
    assert held.os.environ["TGCLI_NO_SEND"] == "1"


def test_a_session_that_cannot_open_answers_with_the_contract_error_and_retries(
    env, monkeypatch, runner_factory
):
    client = ConnectingClient(authorized=False, dialogs=[dialog("Alice", 1)])
    install(monkeypatch, client)
    run = runner_factory()

    code, stdout = run(["dialogs"])

    assert code == 3
    assert json.loads(stdout)["error"]["code"] == "CONFIG"
    assert not run.holding
    client.authorized = True
    assert run(["dialogs"])[0] == 0, "the next call tries again"


def test_mcp_tool_call_end_to_end_over_the_held_session(
    env, monkeypatch, runner_factory
):
    client = ConnectingClient(dialogs=[dialog("Alice", 1), dialog("Bob", 2)])
    install(monkeypatch, client)
    server = Server(Allowlist.build(["1"]), runner_factory())

    result = server.call({"name": "telegram_dialogs", "arguments": {}})

    payload = json.loads(result["content"][-1]["text"])
    assert result["isError"] is False
    assert [d["id"] for d in payload["dialogs"]] == [1]
    assert payload["hidden_by_allowlist"] == 1


def _role_file(role: str):
    return session.session_path(Account("main", 12345, "h", "main"), role)


def test_the_default_role_is_held_once_it_is_logged_in(
    env, monkeypatch, runner_factory
):
    role_file = _role_file(held.DEFAULT_ROLE)
    role_file.parent.mkdir(parents=True, exist_ok=True)
    role_file.touch()
    made = install(monkeypatch, ConnectingClient(dialogs=[dialog("Alice", 1)]))
    run = runner_factory(role=held.DEFAULT_ROLE, fallback_to_main=True)

    assert run(["dialogs"])[0] == 0
    assert made == [role_file]
    assert session.lock_held(role_file) is True
    assert session.lock_held(_session_file()) is False


def test_the_default_role_falls_back_to_main_until_it_is_logged_in(
    env, monkeypatch, runner_factory, capsys
):
    made = install(monkeypatch, ConnectingClient(dialogs=[dialog("Alice", 1)]))
    run = runner_factory(role=held.DEFAULT_ROLE, fallback_to_main=True)

    assert run(["dialogs"])[0] == 0
    assert made == [_session_file()]
    err = capsys.readouterr().err
    assert f"tg accounts login main --role {held.DEFAULT_ROLE}" in err


def test_a_named_role_that_is_not_logged_in_is_an_error_not_a_fallback(
    env, monkeypatch, runner_factory
):
    made = install(monkeypatch, ConnectingClient())
    run = runner_factory(role="agent")

    code, out = run(["dialogs"])

    assert code == 3
    assert "not authorized" in json.loads(out)["error"]["message"]
    assert made == []
