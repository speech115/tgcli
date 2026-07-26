"""Grammar and pre-session checks owned by parser.py and preflight.py.

`--confirm` and `--write` are only gates if they have to be typed in full, and
a `--params` typo is a purely local mistake that must never cost a session
open. Both are decided before anything reaches the network.
"""

import json
from contextlib import asynccontextmanager

import pytest

from tgcli import session
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


@pytest.fixture
def opened_sessions(monkeypatch):
    """Record every session open and refuse it, so network work is visible."""
    opened = []

    @asynccontextmanager
    async def refuse(account, *, mutation_safe=False):
        opened.append(account)
        raise AssertionError("a session was opened before the local checks passed")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(session, "client", refuse)
    return opened


def test_store_cleanup_confirm_must_be_spelled_in_full(config_env, capsys):
    assert main(["store", "cleanup", "--c", "--json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments: --c" in captured.err


def test_store_cleanup_confirm_still_deletes_when_spelled_in_full(
    config_env, capsys, tmp_path
):
    from tgcli.session import state_dir

    spent = state_dir() / "previews" / "p_spent.used"
    spent.parent.mkdir(parents=True)
    spent.write_text("{}")

    assert main(["store", "cleanup", "--confirm", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["confirmed"] is True
    assert payload["removed"] == ["p_spent.used"]
    assert not spent.exists()


def test_api_write_gate_must_be_spelled_in_full(config_env, opened_sessions, capsys):
    assert (
        main(
            ["api", "auth.logOut", "--w", "--confirm", "auth.logOut", "--params", "{}"]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments: --w" in captured.err
    assert opened_sessions == []


def test_api_confirm_gate_must_be_spelled_in_full(config_env, opened_sessions, capsys):
    assert (
        main(
            [
                "api",
                "messages.deleteHistory",
                "--write",
                "--conf",
                "messages.deleteHistory",
                "--params",
                "{}",
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert "unrecognized arguments: --conf" in captured.err
    assert opened_sessions == []


def test_api_gates_still_apply_when_spelled_in_full(config_env, opened_sessions):
    assert (
        main(
            [
                "api",
                "auth.logOut",
                "--write",
                "--confirm",
                "auth.logOut",
                "--params",
                "{}",
            ]
        )
        == 2
    )
    assert opened_sessions == []


def test_malformed_api_params_are_rejected_before_a_session_opens(
    config_env, opened_sessions, capsys
):
    assert main(["api", "users.getFullUser", "--params", "{oops", "--json"]) == 3

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "CONFIG"
    assert opened_sessions == []


def test_non_object_api_params_are_rejected_before_a_session_opens(
    config_env, opened_sessions, capsys
):
    assert main(["api", "users.getFullUser", "--params", "[]", "--json"]) == 3

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "CONFIG"
    assert opened_sessions == []
