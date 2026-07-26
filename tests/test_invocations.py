import json
import os

from tgcli import invocations
from tgcli.session import state_dir


def test_invocation_journal_records_metadata_without_message_content():
    invocations.log_invocation(
        command="send",
        account="main",
        exit_code=0,
        duration_ms=42,
    )

    [entry] = [
        json.loads(line)
        for line in (state_dir() / "invocations.jsonl").read_text().splitlines()
    ]
    assert entry == {
        "timestamp": entry["timestamp"],
        "command": "send",
        "account": "main",
        "exit_code": 0,
        "duration_ms": 42,
    }


def test_invocation_journal_file_is_0600_in_0700_root(wide_umask):
    invocations.log_invocation(command="send", exit_code=0, duration_ms=1)

    path = state_dir() / "invocations.jsonl"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700


def test_invocation_journal_repairs_a_loose_existing_file(wide_umask):
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "invocations.jsonl"
    path.touch()
    os.chmod(path, 0o666)

    invocations.log_invocation(command="send", exit_code=0, duration_ms=1)

    assert path.stat().st_mode & 0o777 == 0o600


def test_invocation_journal_write_failure_warns_without_raising(
    tmp_path, monkeypatch, capsys
):
    blocker = tmp_path / "state"
    blocker.write_text("not a directory")
    monkeypatch.setattr(invocations, "state_dir", lambda: blocker)

    invocations.log_invocation(command="dialogs", exit_code=0, duration_ms=1)

    assert "warning: invocation journal not written:" in capsys.readouterr().err
