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
        [
            sys.executable,
            "-c",
            "from tgcli.cli import entrypoint; entrypoint()",
            "--account",
            "main",
            *argv,
        ],
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


def test_live_info_saved_messages_has_json_shape():
    result = run_tg("--json", "info", "me")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data) == {"id", "name", "kind", "username"}
    assert isinstance(data["id"], int)
    assert isinstance(data["name"], str)
    assert data["kind"] in {"user", "group", "channel"}
    assert data["username"] is None or isinstance(data["username"], str)


def test_live_latest_saved_messages_has_json_shape():
    result = run_tg("--json", "latest", "me")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data) == {"dialog", "message"}
    assert_message_shape(data["message"])


def test_live_count_saved_messages_has_json_shape():
    result = run_tg("--json", "count", "me")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data) == {"dialog", "count"}
    assert_dialog_shape(data["dialog"])
    assert isinstance(data["count"], int)
    assert data["count"] >= 0


def test_live_bounded_search_saved_messages_has_json_shape():
    result = run_tg("--json", "search", "me", "tgcli-live-smoke", "--limit", "1")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert set(data) == {"dialog", "query", "messages"}
    assert_dialog_shape(data["dialog"])
    assert isinstance(data["query"], str)
    assert isinstance(data["messages"], list)
    assert len(data["messages"]) <= 1
    for message in data["messages"]:
        assert_message_shape(message)


def assert_dialog_shape(dialog):
    assert set(dialog) == {"id", "name"}
    assert isinstance(dialog["id"], int)
    assert isinstance(dialog["name"], str)


def assert_message_shape(message):
    assert set(message) == {"id", "date", "from", "text", "media", "reply_to"}
    assert isinstance(message["id"], int)
    assert message["date"] is None or isinstance(message["date"], str)
    assert set(message["from"]) == {"id", "name"}
    assert message["from"]["id"] is None or isinstance(message["from"]["id"], int)
    assert message["from"]["name"] is None or isinstance(message["from"]["name"], str)
    assert isinstance(message["text"], str)
    assert message["media"] is None or isinstance(message["media"], str)
    assert message["reply_to"] is None or isinstance(message["reply_to"], int)
