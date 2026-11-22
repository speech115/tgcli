"""Platform escape hatch for native dialogs, notifications and the browser.

Nothing else in the project may call `osascript` or open a browser.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser

from tgcli.errors import PolicyError


def dialog_available() -> bool:
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def open_url(url: str) -> bool:
    """Open `url` in the person's default browser; False when none opened."""
    try:
        return webbrowser.open(url)
    except webbrowser.Error:
        return False


def ask_secret(title: str, prompt: str, *, hidden: bool) -> str:
    """Collect a secret without putting it on argv.

    Tries a native `osascript` dialog on macOS when available; otherwise reads
    one line from stdin. The secret never appears in any subprocess argument.
    """
    if dialog_available():
        return _ask_osascript(title, prompt, hidden=hidden)
    return sys.stdin.readline().rstrip("\n")


def _ask_osascript(title: str, prompt: str, *, hidden: bool) -> str:
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    hidden_clause = " with hidden answer" if hidden else ""
    script = (
        f'display dialog "{esc(prompt)}" with title "{esc(title)}" '
        f'default answer ""{hidden_clause}'
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PolicyError("secret dialog cancelled or unavailable")
    text = result.stdout
    marker = "text returned:"
    if marker in text:
        value = text.split(marker, 1)[1]
        return value.rstrip("\r\n")
    return text.rstrip("\r\n")
