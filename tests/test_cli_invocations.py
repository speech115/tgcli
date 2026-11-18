import json
import signal
import subprocess
import sys

import pytest

from tests.conftest import FakeClient, make_session_fake
from tgcli.cli import main
from tgcli.state_paths import state_dir

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


@pytest.mark.parametrize(
    ("argv", "command"),
    [(["accounts", "list"], "accounts list"), (["clone", "status"], "clone status")],
)
def test_subcommand_is_journaled_by_its_full_path(tmp_path, monkeypatch, argv, command):
    """`archive sync` and `archive status` must not share one `archive` row."""
    config_env(tmp_path, monkeypatch)

    assert main(argv) == 0

    [entry] = read_journal()
    assert entry["command"] == command


def test_config_error_journals_where_it_was_raised(tmp_path, monkeypatch):
    """Exit 3 covers a dead session, a busy lock, and a missing account; the
    journal names the raising function so the causes can be told apart."""
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE.replace('default_account = "main"\n', ""))
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    monkeypatch.delenv("TGCLI_ACCOUNT", raising=False)

    assert main(["dialogs"]) == 3

    [entry] = read_journal()
    assert (entry["error"], entry["error_site"]) == (
        "CONFIG",
        "config:resolve_account",
    )
    assert "error_type" not in entry


TERMINATION_PROGRAM = """
import os
import select
import signal
import sys

from tgcli import cli


class BlockingStdin:
    "Announce that the invocation is running, then wait to be signalled."

    def read(self):
        # Not time.sleep: a signal landing after the interpreter's last check
        # but before the sleep syscall is not seen until the sleep ends, so the
        # child outlived the parent's wait. The wakeup fd records every signal,
        # so select returns at once even for one that arrived before it.
        wake_read, wake_write = os.pipe()
        os.set_blocking(wake_write, False)
        signal.set_wakeup_fd(wake_write)
        sys.stderr.write("READY\\n")
        sys.stderr.flush()
        select.select([wake_read], [], [], 30)
        return ""


sys.stdin = BlockingStdin()
sys.argv = ["tg", "run", "-"]
cli.entrypoint()
"""


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_termination_signal_journals_an_honest_row_and_dies_by_signal(
    tmp_path, monkeypatch, signum
):
    """CONTRACT §6: a killed run still owes the journal one honest object."""
    config_env(tmp_path, monkeypatch)
    with subprocess.Popen(
        [sys.executable, "-c", TERMINATION_PROGRAM],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
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
    assert entry["command"] == "run"
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
