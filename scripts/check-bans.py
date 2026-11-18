"""Three checks that keep a live account out of trouble.

- Floods are caught through ``tg_errors.FLOOD_ERRORS``: naming one class lets
  the other 420 (FLOOD_PREMIUM_WAIT) pass as a refusal.
- Every ``iter_messages`` call passes ``wait_time``: Telethon otherwise reads
  history pages back to back.
- State read back later is written through ``tgcli.atomic.replace_text``,
  never a bare ``write_text`` a crash can leave half-written.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


def _state_write_errors(path: Path, relative: str) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    errors: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
        ):
            errors.append(
                f"{relative}:{node.lineno} calls write_text; state files must "
                "go through tgcli.atomic.replace_text"
            )
    return errors


FLOOD_CLASSES = {"FloodWaitError", "FloodPremiumWaitError"}


def _flood_class_errors(path: Path, relative: str) -> list[str]:
    """Floods are caught through ``tg_errors.FLOOD_ERRORS``: one class alone
    lets the other 420 pass as a refusal."""
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        f"{relative}:{node.lineno} names {name}; catch floods through "
        "tgcli.tg_errors.FLOOD_ERRORS"
        for node in ast.walk(tree)
        if (
            name := node.attr
            if isinstance(node, ast.Attribute)
            else node.id
            if isinstance(node, ast.Name)
            else None
        )
        in FLOOD_CLASSES
    ]


def _unpaced_history_errors(path: Path, relative: str) -> list[str]:
    """Telethon reads history 100 messages a request, back to back unless it
    is told to wait; nothing else spaces those pages."""
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        f"{relative}:{node.lineno} calls iter_messages without wait_time; pass "
        "wait_time=pace.PAGE_WAIT_S in a loop or bulk walk, or "
        "wait_time=pace.INTERACTIVE_PAGE_WAIT for one chat read at the "
        "owner's request"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "iter_messages"
        and not any(keyword.arg == "wait_time" for keyword in node.keywords)
    ]


def check(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted((root / "src/tgcli").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        errors.extend(_state_write_errors(path, relative))
        if relative != "src/tgcli/tg_errors.py":
            errors.extend(_flood_class_errors(path, relative))
        errors.extend(_unpaced_history_errors(path, relative))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args(argv)
    errors = check(args.root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("ban checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
