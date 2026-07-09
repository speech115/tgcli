"""Live smoke against a real account. Run explicitly:

    TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q

Requires a real ~/.config/tgcli/config.toml and an authorized session.
Read-only: lists dialogs, reads Saved Messages.
"""

import json
import os
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("TGCLI_LIVE_SMOKE") != "1",
    reason="live smoke is opt-in (TGCLI_LIVE_SMOKE=1)",
)


def run_tg(*argv):
    return subprocess.run(
        [sys.executable, "-m", "tgcli.cli", *argv],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_live_dialogs():
    result = run_tg("--json", "dialogs", "--limit", "5")
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["dialogs"]) > 0


def test_live_read_saved_messages():
    result = run_tg("--json", "read", "me", "--limit", "3")
    assert result.returncode == 0, result.stderr
    assert "messages" in json.loads(result.stdout)
