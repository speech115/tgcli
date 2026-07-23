import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
from tests.test_cli_dialogs import make_dialog
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


def test_batch_runs_ops_and_exits_zero(config_env, monkeypatch, capsys):
    client = FakeClient(
        dialogs=[make_dialog()],
        entities={
            "@alice": ns(
                id=111,
                first_name="A",
                last_name=None,
                username="alice",
                bot=False,
                contact=False,
            )
        },
    )
    make_session_fake(monkeypatch, client)
    stdin = (
        json.dumps({"op": "dialogs", "limit": 1})
        + "\n"
        + json.dumps({"op": "resolve", "ref": "@alice"})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: stdin})())

    assert main(["batch"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 2
    assert lines[0]["ok"] is True and lines[0]["op"] == "dialogs"
    assert lines[1]["ok"] is True and lines[1]["op"] == "resolve"


def test_batch_nonzero_exit_on_partial_failure(config_env, monkeypatch, capsys):
    client = FakeClient(dialogs=[make_dialog()], entities={})
    make_session_fake(monkeypatch, client)
    stdin = (
        json.dumps({"op": "resolve", "ref": "@missing"})
        + "\n"
        + json.dumps({"op": "dialogs", "limit": 1})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: stdin})())

    assert main(["batch"]) == 4
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[0]["ok"] is False
    assert lines[1]["ok"] is True


def test_batch_fail_fast_stops_early(config_env, monkeypatch, capsys):
    client = FakeClient(entities={})
    make_session_fake(monkeypatch, client)
    stdin = (
        json.dumps({"op": "resolve", "ref": "@missing"})
        + "\n"
        + json.dumps({"op": "dialogs", "limit": 1})
        + "\n"
    )
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: stdin})())

    assert main(["batch", "--fail-fast"]) == 4
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 1
    assert lines[0]["ok"] is False


def test_batch_rejects_doctor_and_mutations(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient())
    for op in ("doctor", "send", "export", "media.download"):
        monkeypatch.setattr(
            "sys.stdin",
            type("S", (), {"read": lambda self, o=op: json.dumps({"op": o}) + "\n"})(),
        )
        assert main(["batch"]) == 2


def test_batch_rejects_over_100_ops(config_env, monkeypatch):
    make_session_fake(monkeypatch, FakeClient())
    lines = "\n".join(json.dumps({"op": "dialogs", "limit": 1}) for _ in range(101))
    monkeypatch.setattr("sys.stdin", type("S", (), {"read": lambda self: lines})())
    assert main(["batch"]) == 2
