import json
import signal
import subprocess
import sys

import pytest

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


TERMINATION_PROGRAM = """
import sys
import time

from tgcli import cli


class BlockingStdin:
    "Announce that the invocation is running, then wait to be signalled."

    def read(self):
        sys.stderr.write("READY\\n")
        sys.stderr.flush()
        time.sleep(30)
        return ""


sys.stdin = BlockingStdin()
sys.argv = ["tg", "batch"]
cli.entrypoint()
"""


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_termination_signal_journals_an_honest_row_and_dies_by_signal(
    tmp_path, monkeypatch, signum
):
    """CONTRACT §9: a killed run still owes the journal one honest object."""
    config_env(tmp_path, monkeypatch)
    process = subprocess.Popen(
        [sys.executable, "-c", TERMINATION_PROGRAM],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stderr is not None
        assert process.stderr.readline().strip() == "READY"
        process.send_signal(signum)
        process.wait(timeout=30)
    finally:
        process.kill()

    assert process.returncode == -signum
    journal = state_dir() / "invocations.jsonl"
    assert journal.exists(), "the killed run appended no journal object"
    entry = read_journal()[-1]
    assert entry["command"] == "batch"
    assert entry["exit_code"] == 128 + signum
    assert entry["error"] == "TERMINATED"


def test_keyboard_interrupt_journals_an_honest_row_and_propagates(
    tmp_path, monkeypatch
):
    config_env(tmp_path, monkeypatch)
    client = FakeClient(dialogs=[])

    async def interrupted():
        raise KeyboardInterrupt
        yield  # pragma: no cover — makes this an async generator

    client.iter_dialogs = interrupted
    make_session_fake(monkeypatch, client)

    with pytest.raises(KeyboardInterrupt):
        main(["dialogs"])

    [entry] = read_journal()
    assert entry["command"] == "dialogs"
    assert entry["exit_code"] == 130
    assert entry["error"] == "INTERRUPTED"
