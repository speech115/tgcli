#!/usr/bin/env python3
"""Fail-closed gate for the user guide (ADR-0041) and release bookkeeping.

Documentation that drifts is worse than none, so every claim the guide makes
about the CLI surface is checked against the parser itself:

  1. every ``--flag`` named in ``docs/guide/*.md`` exists somewhere in the
     argparse tree;
  2. every ``tg <command>`` named there is a real command;
  3. every relative markdown link resolves on disk.

``CHANGELOG.md`` drifts the same way, and nothing caught it: five releases
shipped a ``## [x.y.z]`` section whose link definition was never added, so the
heading rendered as literal brackets. The bookkeeping ADR-0038 asks for is
checked here too, because the gate is the one step every session actually runs:

  4. every release section has its ``[x.y.z]:`` link definition, that link
     names the same version it defines, and no definition outlives its section.

Run from the repo root: ``uv run python scripts/check-docs.py``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "docs" / "guide"
CHANGELOG = REPO / "CHANGELOG.md"

# "## [1.2.16] — 2026-07-26" — a released section. `## [Unreleased]` is not one.
SECTION = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
# "[1.2.16]: https://.../compare/v1.2.15...v1.2.16"
DEFINITION = re.compile(r"^\[(\d+\.\d+\.\d+)\]:\s*(\S+)", re.MULTILINE)

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


def release_problems(changelog: Path) -> tuple[list[str], int]:
    """Every released section is linkable, and every link names its own release.

    A section without a definition renders as literal `[1.2.16]`; a definition
    whose URL ends in another version silently points at the wrong diff. Both
    are invisible in review and neither can be caught by a link checker that
    only walks the guide.

    The section↔definition relation is 1:1, so duplicates on either side are
    reported rather than collapsed. `CHANGELOG.md` is a shared integrator-merged
    file (AGENTS.md): two slices appending near the same anchor, or a conflict
    resolved by keeping both hunks, is exactly how a version ends up written
    twice — and a duplicate definition would otherwise silently win.
    """
    text = changelog.read_text()
    sections = SECTION.findall(text)
    defined = DEFINITION.findall(text)
    definitions = dict(defined)

    problems = [
        f"{changelog.name}: release {version} has {sections.count(version)}"
        " sections; expected exactly one"
        for version in sorted(set(sections))
        if sections.count(version) > 1
    ]
    urls = [version for version, _ in defined]
    problems += [
        f"{changelog.name}: [{version}]: has {urls.count(version)}"
        " link definitions; expected exactly one"
        for version in sorted(set(urls))
        if urls.count(version) > 1
    ]
    problems += [
        f"{changelog.name}: release {version} has no [{version}]: link definition"
        for version in sections
        if version not in definitions
    ]
    problems += [
        f"{changelog.name}: [{version}]: link points at {url}, not v{version}"
        for version, url in sorted(definitions.items())
        if version in sections and not url.endswith(f"v{version}")
    ]
    problems += [
        f"{changelog.name}: [{version}]: link definition has no release section"
        for version in sorted(definitions.keys() - set(sections))
    ]
    return problems, len(set(sections))


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Check documentation consistency.")
    cli.add_argument("--changelog", type=Path, default=CHANGELOG)
    args = cli.parse_args(argv)

    if not args.changelog.is_file():
        print(f"FAIL no changelog at {args.changelog}")
        return 1

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
        stripped = re.sub(
            r"\s+",
            " ",
            re.sub(r"--(?:account|timeout|session-role) \S+", "", text),
        )
        for name in sorted(set(re.findall(r"\btg (?:--[a-z-]+ )*([a-z-]+)", stripped))):
            if name not in commands:
                problems.append(f"{page.name}: unknown command 'tg {name}'")

        for target in re.findall(r"\]\(([^)#]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (page.parent / target).resolve().exists():
                problems.append(f"{page.name}: dead link -> {target}")

    release_issues, releases = release_problems(args.changelog)
    problems += release_issues

    for problem in problems:
        print("FAIL", problem)
    print(
        f"guide pages checked: {len(pages)}; releases checked: {releases}; "
        f"problems: {len(problems)}"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
