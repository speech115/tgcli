#!/usr/bin/env python3
"""Fail-closed gate for active docs and release bookkeeping.

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

ADR-0065 extends the same fail-closed posture to active summaries that the
guide-only gate could not see:

  5. README names exactly the root global flags and links every task guide page;
  6. README scopes ``random_id`` confirmation to send/forward and requires a
     fresh preview after a failed ``clone init`` / ``clone refresh`` commit;
  7. any exhaustive benchmark claim requires actual parser-wide coverage;
  8. MAP guide/ADR inventory matches the tree;
  9. contributor workflow docs route session entries to ``docs/devlog/``;
 10. every ADR in the tree has its row in the ``docs/decisions/README.md``
     index, which AGENTS.md requires in the same commit as the ADR;
 11. every ADR named by a ``Supersedes`` clause records that supersession in
     its own ``Status`` header, which AGENTS.md requires in the same commit.

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
CHANGELOG = REPO / "CHANGELOG.md"
README = REPO / "README.md"
PROJECT_MAP = REPO / "docs" / "MAP.md"
BENCH = REPO / "scripts" / "bench.py"
DECISIONS = REPO / "docs" / "decisions"
CONTRIBUTING = REPO / "CONTRIBUTING.md"
PR_TEMPLATE = REPO / ".github" / "PULL_REQUEST_TEMPLATE.md"

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
    """Clone previews are consumed before dispatch, so retries need a new one."""
    text = readme.read_text()
    unsafe = any(
        "same preview id" in line
        and ("`clone init`" in line or "`clone refresh`" in line)
        for line in text.splitlines()
    )
    documents_fresh_preview = re.search(
        r"`clone init`.*?`clone refresh`.*?fresh preview",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not unsafe and documents_fresh_preview is not None:
        return []
    return [f"{readme.name}: clone init/refresh retries must require a fresh preview"]


def benchmark_claim_problems(
    readme: Path,
    project_map: Path,
    bench: Path,
    parser: argparse.ArgumentParser,
) -> list[str]:
    """An exhaustive claim is allowed only when the harness is exhaustive."""
    exhaustive_claim = re.compile(
        r"\b(?:exhaustive|complete)\b[^\n.]*\bbenchmark\b|"
        r"\bbenchmark\b[^\n.]*(?:\bevery command\b|\ball commands\b)",
        re.IGNORECASE,
    )
    claims_every_command = any(
        exhaustive_claim.search(path.read_text()) for path in (readme, project_map)
    )
    if not claims_every_command:
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


def map_inventory_problems(project_map: Path, pages: list[Path]) -> list[str]:
    text = project_map.read_text()
    problems: list[str] = []

    guide_match = re.search(r"user-facing task pages, (\d+) \+ index", text)
    actual_guides = sum(page.name != "README.md" for page in pages)
    if guide_match is None:
        problems.append(f"{project_map.name}: guide inventory count is missing")
    elif int(guide_match.group(1)) != actual_guides:
        problems.append(
            f"{project_map.name}: guide count is {guide_match.group(1)}; "
            f"tree has {actual_guides} task pages"
        )

    adr_match = re.search(r"ADR-0001…(\d{4})", text)
    adr_numbers = [
        int(match.group(1))
        for path in DECISIONS.glob("ADR-*.md")
        if (match := re.match(r"ADR-(\d{4})-", path.name))
    ]
    actual_last = max(adr_numbers, default=0)
    if adr_match is None:
        problems.append(f"{project_map.name}: ADR range is missing")
    elif int(adr_match.group(1)) != actual_last:
        problems.append(
            f"{project_map.name}: ADR range ends at {adr_match.group(1)}; "
            f"tree ends at {actual_last:04d}"
        )
    return problems


def adr_index_problems(index: Path) -> list[str]:
    """Every ADR file has a row in the index (AGENTS.md, extending ADR-0007).

    The rule is unconditional and carries no status carve-out: the index has a
    Status column precisely so a not-yet-accepted ADR can be listed as one.
    Without this check the rule held only by memory, and AGENTS.md "Read First"
    sends every fresh session to the index before touching a governed area — an
    ADR missing from it is invisible to the next agent.
    """
    problems: list[str] = []
    try:
        text = index.read_text()
    except FileNotFoundError:
        return [f"{index.name}: ADR index is missing"]

    for path in sorted(DECISIONS.glob("ADR-*.md")):
        match = re.match(r"ADR-(\d{4})-", path.name)
        if match is None:
            continue
        if f"({path.name})" not in text:
            problems.append(f"{index.name}: no index row for ADR-{match.group(1)}")
    return problems


def adr_supersession_problems(decisions: Path | None = None) -> list[str]:
    """A superseded ADR records it in its own Status (AGENTS.md).

    The index row is not enough: AGENTS.md's "Read First" sends an agent to a
    specific ADR as often as to the index, and a file whose header still reads
    a bare `accepted` looks authoritative on its own. ADR-0008 and ADR-0026 set
    the precedent for recording partial supersession in the target's header.

    This is the sibling of `adr_index_problems`, and it was added for the same
    reason: the rule held only by memory, so ADR-0072 landed its supersessions
    in the index while ADR-0045's and ADR-0052's own headers stayed silent.
    """
    decisions = DECISIONS if decisions is None else decisions
    problems: list[str] = []
    for path in sorted(decisions.glob("ADR-*.md")):
        source = re.match(r"ADR-(\d{4})-", path.name)
        if source is None:
            continue
        preamble = path.read_text().split("\n## ", 1)[0]
        # Each "Supersedes" clause runs until the next one or the preamble end;
        # the first ADR id inside it is the target being superseded.
        for clause in preamble.split("Supersedes")[1:]:
            target = re.search(r"ADR-(\d{4})", clause)
            if target is None:
                problems.append(
                    f"ADR-{source.group(1)}: Supersedes clause names no ADR"
                )
                continue
            files = sorted(decisions.glob(f"ADR-{target.group(1)}-*.md"))
            if not files:
                problems.append(
                    f"ADR-{source.group(1)}: supersedes ADR-{target.group(1)}, "
                    "which has no ADR file"
                )
                continue
            header = files[0].read_text().split("\n## ", 1)[0]
            if f"ADR-{source.group(1)}" not in header:
                problems.append(
                    f"ADR-{target.group(1)}: Status does not record being "
                    f"superseded by ADR-{source.group(1)}"
                )
    return problems


def devlog_routing_problems(contributing: Path, pr_template: Path) -> list[str]:
    """ADR-0058 closed DEVLOG.md; active workflow docs must route to devlog/."""
    stale = re.compile(
        r"(?:entry|append)[^\n]*docs/DEVLOG\.md|"
        r"docs/DEVLOG\.md[^\n]*(?:entry|append)",
        re.IGNORECASE,
    )
    return [
        f"{path.name}: points session entries at closed docs/DEVLOG.md"
        for path in (contributing, pr_template)
        if stale.search(path.read_text())
    ]


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
    cli.add_argument("--readme", type=Path, default=README)
    cli.add_argument("--map", type=Path, default=PROJECT_MAP)
    cli.add_argument("--bench", type=Path, default=BENCH)
    cli.add_argument("--contributing", type=Path, default=CONTRIBUTING)
    cli.add_argument("--pr-template", type=Path, default=PR_TEMPLATE)
    cli.add_argument("--adr-index", type=Path, default=DECISIONS / "README.md")
    args = cli.parse_args(argv)

    if not args.changelog.is_file():
        print(f"FAIL no changelog at {args.changelog}")
        return 1

    required = (
        args.readme,
        args.map,
        args.bench,
        args.contributing,
        args.pr_template,
    )
    missing = next((path for path in required if not path.is_file()), None)
    if missing is not None:
        print(f"FAIL no active-document input at {missing}")
        return 1

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
    problems += benchmark_claim_problems(args.readme, args.map, args.bench, parser)
    problems += map_inventory_problems(args.map, pages)
    problems += devlog_routing_problems(args.contributing, args.pr_template)
    problems += adr_index_problems(args.adr_index)
    problems += adr_supersession_problems()
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
