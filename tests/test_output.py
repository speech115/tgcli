import json

from tgcli import output
from tgcli.errors import RateLimitError


def test_emit_json_writes_one_document_to_stdout(capsys):
    output.emit_json({"a": 1, "s": "приве\tт"})
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"a": 1, "s": "приве\tт"}
    assert captured.out.endswith("\n")
    assert captured.err == ""


def test_emit_plain_writes_tsv_with_empty_for_none(capsys):
    output.emit_plain([(1, "x", None), (2, "y", "z")])
    assert capsys.readouterr().out == "1\tx\t\n2\ty\tz\n"


HOSTILE = "Ch\x1bannel\rX\x08Y\nZ\tW"


def test_sanitize_strips_c0_c1_controls_but_keeps_text():
    assert output.sanitize(HOSTILE) == "ChannelXYZW"
    assert output.sanitize("plain") == "plain"
    assert output.sanitize("\x9bcsi\x7fdel") == "csidel"


def test_emit_plain_strips_control_characters_from_cells(capsys):
    output.emit_plain([(HOSTILE, "ok")])
    assert capsys.readouterr().out == "ChannelXYZW\tok\n"


def test_emit_plain_keeps_the_writers_own_tab_and_newline(capsys):
    output.emit_plain([("a", "b"), ("c", "d")])
    assert capsys.readouterr().out == "a\tb\nc\td\n"


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


def test_emit_error_human_mode_strips_control_characters(capsys):
    output.emit_error(RateLimitError(HOSTILE, retry_after=1), as_json=False)
    assert capsys.readouterr().err == "error: ChannelXYZW\n"


def test_emit_error_json_mode_passes_control_characters_through(capsys):
    output.emit_error(RateLimitError(HOSTILE, retry_after=1), as_json=True)
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["message"] == HOSTILE


def test_emit_error_masks_phones_in_typed_errors(capsys):
    """CONTRACT §10 promises masked phones. Only the RUNTIME path masked
    them, so a typed error such as `phone not found: '+79991234567'` reached
    stdout, stderr and agent transcripts raw."""
    from tgcli.errors import NotFoundError

    err = NotFoundError("phone not found: '+79991234567'")
    output.emit_error(err, as_json=True)
    output.emit_error(err, as_json=False)
    captured = capsys.readouterr()
    assert "79991234567" not in captured.out + captured.err
    assert json.loads(captured.out)["error"]["message"] == "phone not found: '+7…67'"
    assert "phone not found: '+7…67'" in captured.err
