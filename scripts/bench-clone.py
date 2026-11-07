"""Benchmark `tg clone sync` on a fixed offline channel.

Runs every scenario in `tests/fakes/clone_bench.py` through the real CLI
against a fake Telegram, prints the report as JSON, and compares everything
but wall time with the committed baseline (`tests/fakes/clone_bench_baseline.json`).

    uv run python scripts/bench-clone.py             # report, exit 1 on drift
    uv run python scripts/bench-clone.py --repeat 5  # median wall time of 5
    uv run python scripts/bench-clone.py --update    # accept the new numbers

A change that moves a request count, a state write, a sync counter, or a
copy rewrites the baseline with `--update` in the same PR, so the diff shows
what moved. `tests/test_clone_benchmark.py` holds the gate to it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.fakes import clone_bench  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--update", action="store_true", help="rewrite the baseline")
    parser.add_argument(
        "--repeat", type=int, default=1, help="runs per scenario; wall time is a median"
    )
    args = parser.parse_args()

    reports = []
    for scenario in clone_bench.SCENARIOS:
        runs = [clone_bench.run(scenario) for _ in range(max(args.repeat, 1))]
        report = runs[0]
        for index, row in enumerate(report["phases"]):
            row["seconds"] = round(
                statistics.median(item["phases"][index]["seconds"] for item in runs), 4
            )
        reports.append(report)
    print(json.dumps(reports, indent=2))

    current = [clone_bench.deterministic(item) for item in reports]
    if args.update:
        clone_bench.BASELINE.write_text(json.dumps(current, indent=2) + "\n")
        print(
            f"baseline written: {clone_bench.BASELINE.relative_to(ROOT)}",
            file=sys.stderr,
        )
        return 0
    drift = clone_bench.drift(clone_bench.load_baseline(), current)
    for line in drift:
        print(line, file=sys.stderr)
    if drift:
        print(
            "bench-clone: differs from the baseline; if intended, rerun with --update",
            file=sys.stderr,
        )
        return 1
    print("bench-clone: matches the baseline", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
