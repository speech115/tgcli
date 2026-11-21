"""The only module allowed to write to stdout (docs/CONTRACT.md §2)."""

import json
import sys

from tgcli.errors import TgcliError
from tgcli.formatting import mask_phones_in_text

_CONTROLS = dict.fromkeys(
    [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)],
    None,
)


_JSON_CONTROL_ESCAPES = {code: f"\\u{code:04x}" for code in range(0x7F, 0xA0)}


def _json_line(data) -> str:
    text = json.dumps(data, ensure_ascii=False, default=str)
    return text.translate(_JSON_CONTROL_ESCAPES) + "\n"


def sanitize(text: str) -> str:
    """Drop C0/C1 control characters from Telegram-controlled text."""
    return text.translate(_CONTROLS)


def emit_json(data) -> None:
    encoder = json.JSONEncoder(ensure_ascii=False, default=str)
    chunks = [
        chunk.translate(_JSON_CONTROL_ESCAPES) for chunk in encoder.iterencode(data)
    ]
    sys.stdout.writelines(chunks)
    sys.stdout.write("\n")
    sys.stdout.flush()


def emit_plain(rows) -> None:
    for row in rows:
        sys.stdout.write(
            "\t".join("" if cell is None else sanitize(str(cell)) for cell in row)
            + "\n"
        )
    sys.stdout.flush()


def note(message: str) -> None:
    sys.stderr.write(message + "\n")


def emit_error(err: TgcliError, *, as_json: bool) -> None:
    message = mask_phones_in_text(str(err))
    if as_json:
        payload = {"error": {"code": err.code, "message": message, **err.details}}
        line = _json_line(payload)
        sys.stdout.write(line)
        sys.stdout.flush()
        sys.stderr.write(line)
    else:
        note(f"error: {sanitize(message)}")
