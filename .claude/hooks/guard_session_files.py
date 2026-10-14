"""PreToolUse hook: refuse a bare Python interpreter near Telegram session files.

The pinned Telethon writes a session schema that a system Telethon crashes on,
so a `.session` file is only ever opened through `uv run` or `.venv/bin/python`.
Claude Code passes the tool call as JSON on stdin; exit 2 blocks it and shows
stderr to the agent.
"""

import json
import re
import sys
from collections.abc import Iterator

SESSION_HINT = re.compile(r"\.session\b|/sessions/")
SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|]|\$\(|`")
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*")
WRAPPERS = ("env", "exec", "time", "sudo", "nohup")
BARE_PYTHON = re.compile(r"(?:\S*/)?python(?:3(?:\.\d+)?)?")
PINNED_PYTHON = re.compile(r"(?:^|\S*/)\.venv/bin/python(?:3(?:\.\d+)?)?")


def _lines_with_bodies(command: str) -> Iterator[tuple[str, str]]:
    """Yield each shell line with the heredoc bodies it feeds."""
    lines = command.split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        body = []
        for delimiter in HEREDOC.findall(line):
            while index < len(lines) and lines[index].strip() != delimiter:
                body.append(lines[index])
                index += 1
            index += 1
        yield line, "\n".join(body)


def _first_word(segment: str) -> str:
    words = segment.split()
    while words and (words[0] in WRAPPERS or ENV_ASSIGNMENT.fullmatch(words[0])):
        words.pop(0)
    return words[0].strip("({") if words else ""


def _runs_bare_python(line: str) -> bool:
    for segment in SEGMENT_SPLIT.split(line):
        word = _first_word(segment)
        if BARE_PYTHON.fullmatch(word) and not PINNED_PYTHON.fullmatch(word):
            return True
    return False


def is_blocked(command: str) -> bool:
    pairs = list(_lines_with_bodies(command))
    shell_text = "\n".join(line for line, _ in pairs)
    return any(
        _runs_bare_python(line)
        and (SESSION_HINT.search(shell_text) or SESSION_HINT.search(body))
        for line, body in pairs
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if is_blocked(command):
        sys.stderr.write(
            "blocked: a bare python interpreter must not touch Telegram .session "
            "files (the pinned Telethon schema crashes older versions). "
            "Use `uv run tg ...` or `.venv/bin/python` instead.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
