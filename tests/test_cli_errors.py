"""Failure paths of the invocation lifecycle (CONTRACT §2/§4).

A `--timeout` expiry, an untranslated network failure, and a stdout pipe the
reader closed are all contract outcomes: one envelope on stdout, the identical
stderr mirror, a documented exit code, and a truthful journal row — never a
raw traceback.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from tgcli import cli, dispatch
from tgcli.cli import main
from tgcli.state_paths import state_dir

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    return path


def read_journal(root=None):
    path = (root or state_dir()) / "invocations.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _fails_with(monkeypatch, exc):
    async def run_network(args, account):
        raise exc

    monkeypatch.setattr(dispatch, "run_network", run_network)


def test_untranslated_failure_masks_phone_in_verbose_traceback(
    env, monkeypatch, capsys
):
    _fails_with(monkeypatch, ConnectionError("failed for +79991234567"))

    assert main(["dialogs", "--json", "--verbose"]) == 1

    captured = capsys.readouterr()
    assert "Traceback (most recent call last)" in captured.err
    assert "ConnectionError: failed for +7…67" in captured.err
    assert "79991234567" not in captured.err


def test_entrypoint_exits_quietly_when_stdout_hangs_up(monkeypatch, capsys):
    def boom(argv=None):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli, "main", boom)

    with pytest.raises(SystemExit) as excinfo:
        cli.entrypoint()

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_closed_stdout_pipe_exits_quietly_with_a_truthful_journal(tmp_path):
    """`tg --json ... | head -0`: the reader hangs up before we write."""
    config = tmp_path / "config.toml"
    config.write_text(SAMPLE)
    state = tmp_path / "state"
    child_env = {
        **os.environ,
        "TGCLI_CONFIG": str(config),
        "TGCLI_STATE_DIR": str(state),
    }
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from tgcli.cli import entrypoint; entrypoint()",
            "--json",
            "accounts",
            "list",
        ],
        stdout=write_fd,
        stderr=subprocess.PIPE,
        env=child_env,
    )
    os.close(write_fd)
    os.close(read_fd)
    stderr = process.communicate()[1]

    assert process.returncode == 0
    assert stderr == b""
    [entry] = read_journal(state)
    assert entry["command"] == "accounts list"
    assert entry["exit_code"] == 0
    assert entry["error"] == "BROKEN_PIPE"


def test_closed_pipe_during_an_error_envelope_keeps_the_real_failure(tmp_path):
    """A reader that hangs up must not overwrite the failure being reported."""
    config = tmp_path / "config.toml"
    config.write_text(SAMPLE)
    state = tmp_path / "state"
    child_env = {
        **os.environ,
        "TGCLI_CONFIG": str(config),
        "TGCLI_STATE_DIR": str(state),
    }
    read_fd, write_fd = os.pipe()
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from tgcli.cli import entrypoint; entrypoint()",
            "--json",
            "accounts",
            "show",
            "nosuchalias",
        ],
        stdout=write_fd,
        stderr=subprocess.PIPE,
        env=child_env,
    )
    os.close(write_fd)
    os.close(read_fd)
    stderr = process.communicate()[1]

    # The command really failed (unknown alias → exit 4); the closed pipe only
    # costs the envelope, never the truth about the run.
    assert process.returncode == 4
    assert b"Traceback" not in stderr
    [entry] = read_journal(state)
    assert entry["exit_code"] == 4
    assert entry["error"] == "NOT_FOUND"
