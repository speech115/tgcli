import json

import pytest

from tgcli import output
from tgcli.errors import RateLimitError


def test_emit_json_writes_one_document_to_stdout(capsys):
    output.emit_json({"a": 1, "s": "приве\tт"})
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"a": 1, "s": "приве\tт"}
    assert captured.out.endswith("\n")
    assert captured.err == ""


HOSTILE = "Ch\x1bannel\rX\x08Y\nZ\tW"


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


def test_emit_error_json_mode_passes_control_characters_through(capsys):
    output.emit_error(RateLimitError(HOSTILE, retry_after=1), as_json=True)
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["message"] == HOSTILE


def test_emit_json_escapes_del_and_c1_but_keeps_the_data(capsys):
    hostile = "a\x7fb\x9b2Kc\x9d8;;x\x9cd"
    output.emit_json({"s": hostile, "ru": "привет"})
    out = capsys.readouterr().out
    assert not any(0x7F <= ord(ch) <= 0x9F for ch in out)
    assert "\\u009b" in out and "привет" in out
    assert json.loads(out) == {"s": hostile, "ru": "привет"}


def test_emit_error_json_escapes_c1_on_both_streams(capsys):
    output.emit_error(RateLimitError("bad\x9bname"), as_json=True)
    captured = capsys.readouterr()
    for stream in (captured.out, captured.err):
        assert "\x9b" not in stream
        assert json.loads(stream)["error"]["message"] == "bad\x9bname"


def test_emit_json_writes_nothing_when_encoding_fails_late(capsys):
    data = {"first": "written?"}
    data["loop"] = data
    with pytest.raises(ValueError):
        output.emit_json(data)
    assert capsys.readouterr().out == ""
