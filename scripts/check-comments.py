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
LANGUAGES = {".sh": "shell", ".toml": "toml", ".yml": "yaml", ".yaml": "yaml"}
SHELL_WORD_BREAK = set(" \t;&|()")
YAML_SCALAR_START = set(":-[{,?")


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


def _opens_quote(language: str, line: str, index: int) -> bool:
    if language != "yaml":
        return True
    before = line[:index].rstrip()
    return not before or before[-1] in YAML_SCALAR_START


def _starts_comment(language: str, line: str, index: int) -> bool:
    if language == "toml" or index == 0:
        return True
    if language == "shell":
        return line[index - 1] in SHELL_WORD_BREAK
    return line[index - 1] in " \t"


def _text_comments(source: str, language: str) -> list[tuple[int, str]]:
    found = []
    quote = ""
    for number, line in enumerate(source.splitlines(), 1):
        if _is_shebang(number, line):
            continue
        index = 0
        while index < len(line):
            char = line[index]
            if quote:
                if char == "\\" and quote in ('"', '"""'):
                    index += 2
                    continue
                if line.startswith(quote, index):
                    index += len(quote)
                    if (
                        quote == "'"
                        and language == "yaml"
                        and line[index : index + 1] == "'"
                    ):
                        index += 1
                        continue
                    quote = ""
                    continue
                index += 1
                continue
            if char == "\\" and language == "shell":
                index += 2
                continue
            if char in "\"'" and _opens_quote(language, line, index):
                triple = char * 3
                quote = (
                    triple
                    if language == "toml" and line.startswith(triple, index)
                    else char
                )
                index += len(quote)
                continue
            if char == "#" and _starts_comment(language, line, index):
                found.append((number, line[index:].strip()))
                break
            index += 1
    return found


def _language(relative: str) -> str | None:
    if relative.startswith(".githooks/"):
        return "shell"
    return LANGUAGES.get(Path(relative).suffix)


def comments(relative: str) -> list[tuple[int, str]] | None:
    path = ROOT / relative
    if path.suffix == ".py":
        return _python_comments(path.read_text())
    language = _language(relative)
    if language is None:
        return None
    return _text_comments(path.read_text(), language)


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
