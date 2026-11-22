"""Checks that keep a live account and its safety contracts out of trouble.

- Floods are caught through ``tg_errors.FLOOD_ERRORS``: naming one class lets
  the other 420 (FLOOD_PREMIUM_WAIT) pass as a refusal.
- Every ``iter_messages`` call passes ``wait_time``: Telethon otherwise reads
  history pages back to back.
- State read back later is written through ``tgcli.atomic.replace_text``,
  never a bare ``write_text`` a crash can leave half-written.
- Chat references resolve through ``tgcli.chatref.resolve`` or
  ``tgcli.chatref.resolve_input``, never bare ``chatref.parse(...)``: raw
  parsing skips marked peer-id verification and preview peer pinning.
- Async ``except Exception`` handlers that swallow errors must re-raise
  ``tg_errors.RUN_ABORTING_ERRORS`` or ``tg_errors.NOT_REFUSALS`` first so
  floods and dead sessions are never masked as warnings (ADR-0129).
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


def _raw_chatref_parse_errors(path: Path, relative: str) -> list[str]:
    """Raw chatref.parse() bypasses marked peer-id checks and preview pinning."""
    tree = ast.parse(path.read_text(), filename=str(path))
    return [
        f"{relative}:{node.lineno} calls chatref.parse directly; use "
        "tgcli.chatref.resolve or tgcli.chatref.resolve_input instead so "
        "marked peer ids and preview targets are verified"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "chatref"
        and node.func.attr == "parse"
    ]


ABORT_GUARD_ALLOWLIST = frozenset(
    {
        "src/tgcli/cli.py",
        "src/tgcli/commands/doctor.py",
        "src/tgcli/commands/setup.py",
    }
)


def _contains_async(stmts: list[ast.stmt]) -> bool:
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Await | ast.AsyncFor | ast.AsyncWith):
                return True
    return False


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    names: set[str] = set()
    for node in ast.walk(handler.type):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _always_reraises(handler: ast.ExceptHandler) -> bool:
    return bool(handler.body) and isinstance(handler.body[-1], ast.Raise)


def _swallowed_abort_errors(path: Path, relative: str) -> list[str]:
    """An async `except Exception` that swallows errors must let floods and
    dead sessions escape first."""
    tree = ast.parse(path.read_text(), filename=str(path))
    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not _contains_async(node.body):
            continue
        prior_names: set[str] = set()
        for handler in node.handlers:
            names = _handler_names(handler)
            catches_broad = handler.type is None or "Exception" in names
            if catches_broad and not _always_reraises(handler):
                guarded = (
                    "RUN_ABORTING_ERRORS" in prior_names
                    or "NOT_REFUSALS" in prior_names
                    or (
                        "FLOOD_ERRORS" in prior_names
                        and "UnauthorizedError" in prior_names
                    )
                )
                if not guarded:
                    errors.append(
                        f"{relative}:{handler.lineno} swallows Exception in an "
                        "async block without guarding tgcli.tg_errors."
                        "RUN_ABORTING_ERRORS or tgcli.tg_errors.NOT_REFUSALS first"
                    )
            prior_names |= names
    return errors


def check(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted((root / "src/tgcli").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        errors.extend(_state_write_errors(path, relative))
        if relative != "src/tgcli/tg_errors.py":
            errors.extend(_flood_class_errors(path, relative))
        if relative != "src/tgcli/chatref.py":
            errors.extend(_raw_chatref_parse_errors(path, relative))
        if relative not in ABORT_GUARD_ALLOWLIST:
            errors.extend(_swallowed_abort_errors(path, relative))
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
