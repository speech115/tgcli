"""Failure paths of the invocation lifecycle (CONTRACT §2/§4, ADR-0053).

A `--timeout` expiry, an untranslated network failure, and a stdout pipe the
reader closed are all contract outcomes: one envelope on stdout, the identical
stderr mirror, a documented exit code, and a truthful journal row — never a
raw traceback.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import pytest
from telethon.errors import rpcerrorlist

from tgcli import cli, dispatch, output
from tgcli.cli import main
from tgcli.errors import CommandTimeoutError
from tgcli.session import state_dir

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


def _hangs(monkeypatch):
    async def run_network(args, account):
        await asyncio.sleep(10)

    monkeypatch.setattr(dispatch, "run_network", run_network)


def _fails_with(monkeypatch, exc):
    async def run_network(args, account):
        raise exc

    monkeypatch.setattr(dispatch, "run_network", run_network)


def test_timeout_expiry_is_a_timeout_envelope_on_both_streams(env, monkeypatch, capsys):
    _hangs(monkeypatch)

    assert main(["dialogs", "--json", "--timeout", "0.05"]) == 1

    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["error"]["code"] == CommandTimeoutError.code == "TIMEOUT"
    assert "0.05" in envelope["error"]["message"]
    assert json.loads(captured.err.splitlines()[-1]) == envelope
    assert "Traceback" not in captured.err
    [entry] = read_journal()
    assert entry["exit_code"] == 1
    assert entry["error"] == "TIMEOUT"


def test_timeout_expiry_reports_the_deadline_in_plain_mode(env, monkeypatch, capsys):
    _hangs(monkeypatch)

    assert main(["dialogs", "--plain", "--timeout", "0.05"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "0.05" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("connection to Telegram failed"),
        rpcerrorlist.MsgIdInvalidError(request=None),
    ],
    ids=["connection", "rpc"],
)
def test_untranslated_failure_is_one_runtime_envelope(env, monkeypatch, capsys, exc):
    _fails_with(monkeypatch, exc)

    assert main(["dialogs", "--json"]) == 1

    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope == {"error": {"code": "RUNTIME", "message": str(exc)}}
    assert json.loads(captured.err.splitlines()[-1]) == envelope
    assert "Traceback" not in captured.err
    [entry] = read_journal()
    assert entry["exit_code"] == 1
    assert entry["error"] == "RUNTIME"


def test_untranslated_failure_reports_through_stderr_in_plain_mode(
    env, monkeypatch, capsys
):
    _fails_with(monkeypatch, ConnectionError("connection to Telegram failed"))

    assert main(["dialogs", "--plain"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: connection to Telegram failed\n"


def test_untranslated_failure_shows_the_traceback_only_under_verbose(
    env, monkeypatch, capsys
):
    _fails_with(monkeypatch, ConnectionError("connection to Telegram failed"))

    assert main(["dialogs", "--json", "--verbose"]) == 1

    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "RUNTIME"
    assert "Traceback (most recent call last)" in captured.err
    assert "ConnectionError: connection to Telegram failed" in captured.err


def test_emit_failure_is_journaled_as_an_error(env, monkeypatch):
    def boom(data):
        raise RuntimeError("stdout exploded")

    monkeypatch.setattr(output, "emit_json", boom)

    assert main(["accounts", "list", "--json"]) == 1

    [entry] = read_journal()
    assert entry["exit_code"] == 1
    assert entry["error"] == "RUNTIME"


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
    assert entry["command"] == "accounts"
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
