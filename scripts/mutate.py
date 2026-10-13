"""Mutation check for the lines a branch changed: is each behavior pinned?

For every comparison and every `and`/`or` on a line of `src/` that differs
from BASE, flip it in a scratch copy of the tree, run the suite, and report
whether a test failed. A mutant that survives is a behavior no test pins, or
code that changes nothing. Too slow for the gate: run it before asking for
review.

    uv run python scripts/mutate.py              # lines changed vs origin/main
    uv run python scripts/mutate.py --base HEAD~3 --limit 20
    uv run python scripts/mutate.py --base 73945c3~1 src/tgcli/commands/send.py

Exit 0 when every mutant was killed, 1 when one survived, 2 when the
unmutated suite already fails in the scratch copy.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWAP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.GtE,
    ast.LtE: ast.Gt,
    ast.Gt: ast.LtE,
    ast.GtE: ast.Lt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


@dataclass(frozen=True)
class Mutant:
    path: str
    line: int
    before: str
    after: str
    source: str  # the whole file with this one change applied


def changed_lines(base: str, paths: list[str]) -> dict[str, set[int]]:
    """Lines under PATHS that differ from BASE, by repository-relative path."""
    diff = subprocess.run(
        ["git", "diff", "-U0", base, "--", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    lines: dict[str, set[int]] = {}
    path = None
    for row in diff.splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else None
        elif path and path.endswith(".py") and (match := HUNK.match(row)):
            start, count = int(match[1]), int(match[2] or 1)
            lines.setdefault(path, set()).update(range(start, start + count))
    return lines


def mutants(path: str, source: str, lines: set[int]) -> list[Mutant]:
    """One mutant per single-line comparison or `and`/`or` on LINES."""
    found = []
    rows = source.splitlines(keepends=True)
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            swap = SWAP.get(type(node.ops[0]))
            if swap is None:
                continue
            flipped: ast.expr = ast.Compare(node.left, [swap()], node.comparators)
        elif isinstance(node, ast.BoolOp):
            op = ast.And() if isinstance(node.op, ast.Or) else ast.Or()
            flipped = ast.BoolOp(op, node.values)
        else:
            continue
        if node.lineno not in lines or node.lineno != node.end_lineno:
            continue
        row = rows[node.lineno - 1]
        assert node.end_col_offset is not None
        before = row[node.col_offset : node.end_col_offset]
        after = f"({ast.unparse(flipped)})"
        rows[node.lineno - 1] = (
            row[: node.col_offset] + after + row[node.end_col_offset :]
        )
        found.append(Mutant(path, node.lineno, before, after, "".join(rows)))
        rows[node.lineno - 1] = row
    return sorted(found, key=lambda mutant: (mutant.line, mutant.before))


def run_suite(tree: Path, timeout: float) -> int:
    # An absolute PYTHONPATH, or the editable install shadows the copy and
    # every mutant "survives".
    env = {k: v for k, v in os.environ.items() if not k.startswith("TGCLI_")}
    env.update(PYTHONPATH=str(tree / "src"), HOME=str(tree / "home"))
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-n", "auto"]
            + ["-p", "no:cacheprovider", "tests", "--ignore=tests/live"],
            cwd=tree,
            env=env,
            capture_output=True,
            timeout=timeout,
        ).returncode
    except subprocess.TimeoutExpired:
        return -1  # a hang is a failure too


def killed(tree: Path, mutant: Mutant, timeout: float) -> bool:
    target = tree / mutant.path
    original = target.read_text()
    target.write_text(mutant.source)
    try:
        return run_suite(tree, timeout) != 0
    finally:
        target.write_text(original)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("paths", nargs="*", default=["src"])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()

    candidates = [
        mutant
        for path, lines in sorted(changed_lines(args.base, args.paths).items())
        if (ROOT / path).is_file()
        for mutant in mutants(path, (ROOT / path).read_text(), lines)
    ]
    if not candidates:
        print(f"no comparison or and/or on lines changed since {args.base}")
        return 0
    survived = 0
    with tempfile.TemporaryDirectory(prefix="tgcli-mutate-") as scratch:
        tree = Path(scratch)
        listing = ["git", "ls-files", "-z", "--cached", "--others"]
        tracked = subprocess.run(
            [*listing, "--exclude-standard"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout.decode()
        for name in filter(None, tracked.split("\0")):
            if (ROOT / name).is_file():
                (tree / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / name, tree / name)
        (tree / "home").mkdir()
        if run_suite(tree, args.timeout) != 0:
            print("the unmutated suite fails in the scratch copy; nothing to measure")
            return 2
        for mutant in candidates[: args.limit]:
            status = "killed" if killed(tree, mutant, args.timeout) else "SURVIVED"
            survived += status == "SURVIVED"
            where = f"{mutant.path}:{mutant.line}"
            print(f"{status}\t{where}\t{mutant.before} -> {mutant.after}", flush=True)
    skipped = len(candidates) - min(len(candidates), args.limit)
    tail = f", {skipped} not run (--limit)" if skipped else ""
    print(f"{survived} of {min(len(candidates), args.limit)} survived{tail}")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
