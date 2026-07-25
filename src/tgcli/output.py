"""The only module allowed to write to stdout (docs/CONTRACT.md §2)."""

import json
import sys

from tgcli.errors import TgcliError


def emit_json(data) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def emit_json_lines(items) -> None:
    for item in items:
        emit_json(item)


def emit_plain(rows) -> None:
    for row in rows:
        sys.stdout.write(
            "\t".join("" if cell is None else str(cell) for cell in row) + "\n"
        )


def note(message: str) -> None:
    sys.stderr.write(message + "\n")


def emit_error(err: TgcliError, *, as_json: bool) -> None:
    if as_json:
        payload = {"error": {"code": err.code, "message": str(err), **err.details}}
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        sys.stdout.write(line)
        sys.stderr.write(line)
    else:
        note(f"error: {err}")
