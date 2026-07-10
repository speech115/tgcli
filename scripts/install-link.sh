#!/usr/bin/env bash
# Symlink the tgcli entrypoint onto PATH ahead of the old tools/telegram wrapper.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="${TGCLI_REPO:-$(dirname "$script_dir")}"
bin_dir="${TGCLI_BIN_DIR:-$HOME/.local/bin}"
entry="$repo/.venv/bin/tg"

if [[ ! -x "$entry" ]]; then
  echo "error: $entry not found or not executable; run 'uv sync' in $repo first" >&2
  exit 1
fi

mkdir -p "$bin_dir"
ln -sfn "$entry" "$bin_dir/tg"
echo "linked $bin_dir/tg -> $entry" >&2

resolved="$(command -v tg || true)"
if [[ -n "$resolved" && "$resolved" != "$bin_dir/tg" ]]; then
  echo "warning: 'tg' currently resolves to $resolved; ensure $bin_dir precedes it in PATH" >&2
fi
