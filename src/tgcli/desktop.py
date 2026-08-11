"""Platform escape hatch for native dialogs and notifications (ADR-0042/0087).

Nothing else in the project may call `osascript`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

from tgcli.errors import PolicyError


def dialog_available() -> bool:
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def ask_secret(title: str, prompt: str, *, hidden: bool) -> str:
    """Collect a secret without putting it on argv.

    Tries a native `osascript` dialog on macOS when available; otherwise reads
    one line from stdin. The secret never appears in any subprocess argument.
    """
    if dialog_available():
        return _ask_osascript(title, prompt, hidden=hidden)
    return sys.stdin.readline().rstrip("\n")


def _ask_osascript(title: str, prompt: str, *, hidden: bool) -> str:
    # Escape for AppleScript string literals.
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    hidden_clause = " with hidden answer" if hidden else ""
    script = (
        f'display dialog "{esc(prompt)}" with title "{esc(title)}" '
        f'default answer ""{hidden_clause}'
    )
    # Answer arrives on stdout; the secret is never an argv element of ours
    # beyond the AppleScript source that only contains the prompt text.
    result = subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PolicyError("secret dialog cancelled or unavailable")
    # osascript prints "button returned:OK, text returned:<value>"
    text = result.stdout
    marker = "text returned:"
    if marker in text:
        value = text.split(marker, 1)[1]
        return value.rstrip("\r\n")
    return text.rstrip("\r\n")


def notify(title: str, message: str) -> bool:
    """Show one best-effort macOS notification; return whether it was sent."""
    if not dialog_available():
        return False

    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return result.returncode == 0
