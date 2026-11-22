#!/bin/sh
guide=https://github.com/speech115/tgcli/blob/main/INSTALL_FOR_AGENTS.md
PATH="$HOME/.local/bin:$PATH"
if [ -d "$HOME/.local/share/uv/tools/tg-harness" ]; then
  echo "The retired speech115/tg (tg-harness) is installed and also provides a tg command. Replace it with tgcli by following $guide."
elif ! command -v tg >/dev/null 2>&1; then
  echo "tgcli, the tg command for Telegram, is not installed on this computer. To install it, follow $guide."
else
  config=${TGCLI_CONFIG:-$HOME/.config/tgcli/config.toml}
  set -- "${TGCLI_STATE_DIR:-$HOME/.local/state/tgcli}"/sessions/*.session
  if [ ! -f "$config" ] || [ ! -f "$1" ]; then
    echo "tgcli is installed, but no Telegram account is logged in. Run tg setup to connect one; it opens a page in the browser. Details: $guide"
  fi
fi
exit 0
