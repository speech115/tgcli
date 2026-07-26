"""The only module allowed to write to stdout (docs/CONTRACT.md §2)."""

import json
import sys

from tgcli.errors import TgcliError

# CONTRACT.md §8: human/plain output strips control characters from untrusted
# content. C0 (including tab and newline), DEL, and C1 all go — a name that
# smuggles its own tab or newline would also forge a TSV column or row, and the
# separators are the writer's to produce. --json keeps the data verbatim.
_CONTROLS = dict.fromkeys(
    [*range(0x00, 0x20), 0x7F, *range(0x80, 0xA0)],
    None,
)


def sanitize(text: str) -> str:
    """Drop C0/C1 control characters from Telegram-controlled text."""
    return text.translate(_CONTROLS)


def emit_json(data) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")
    # Flush here so a reader that hung up raises BrokenPipeError while cli.py
    # can still handle it, not during interpreter shutdown.
    sys.stdout.flush()


def emit_json_lines(items) -> None:
    for item in items:
        emit_json(item)


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
    if as_json:
        payload = {"error": {"code": err.code, "message": str(err), **err.details}}
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        sys.stdout.write(line)
        # Same reason as emit_json: surface a hung-up reader to cli.py, not to
        # interpreter shutdown.
        sys.stdout.flush()
        sys.stderr.write(line)
    else:
        note(f"error: {sanitize(str(err))}")
