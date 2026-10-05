#!/usr/bin/env python3
"""Fail-closed gate: active docs must match the real CLI.

Documentation that drifts is worse than none, so every claim the guide makes
about the CLI surface is checked against the parser itself:

  1. every ``--flag`` named in ``docs/guide/*.md`` exists somewhere in the
     argparse tree;
  2. every ``tg <command>`` named there is a real command;
  3. every relative markdown link resolves on disk;
  4. README names exactly the root global flags and links every task guide page;
  5. README scopes ``random_id`` confirmation to send/forward and requires
     retrying the same preview id after a failed ``clone init`` /
     ``clone refresh`` commit (ADR-0083);
  6. any exhaustive benchmark claim requires actual parser-wide coverage.

Run from the repo root: ``uv run python scripts/check-docs.py``.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "docs" / "guide"
README = REPO / "README.md"
BENCH = REPO / "scripts" / "bench.py"

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


def root_global_flags(parser: argparse.ArgumentParser) -> set[str]:
    """Long options exposed by the root parser, excluding ubiquitous help."""
    return {
        option
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--") and option != "--help"
    }


def root_commands(parser: argparse.ArgumentParser) -> set[str]:
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            return set(choices)
    return set()


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


def readme_guide_link_problems(readme: Path, pages: list[Path]) -> list[str]:
    """Every task page is discoverable from the repository landing page."""
    text = readme.read_text()
    return [
        f"{readme.name}: guide page is not linked: docs/guide/{page.name}"
        for page in pages
        if page.name != "README.md" and f"(docs/guide/{page.name})" not in text
    ]


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


def benchmark_claim_problems(
    readme: Path,
    bench: Path,
    parser: argparse.ArgumentParser,
) -> list[str]:
    """An exhaustive claim is allowed only when the harness is exhaustive."""
    exhaustive_claim = re.compile(
        r"\b(?:exhaustive|complete)\b[^\n.]*\bbenchmark\b|"
        r"\bbenchmark\b[^\n.]*(?:\bevery command\b|\ball commands\b)",
        re.IGNORECASE,
    )
    if not exhaustive_claim.search(readme.read_text()):
        return []

    covered: set[str] = set()
    tree = ast.parse(bench.read_text(), filename=str(bench))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        function = node.func
        argv = node.args[1]
        if (
            not isinstance(function, ast.Attribute)
            or function.attr != "run"
            or not isinstance(argv, (ast.List, ast.Tuple))
            or not argv.elts
            or not isinstance(argv.elts[0], ast.Constant)
            or not isinstance(argv.elts[0].value, str)
        ):
            continue
        covered.add(argv.elts[0].value)

    missing = sorted(root_commands(parser) - covered)
    if not missing:
        return []
    return [f"benchmark claims every command but omits: {', '.join(missing)}"]


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Check documentation consistency.")
    cli.add_argument("--readme", type=Path, default=README)
    cli.add_argument("--bench", type=Path, default=BENCH)
    args = cli.parse_args(argv)

    parser = build_parser()
    flags, commands = walk(parser)
    flags |= {"--help", "--version"}
    commands |= REMOVED_COMMANDS | {"--help", "--version"}

    pages = sorted(GUIDE.glob("*.md"))
    if not pages:
        print(f"FAIL no guide pages found under {GUIDE}")
        return 1

    problems = readme_global_flag_problems(args.readme, parser)
    problems += readme_guide_link_problems(args.readme, pages)
    problems += readme_random_id_problems(args.readme)
    problems += readme_clone_retry_problems(args.readme)
    problems += benchmark_claim_problems(args.readme, args.bench, parser)
    for page in pages:
        text = page.read_text()

        for flag in sorted(set(re.findall(r"--[a-z][a-z0-9-]*", text))):
            if flag not in flags:
                problems.append(f"{page.name}: unknown flag {flag}")

        # drop value-taking flags so their argument is not read as a command
        stripped = re.sub(
            r"\s+",
            " ",
            re.sub(
                r"--(?:account|timeout|session-role|cursor|peer|drop-peer|wait) \S+",
                "",
                text,
            ),
        )
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
