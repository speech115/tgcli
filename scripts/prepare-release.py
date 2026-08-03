#!/usr/bin/env python3
"""Prepare the release bookkeeping ADR-0038 requires, so the integrator writes prose.

Run at merge time, never on a feature branch (AGENTS.md: feature branches never
touch the version files, `CHANGELOG.md`, or tags). The script moves the version
in both files, opens the `CHANGELOG.md` section with the date, lists the ADRs
and PRs the slice landed, and adds the `[x.y.z]:` compare link the docs gate
refuses a release without. What it cannot do is say what the release means to
an operator: it leaves a marked line for that, and the integrator replaces it.
"""

import argparse
import re
import subprocess
import sys
from datetime import date as date_type
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tgcli.atomic import replace_text  # noqa: E402

REPO_URL = "https://github.com/speech115/tgcli"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PR_SUBJECT_RE = re.compile(r"^(?P<title>.+?) \(#(?P<number>\d+)\)$")
ADR_PATH_RE = re.compile(r"^docs/decisions/(ADR-\d{4})-[a-z0-9-]+\.md$")
SECTION_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
PROJECT_VERSION_RE = re.compile(
    r"^\[project\]$.*?^version = \"(.+?)\"$", re.MULTILINE | re.DOTALL
)
MODULE_VERSION_RE = re.compile(r'^__version__ = "(.+?)"$', re.MULTILINE)
LINK_RE = re.compile(r"^\[(\d+\.\d+\.\d+)\]: ", re.MULTILINE)
PLACEHOLDER = (
    "<!-- prepare-release: replace this line with what the release means to an "
    "operator, then delete the marker. -->"
)


def parse_version(version: str) -> tuple[int, int, int]:
    match = VERSION_RE.match(version)
    if not match:
        raise ValueError(f"not a semver version: {version}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def next_patch(version: str) -> str:
    major, minor, patch = parse_version(version)
    return f"{major}.{minor}.{patch + 1}"


def merged_prs(subjects: list[str]) -> list[tuple[int, str]]:
    """Squash merges end in `(#N)`; anything else never became a PR."""
    found = []
    for subject in subjects:
        match = PR_SUBJECT_RE.match(subject.strip())
        if match:
            found.append((int(match.group("number")), match.group("title")))
    return found


def added_adrs(paths: list[str]) -> list[str]:
    return sorted({m.group(1) for p in paths if (m := ADR_PATH_RE.match(p.strip()))})


def git(repo_root: Path, *args: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def collect_slice(
    repo_root: Path, previous: str
) -> tuple[list[tuple[int, str]], list[str]]:
    """PRs and ADRs landed since the previous release tag, best effort.

    A missing tag or a non-git directory yields empty lists: the section is
    then a skeleton the integrator fills, which is still better than nothing.
    """
    span = f"v{previous}..HEAD"
    if not git(repo_root, "rev-parse", "--verify", f"v{previous}^{{commit}}"):
        print(
            f"prepare-release: no tag v{previous} here — the PR/ADR list will be "
            "empty and the compare link will not resolve until it exists",
            file=sys.stderr,
        )
        return [], []
    subjects = git(repo_root, "log", "--pretty=%s", span)
    paths = git(repo_root, "diff", "--name-only", "--diff-filter=A", span)
    return merged_prs(subjects), added_adrs(paths)


def render_section(
    version: str,
    date: str,
    prs: list[tuple[int, str]],
    adrs: list[str],
) -> str:
    lines = [f"## [{version}] — {date}", "", PLACEHOLDER, ""]
    if adrs:
        lines += [f"Rationale: {', '.join(adrs)}.", ""]
    lines += ["### Changed", ""]
    if prs:
        lines += [f"- {title} (#{number})" for number, title in prs]
    else:
        lines.append("- ")
    lines.append("")
    return "\n".join(lines)


def render_link(version: str, previous: str) -> str:
    return f"[{version}]: {REPO_URL}/compare/v{previous}...v{version}"


def read_version(pyproject: Path) -> str:
    """The `[project]` table's version — never another table's identical line."""
    match = PROJECT_VERSION_RE.search(pyproject.read_text())
    if not match:
        raise ValueError(f"no [project] version in {pyproject}")
    return match.group(1)


def read_module_version(init: Path) -> str:
    match = MODULE_VERSION_RE.search(init.read_text())
    if not match:
        raise ValueError(f"no __version__ in {init}")
    return match.group(1)


def insert_section(changelog: str, section: str, link: str) -> str:
    first_section = SECTION_RE.search(changelog)
    if not first_section:
        raise ValueError("no release section to insert above")
    at = first_section.start()
    changelog = changelog[:at] + section + "\n" + changelog[at:]

    first_link = LINK_RE.search(changelog)
    if not first_link:
        raise ValueError("no compare-link block to extend")
    at = first_link.start()
    return changelog[:at] + link + "\n" + changelog[at:]


def bump(path: Path, pattern: re.Pattern[str], version: str) -> None:
    text = path.read_text()
    match = pattern.search(text)
    if not match:
        raise ValueError(f"no version line in {path}")
    replaced = text[: match.start(1)] + version + text[match.end(1) :]
    replace_text(path, replaced)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the next release section")
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--version", help="explicit version; default bumps the patch digit"
    )
    parser.add_argument("--date", help="release date; default today")
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = parser.parse_args(argv)

    root: Path = args.repo_root
    pyproject = root / "pyproject.toml"
    init = root / "src" / "tgcli" / "__init__.py"
    changelog_path = root / "CHANGELOG.md"

    previous = read_version(pyproject)
    module_version = read_module_version(init)
    if module_version != previous:
        print(
            f"prepare-release: {pyproject.name} says {previous} but "
            f"{init.name} says {module_version} — reconcile them by hand first; "
            "an interrupted earlier run leaves exactly this state",
            file=sys.stderr,
        )
        return 1
    try:
        version = args.version or next_patch(previous)
        parse_version(version)
    except ValueError as error:
        print(f"prepare-release: {error}", file=sys.stderr)
        return 1

    if parse_version(version) <= parse_version(previous):
        print(
            f"prepare-release: {version} does not move past the current {previous}",
            file=sys.stderr,
        )
        return 1

    changelog = changelog_path.read_text()
    if f"## [{version}]" in changelog:
        print(f"prepare-release: {version} already has a section", file=sys.stderr)
        return 1

    prs, adrs = collect_slice(root, previous)
    section = render_section(
        version=version,
        date=args.date or date_type.today().isoformat(),
        prs=prs,
        adrs=adrs,
    )
    link = render_link(version, previous)

    if args.dry_run:
        print(section)
        print(link)
        return 0

    bump(pyproject, PROJECT_VERSION_RE, version)
    bump(init, MODULE_VERSION_RE, version)
    replace_text(changelog_path, insert_section(changelog, section, link))

    print(f"prepare-release: {previous} → {version}; {len(prs)} PRs, {len(adrs)} ADRs")
    print("prepare-release: replace the marker line in CHANGELOG.md before merging")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
