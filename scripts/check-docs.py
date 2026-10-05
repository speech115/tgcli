#!/usr/bin/env python3
"""Fail-closed gate: active docs must match the real CLI.

Documentation that drifts is worse than none, so every claim README.md and
SKILL.md make about the CLI surface is checked against the parser itself:

  1. every ``--flag`` they name exists somewhere in the argparse tree;
  2. every ``tg <command>`` they name is a real command;
  3. every relative markdown link resolves on disk;
  4. README names exactly the root global flags;
  5. README scopes ``random_id`` confirmation to send/forward and requires
     retrying the same preview id after a failed ``clone init`` /
     ``clone refresh`` commit (ADR-0083).

Run from the repo root: ``uv run python scripts/check-docs.py``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"
SKILL = REPO / "SKILL.md"

sys.path.insert(0, str(REPO / "src"))

from tgcli.parser import build_parser  # noqa: E402


def walk(parser: argparse.ArgumentParser) -> tuple[set[str], dict[str, set[str]]]:
    """Collect every option string, and each command's subcommand names."""
    flags: set[str] = set()
    commands: dict[str, set[str]] = {}
    for action in parser._actions:
        flags.update(opt for opt in action.option_strings if opt.startswith("--"))
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for name, subparser in choices.items():
                sub_flags, sub_commands = walk(subparser)
                flags |= sub_flags
                commands[name] = set(sub_commands)
    return flags, commands


def root_global_flags(parser: argparse.ArgumentParser) -> set[str]:
    """Long options exposed by the root parser, excluding ubiquitous help."""
    return {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }


def readme_global_flag_problems(
    readme: Path, parser: argparse.ArgumentParser
) -> list[str]:
    text = readme.read_text()
    match = re.search(
        r"\*\*Global flags:\*\*(.*?)\n\n\*\*Environment overrides:\*\*",
        text,
        re.DOTALL,
    )
    if match is None:
        return [f"{readme.name}: no Global flags section"]
    documented = set(re.findall(r"--[a-z][a-z0-9-]*", match.group(1)))
    expected = root_global_flags(parser)
    problems = [
        f"{readme.name}: global flags missing {flag}"
        for flag in sorted(expected - documented)
    ]
    problems += [
        f"{readme.name}: unknown root global flag {flag}"
        for flag in sorted(documented - expected)
    ]
    return problems


def readme_random_id_problems(readme: Path) -> list[str]:
    """Reject the two broad phrasings that erased operation-specific retries."""
    paragraphs = re.split(r"\n\s*\n", readme.read_text())
    broad = any(
        (
            re.search(
                r"`send`.*?`edit`.*?`delete`.*?`forward`.*?`draft`"
                r".*?`random_id`",
                paragraph,
                re.DOTALL,
            )
            or re.search(r"A commit\b.*?`random_id`", paragraph, re.DOTALL)
        )
        for paragraph in paragraphs
    )
    if not broad:
        return []
    return [f"{readme.name}: random_id guarantee must be scoped to send and forward"]


def readme_clone_retry_problems(readme: Path) -> list[str]:
    """Clone commits are retryable via begin_commit (ADR-0083), not spend-once."""
    lines = readme.read_text().splitlines()
    try:
        start = next(
            i
            for i, line in enumerate(lines)
            if line.lstrip().startswith("- `clone init`") and "`clone refresh`" in line
        )
    except StopIteration:
        return [
            f"{readme.name}: clone init/refresh retries must reuse the same preview id"
        ]
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.startswith("- ") or (line.strip() and not line.startswith((" ", "\t"))):
            break
        block.append(line)
    block_text = "\n".join(block)
    if "fresh preview" in block_text or "same preview id" not in block_text:
        return [
            f"{readme.name}: clone init/refresh retries must reuse the same preview id"
        ]
    return []


def page_problems(
    page: Path, flags: set[str], commands: dict[str, set[str]]
) -> list[str]:
    """Flags, commands, and relative links a page names must all be real."""
    text = page.read_text()
    prose = re.sub(r"\]\([^)]*\)", "]", text)  # a link target is not a flag
    problems = [
        f"{page.name}: unknown flag {flag}"
        for flag in sorted(set(re.findall(r"--[a-z][a-z0-9-]*", prose)))
        if flag not in flags
    ]

    # drop value-taking flags so their argument is not read as a command
    stripped = re.sub(
        r"\s+",
        " ",
        re.sub(
            r"--(?:account|timeout|max-runtime|session-role|cursor|peer|drop-peer"
            r"|wait) \S+",
            "",
            prose,
        ),
    )
    named = re.findall(
        r"\btg (?:--[a-z-]+ )*([a-z][a-z-]*)(?: ([a-z][a-z-]*))?", stripped
    )
    for name, sub in sorted(set(named)):
        if name not in commands:
            problems.append(f"{page.name}: unknown command 'tg {name}'")
        elif sub and commands[name] and sub not in commands[name]:
            problems.append(f"{page.name}: unknown command 'tg {name} {sub}'")

    for target in re.findall(r"\]\(([^)#]+)\)", text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if not (page.parent / target).resolve().exists():
            problems.append(f"{page.name}: dead link -> {target}")
    return problems


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Check documentation consistency.")
    cli.add_argument("--readme", type=Path, default=README)
    cli.add_argument("--skill", type=Path, default=SKILL)
    args = cli.parse_args(argv)

    parser = build_parser()
    flags, commands = walk(parser)
    flags |= {"--help", "--version"}

    problems = readme_global_flag_problems(args.readme, parser)
    problems += readme_random_id_problems(args.readme)
    problems += readme_clone_retry_problems(args.readme)
    for page in (args.readme, args.skill):
        problems += page_problems(page, flags, commands)

    for problem in problems:
        print("FAIL", problem)
    print(f"problems: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
