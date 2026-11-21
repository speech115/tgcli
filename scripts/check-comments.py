"""Ratchet code comments down to zero.

Decisions live in docs/decisions (ADR-0132), not in comments. Tool directives
(``type: ignore``, ``noqa``, ``pragma: no cover``, ``pyright: ignore``) and a
first-line shebang are not comments. Every other comment counts against
``scripts/comment-baseline.json``: a file may not gain one, and a file that
lost some must lower its entry (``--update``) so the fence only tightens.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "comment-baseline.json"
DIRECTIVE = re.compile(
    r"#\s*(type:\s*ignore(\[[\w\-, ]+\])?"
    r"|noqa(:\s*[A-Z]+\d+(\s*,\s*[A-Z]+\d+)*)?"
    r"|pragma:\s*no cover"
    r"|pyright:\s*ignore(\[[\w\-, ]+\])?)"
)
TEXT_SUFFIXES = {".sh", ".toml", ".yml", ".yaml"}
TEXT_COMMENT = re.compile(r"(^|\s)#(\s|$)")


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if (ROOT / line).is_file()]


def _is_shebang(line: int, text: str) -> bool:
    return line == 1 and text.startswith("#!")


def _python_comments(source: str) -> list[tuple[int, str]]:
    found = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        line, text = token.start[0], token.string
        if _is_shebang(line, text) or DIRECTIVE.fullmatch(text.strip()):
            continue
        found.append((line, text))
    return found


def _text_comments(source: str) -> list[tuple[int, str]]:
    return [
        (line, text.strip())
        for line, text in enumerate(source.splitlines(), 1)
        if TEXT_COMMENT.search(text) and not _is_shebang(line, text)
    ]


def comments(relative: str) -> list[tuple[int, str]] | None:
    path = ROOT / relative
    if path.suffix == ".py":
        return _python_comments(path.read_text())
    if path.suffix in TEXT_SUFFIXES or relative.startswith(".githooks/"):
        return _text_comments(path.read_text())
    return None


def census() -> dict[str, list[tuple[int, str]]]:
    result = {}
    for relative in _tracked():
        found = comments(relative)
        if found:
            result[relative] = found
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update",
        action="store_true",
        help="lower the baseline to the current counts (never raises one)",
    )
    parser.add_argument(
        "--list", action="store_true", help="print every counted comment"
    )
    args = parser.parse_args(argv)
    baseline: dict[str, int] = json.loads(BASELINE.read_text())
    found = census()
    if args.list:
        for relative, items in sorted(found.items()):
            for line, text in items:
                print(f"{relative}:{line}: {text}")
    grown = {
        relative: items
        for relative, items in found.items()
        if len(items) > baseline.get(relative, 0)
    }
    if grown:
        for relative, items in sorted(grown.items()):
            allowed = baseline.get(relative, 0)
            print(
                f"{relative}: {len(items)} comments, baseline allows {allowed}",
                file=sys.stderr,
            )
            for line, text in items:
                print(f"  {relative}:{line}: {text}", file=sys.stderr)
        print(
            "check-comments: no new comments; record a decision in "
            "docs/decisions instead (ADR-0132)",
            file=sys.stderr,
        )
        return 1
    current = {relative: len(items) for relative, items in sorted(found.items())}
    if current != baseline:
        if args.update:
            BASELINE.write_text(json.dumps(current, indent=2) + "\n")
            print("check-comments: baseline lowered", file=sys.stderr)
            return 0
        print(
            "check-comments: comments were removed; ratchet the baseline down "
            "with `uv run python scripts/check-comments.py --update`",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
