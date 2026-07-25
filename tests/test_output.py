import json

from tgcli import output
from tgcli.errors import RateLimitError


def test_emit_json_writes_one_document_to_stdout(capsys):
    output.emit_json({"a": 1, "s": "приве\tт"})
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"a": 1, "s": "приве\tт"}
    assert captured.out.endswith("\n")
    assert captured.err == ""


def test_emit_json_lines_writes_one_document_per_line(capsys):
    output.emit_json_lines([{"ok": True}, {"ok": False, "message": "привет"}])

    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"ok": True},
        {"ok": False, "message": "привет"},
    ]


def test_emit_plain_writes_tsv_with_empty_for_none(capsys):
    output.emit_plain([(1, "x", None), (2, "y", "z")])
    assert capsys.readouterr().out == "1\tx\t\n2\ty\tz\n"


def test_note_goes_to_stderr_only(capsys):
    output.note("working...")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "working...\n"


def test_emit_error_json_mode_is_machine_readable(capsys):
    output.emit_error(RateLimitError("flood", retry_after=42), as_json=True)
    captured = capsys.readouterr()
    expected = {"error": {"code": "FLOOD_WAIT", "message": "flood", "retry_after": 42}}
    assert json.loads(captured.out) == expected
    assert json.loads(captured.err) == expected
    assert captured.out == captured.err


def test_emit_error_human_mode(capsys):
    output.emit_error(RateLimitError("flood", retry_after=42), as_json=False)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "error: flood\n"
