"""Reproduction check: do the tests this branch changed fail on BASE's code?

Copy the working tree to a scratch directory, put BASE's `src/` back, and run
only the test files that differ from BASE. A test that fails there pins the
change: it is the reproducing test of a bug fix, or the first test of a new
behavior. A test that passes on BASE would not have caught the bug. A test
that errors (an import of a name BASE lacks, say) shows the API is new, not
that the behavior was wrong.

    uv run python scripts/repro.py              # tests changed vs origin/main
    uv run python scripts/repro.py --base HEAD~1

Exit 0 when at least one changed test fails on BASE, 1 when none does, 2 when
the branch changes no test.
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*argv: str) -> bytes:
    return subprocess.run(
        ["git", *argv], cwd=ROOT, check=True, capture_output=True
    ).stdout


def changed_tests(base: str) -> list[str]:
    """Test modules added or modified since BASE, untracked ones included."""
    names = git("diff", "-z", "--name-only", "--diff-filter=AM", base, "--", "tests")
    names += git("ls-files", "-z", "--others", "--exclude-standard", "--", "tests")
    return sorted(
        name
        for name in set(filter(None, names.decode().split("\0")))
        if Path(name).name.startswith("test_")
        and name.endswith(".py")
        and not name.startswith("tests/live/")
        and (ROOT / name).is_file()
    )


def scratch_tree(base: str, tree: Path) -> None:
    """The working tree, with `src/` as it is at BASE."""
    listing = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    for name in filter(None, listing.decode().split("\0")):
        if (ROOT / name).is_file() and not name.startswith("src/"):
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, tree / name)
    archive = git("archive", "--format=tar", base, "src")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(tree, filter="data")
    (tree / "home").mkdir()


def run_tests(tree: Path, tests: list[str], timeout: float) -> dict[str, str]:
    """Outcome per test id: `passed`, `failed`, `error`, or `skipped`."""
    report = tree / "report.xml"
    # An absolute PYTHONPATH, or the editable install shadows BASE's src.
    env = {k: v for k, v in os.environ.items() if not k.startswith("TGCLI_")}
    env.update(PYTHONPATH=str(tree / "src"), HOME=str(tree / "home"))
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        + [f"--junitxml={report}", *tests],
        cwd=tree,
        env=env,
        capture_output=True,
        timeout=timeout,
    )
    outcomes = {}
    for case in ET.parse(report).iter("testcase"):
        name = f"{case.get('classname')}::{case.get('name')}"
        tags = {child.tag for child in case}
        outcome = "passed"
        for tag in ("failure", "error", "skipped"):
            if tag in tags:
                outcome = "failed" if tag == "failure" else tag
                break
        outcomes[name] = outcome
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()

    tests = changed_tests(args.base)
    if not tests:
        print(f"no test changed since {args.base}")
        return 2
    with tempfile.TemporaryDirectory(prefix="tgcli-repro-") as scratch:
        tree = Path(scratch)
        scratch_tree(args.base, tree)
        outcomes = run_tests(tree, tests, args.timeout)
    for name, outcome in sorted(outcomes.items()):
        if outcome != "passed":
            label = "FAILS ON BASE" if outcome == "failed" else f"{outcome} on base"
            print(f"{label}\t{name}")
    failing = sum(outcome == "failed" for outcome in outcomes.values())
    print(f"{failing} of {len(outcomes)} changed tests fail on {args.base}")
    return 0 if failing else 1


if __name__ == "__main__":
    sys.exit(main())
