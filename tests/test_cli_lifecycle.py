"""Invocation-lifecycle tests: the deadline and the usage envelope.

These live apart from the per-command suites because what they pin belongs to
cli.py — how long one invocation may run and what a `--json` caller reads back
when the invocation never reached a command at all.
"""

import json
import time

import pytest

from tests.conftest import FakeClient, make_session_fake
from tgcli import __version__, cli
from tgcli.cli import main
from tgcli.session import state_dir

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


def read_journal():
    return [
        json.loads(line)
        for line in (state_dir() / "invocations.jsonl").read_text().splitlines()
    ]


class NeverEofStdin:
    """A stdin whose read() never reaches EOF — bounded so a test cannot hang."""

    LIMIT = 5.0

    def read(self):
        deadline = time.monotonic() + self.LIMIT
        while time.monotonic() < deadline:
            time.sleep(0.02)
        return ""


def test_batch_stdin_that_never_eofs_hits_the_timeout(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[]))
    monkeypatch.setattr("sys.stdin", NeverEofStdin())

    started = time.monotonic()
    assert main(["batch", "--timeout", "0.3", "--json"]) == 1
    elapsed = time.monotonic() - started

    assert elapsed < NeverEofStdin.LIMIT
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "TIMEOUT"
    assert read_journal()[-1]["error"] == "TIMEOUT"


def test_usage_error_under_json_emits_one_usage_envelope(config_env, capsys):
    assert main(["search", "--json"]) == 1

    captured = capsys.readouterr()
    [line] = captured.out.splitlines()
    payload = json.loads(line)
    assert payload["error"]["code"] == "USAGE"
    # CONTRACT §2: the same object is the last line of stderr, after the usage.
    assert captured.err.splitlines()[-1] == line
    assert read_journal()[-1]["error"] == "USAGE"


def test_usage_error_without_json_stays_stderr_only(config_env, capsys):
    assert main(["search"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "search requires CHAT QUERY" in captured.err


def test_option_like_text_is_not_a_silent_help_exit(config_env, capsys):
    """argparse groups short options, so a `-hi` message fires -h and exits 0."""
    assert main(["send", "@somebody", "-hi", "--preview"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "show this help message" not in captured.err
    assert "usage: tg" in captured.err


def test_option_like_text_writes_nothing_to_stdout_under_json(config_env, capsys):
    assert main(["draft", "set", "@somebody", "-hi", "--preview", "--json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "show this help message" not in captured.err


def test_explicit_help_still_prints_and_exits_zero(config_env, capsys):
    assert main(["--help"]) == 0
    assert "usage: tg" in capsys.readouterr().out

    assert main(["draft", "set", "-h"]) == 0
    assert "usage: tg draft set" in capsys.readouterr().out


def test_explicit_version_still_prints_and_exits_zero(config_env, capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def _record_wait_for(monkeypatch):
    observed = []
    original = cli.asyncio.wait_for

    async def record(awaitable, timeout):
        observed.append(timeout)
        return await original(awaitable, timeout)

    monkeypatch.setattr(cli.asyncio, "wait_for", record)
    return observed


def test_clone_init_has_the_uniform_default_deadline(config_env, monkeypatch, capsys):
    """D2: no command keeps a deadline exemption; clone init gets the 60s default.

    Governed sleep does not count against it (ADR-0072 decision 6), so a
    paced run is not punished for pacing — the deadline is a hang detector,
    not a job bound.
    """
    from tests.test_cli_clone_init import CloneInitClient

    make_session_fake(monkeypatch, CloneInitClient())

    assert main(["clone", "init", "@source", "--json"]) == 0


def test_clone_init_still_honours_an_explicit_timeout(config_env, monkeypatch, capsys):
    from tests.test_cli_clone_init import CloneInitClient

    make_session_fake(monkeypatch, CloneInitClient())

    assert main(["clone", "init", "@source", "--timeout", "30", "--json"]) == 0


def test_clone_refresh_has_the_uniform_default_deadline(
    config_env, monkeypatch, capsys
):
    from tests.test_cli_clone_refresh import RefreshClient, _eligible_pair, seed_clone
    from tgcli.clone import state

    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src, dst = _eligible_pair()
    make_session_fake(monkeypatch, RefreshClient([src], [dst]))

    assert main(["clone", "refresh", "@source", "--json"]) == 0
