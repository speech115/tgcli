import json

from tests.conftest import FakeClient, make_session_fake
from tgcli.cli import main
from tgcli.session import state_dir

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def read_journal():
    return [
        json.loads(line)
        for line in (state_dir() / "invocations.jsonl").read_text().splitlines()
    ]


def test_successful_command_writes_invocation_metadata(tmp_path, monkeypatch):
    config_env(tmp_path, monkeypatch)
    make_session_fake(monkeypatch, FakeClient(dialogs=[]))

    assert main(["dialogs"]) == 0

    [entry] = read_journal()
    assert entry["command"] == "dialogs"
    assert entry["account"] == "main"
    assert entry["exit_code"] == 0
    assert entry["duration_ms"] >= 0
    assert "error" not in entry


def test_policy_block_writes_the_structured_error_code():
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

    [entry] = read_journal()
    assert entry["command"] == "api"
    assert entry["exit_code"] == 2
    assert entry["error"] == "BLOCKED"
