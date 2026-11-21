#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

static_only=false
case "${1:-}" in
  "") ;;
  --static) static_only=true ;;
  *) echo "usage: scripts/gate.sh [--static]" >&2; exit 2 ;;
esac

run() {
  echo "==> $*"
  "$@"
}

run uv lock --check
run uv run ruff check .
run uv run ruff format --check .
run uv run python scripts/check-bans.py
run uv run python scripts/check-comments.py
run uv run pyright
if $static_only; then
  echo "gate: static checks passed"
  exit 0
fi
run env TGCLI_READONLY=1 TGCLI_ACCOUNT=gate-isolation uv run pytest -q -n auto --ignore=tests/live
run uv run pytest -q tests/live

echo "gate: all checks passed"
