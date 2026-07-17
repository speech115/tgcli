"""Live smoke against a real account. Run explicitly:

    TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q

Requires a real ~/.config/tgcli/config.toml and an authorized session.
Read-only: lists dialogs, reads Saved Messages.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("TGCLI_LIVE_SMOKE") != "1",
    reason="live smoke is opt-in (TGCLI_LIVE_SMOKE=1)",
)


def run_tg(*argv):
    environment = os.environ.copy()
    environment.pop("TGCLI_STATE_DIR", None)
    return subprocess.run(
        [
            Path(sys.executable).with_name("tg"),
            "--account",
            "main",
            *argv,
        ],
        capture_output=True,
        env=environment,
        text=True,
        timeout=120,
    )


def test_live_harness_uses_console_script(monkeypatch):
    command = None
    environment = None

    def capture_run(argv, **kwargs):
        nonlocal command, environment
        command = argv
        environment = kwargs.get("env", os.environ)

    monkeypatch.setattr(subprocess, "run", capture_run)

    run_tg("--json", "info", "me")

    assert command[0] == Path(sys.executable).with_name("tg")
    assert "TGCLI_STATE_DIR" not in environment


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
    assert {"id", "name", "kind", "username"} <= data.keys()
    assert isinstance(data["id"], int)
    assert isinstance(data["name"], str)
    assert data["kind"] in {"user", "group", "channel"}
    assert data["username"] is None or isinstance(data["username"], str)


def test_live_latest_saved_messages_has_json_shape():
    result = run_tg("--json", "latest", "me")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert {"dialog", "message"} <= data.keys()
    assert_message_shape(data["message"])


def test_live_count_saved_messages_has_json_shape():
    result = run_tg("--json", "count", "me")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert {"dialog", "count"} <= data.keys()
    assert_dialog_shape(data["dialog"])
    assert isinstance(data["count"], int)
    assert data["count"] >= 0


def test_live_bounded_search_saved_messages_has_json_shape():
    result = run_tg("--json", "search", "me", "tgcli-live-smoke", "--limit", "1")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert {"dialog", "query", "messages"} <= data.keys()
    assert_dialog_shape(data["dialog"])
    assert isinstance(data["query"], str)
    assert isinstance(data["messages"], list)
    assert len(data["messages"]) <= 1
    for message in data["messages"]:
        assert_message_shape(message)


def test_live_raw_api_get_full_user_has_envelope():
    result = run_tg("--json", "api", "users.getFullUser", "--params", '{"id":"@self"}')
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["method"] == "users.getFullUser"
    assert isinstance(data["result"], dict)


def assert_dialog_shape(dialog):
    assert {"id", "name"} <= dialog.keys()
    assert isinstance(dialog["id"], int)
    assert isinstance(dialog["name"], str)


def assert_message_shape(message):
    assert {"id", "date", "from", "text", "media", "reply_to"} <= message.keys()
    assert isinstance(message["id"], int)
    assert message["date"] is None or isinstance(message["date"], str)
    assert {"id", "name"} <= message["from"].keys()
    assert message["from"]["id"] is None or isinstance(message["from"]["id"], int)
    assert message["from"]["name"] is None or isinstance(message["from"]["name"], str)
    assert isinstance(message["text"], str)
    assert message["media"] is None or isinstance(message["media"], str)
    assert message["reply_to"] is None or isinstance(message["reply_to"], int)
