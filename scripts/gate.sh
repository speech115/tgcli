#!/usr/bin/env bash
# The full pre-commit gate — the same steps CI runs, in the same order.
# AGENTS.md: run this before every commit; quote real output in PRs.
set -euo pipefail
cd "$(dirname "$0")/.."

run() {
  echo "==> $*"
  "$@"
}

run uv lock --check
run uv run ruff check .
run uv run ruff format --check .
run uv run python scripts/check-architecture.py
run uv run pyright
run uv run pytest -q -n auto
run uv run python scripts/check-coverage.py
run uv run python scripts/check-docs.py

echo "gate: all checks passed"
