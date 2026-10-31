#!/usr/bin/env bash
# The full pre-commit gate — the same steps CI runs (there split into two
# parallel jobs, static checks and tests).
# AGENTS.md: run this before every commit; quote real output in PRs.
# `--static` skips the test suite: the pre-push hook (.githooks/pre-push).
set -euo pipefail
cd "$(dirname "$0")/.."

static_only=false
if [[ "${1:-}" == "--static" ]]; then
  static_only=true
fi

run() {
  echo "==> $*"
  "$@"
}

run uv lock --check
run uv run ruff check .
run uv run ruff format --check .
run uv run python scripts/check-architecture.py
run uv run pyright
run uv run python scripts/check-coverage.py
run uv run python scripts/check-docs.py
if $static_only; then
  echo "gate: static checks passed"
  exit 0
fi
# Hostile shell values on purpose: a test must set whatever tgcli environment
# it needs, so a developer's exported TGCLI_READONLY never turns the gate red.
run env TGCLI_READONLY=1 TGCLI_ACCOUNT=gate-isolation uv run pytest -q -n auto --ignore=tests/live
# Live smoke runs against the real account in the caller's own environment, so
# an opt-in TGCLI_LIVE_WRITES=1 draft round-trip is not forced readonly. It is
# skipped unless TGCLI_LIVE_SMOKE=1.
run uv run pytest -q tests/live

echo "gate: all checks passed"
