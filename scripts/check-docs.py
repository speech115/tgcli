#!/usr/bin/env python3
"""Fail-closed gate for the user guide (ADR-0041).

Documentation that drifts is worse than none, so every claim the guide makes
about the CLI surface is checked against the parser itself:

  1. every ``--flag`` named in ``docs/guide/*.md`` exists somewhere in the
     argparse tree;
  2. every ``tg <command>`` named there is a real command;
  3. every relative markdown link resolves on disk.

Run from the repo root: ``uv run python scripts/check-docs.py``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "docs" / "guide"

sys.path.insert(0, str(REPO / "src"))

from tgcli.parser import build_parser  # noqa: E402

# Surfaces that were removed but that the guide may still name as history.
REMOVED_COMMANDS = {"mirror"}


def walk(parser: argparse.ArgumentParser) -> tuple[set[str], set[str]]:
    """Collect every option string and every command name in the tree."""
    flags: set[str] = set()
    commands: set[str] = set()
    for action in parser._actions:
        flags.update(opt for opt in action.option_strings if opt.startswith("--"))
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for name, subparser in choices.items():
                commands.add(name)
                sub_flags, sub_commands = walk(subparser)
                flags |= sub_flags
                commands |= sub_commands
    return flags, commands


def main() -> int:
    flags, commands = walk(build_parser())
    flags |= {"--help", "--version"}
    commands |= REMOVED_COMMANDS | {"--help", "--version"}

    pages = sorted(GUIDE.glob("*.md"))
    if not pages:
        print(f"FAIL no guide pages found under {GUIDE}")
        return 1

    problems: list[str] = []
    for page in pages:
        text = page.read_text()

        for flag in sorted(set(re.findall(r"--[a-z][a-z0-9-]*", text))):
            if flag not in flags:
                problems.append(f"{page.name}: unknown flag {flag}")

        # drop value-taking flags so their argument is not read as a command
        stripped = re.sub(r"\s+", " ", re.sub(r"--(?:account|timeout) \S+", "", text))
        for name in sorted(set(re.findall(r"\btg (?:--[a-z-]+ )*([a-z-]+)", stripped))):
            if name not in commands:
                problems.append(f"{page.name}: unknown command 'tg {name}'")

        for target in re.findall(r"\]\(([^)#]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (page.parent / target).resolve().exists():
                problems.append(f"{page.name}: dead link -> {target}")

    for problem in problems:
        print("FAIL", problem)
    print(f"guide pages checked: {len(pages)}; problems: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
