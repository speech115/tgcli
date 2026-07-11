import json

from tgcli import invocations
from tgcli.session import state_dir


def test_invocation_journal_records_metadata_without_message_content():
    invocations.log_invocation(
        command="send",
        account="main",
        exit_code=0,
        duration_ms=42,
    )

    [entry] = [json.loads(line) for line in (state_dir() / "invocations.jsonl").read_text().splitlines()]
    assert entry == {
        "timestamp": entry["timestamp"],
        "command": "send",
        "account": "main",
        "exit_code": 0,
        "duration_ms": 42,
    }


def test_invocation_journal_write_failure_warns_without_raising(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "state"
    blocker.write_text("not a directory")
    monkeypatch.setattr(invocations, "state_dir", lambda: blocker)

    invocations.log_invocation(command="dialogs", exit_code=0, duration_ms=1)

    assert "warning: invocation journal not written:" in capsys.readouterr().err
