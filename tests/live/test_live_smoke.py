"""Live smoke against a real account. Run explicitly:

    TGCLI_LIVE_SMOKE=1 .venv/bin/pytest tests/live -q

Requires a real ~/.config/tgcli/config.toml and an authorized session.
`TGCLI_LIVE_ACCOUNT` picks the alias (default `main`). Every call runs under
`TGCLI_READONLY=1`; the draft round-trip writes to Saved Messages and runs only
with `TGCLI_LIVE_WRITES=1` as well.
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
WRITES = os.environ.get("TGCLI_LIVE_WRITES") == "1"


def run_tg(*argv):
    environment = os.environ.copy()
    environment.pop("TGCLI_STATE_DIR", None)
    if not WRITES:
        environment["TGCLI_READONLY"] = "1"
    return subprocess.run(
        [
            Path(sys.executable).with_name("tg"),
            "--account",
            os.environ.get("TGCLI_LIVE_ACCOUNT", "main"),
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
    assert WRITES or environment["TGCLI_READONLY"] == "1"


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


@pytest.mark.skipif(not WRITES, reason="writes a draft (TGCLI_LIVE_WRITES=1)")
def test_live_draft_set_show_clear_idempotent():
    """ADR-0039 live gate: markdown draft, reply header, no-op set/clear.

    Uses Saved Messages. Leaves the draft empty on success.
    """
    latest = run_tg("--json", "latest", "me")
    assert latest.returncode == 0, latest.stderr
    reply_to = json.loads(latest.stdout)["message"]["id"]
    assert isinstance(reply_to, int)

    marker = "tgcli-draft-live **bold**"

    preview = run_tg(
        "--json",
        "draft",
        "set",
        "me",
        marker,
        "--reply-to",
        str(reply_to),
        "--preview",
    )
    assert preview.returncode == 0, preview.stderr
    preview_id = json.loads(preview.stdout)["preview_id"]

    commit = run_tg("--json", "draft", "set", "--commit", preview_id)
    assert commit.returncode == 0, commit.stderr
    draft = json.loads(commit.stdout)["draft"]
    assert draft["is_empty"] is False
    assert draft["text"] == "tgcli-draft-live bold"
    assert "**" not in draft["text"]
    assert draft["reply_to_msg_id"] == reply_to
    assert draft["date"] is not None

    show = run_tg("--json", "draft", "show", "me")
    assert show.returncode == 0, show.stderr
    shown = json.loads(show.stdout)["draft"]
    assert shown["is_empty"] is False
    assert shown["text"] == "tgcli-draft-live bold"
    assert "**" not in shown["text"]
    assert shown["reply_to_msg_id"] == reply_to

    # Repeated identical set must not fail (Telegram *NotModified class).
    again_preview = run_tg(
        "--json",
        "draft",
        "set",
        "me",
        marker,
        "--reply-to",
        str(reply_to),
        "--preview",
    )
    assert again_preview.returncode == 0, again_preview.stderr
    again = run_tg(
        "--json",
        "draft",
        "set",
        "--commit",
        json.loads(again_preview.stdout)["preview_id"],
    )
    assert again.returncode == 0, again.stderr

    clear_preview = run_tg("--json", "draft", "clear", "me", "--preview")
    assert clear_preview.returncode == 0, clear_preview.stderr
    cleared = run_tg(
        "--json",
        "draft",
        "clear",
        "--commit",
        json.loads(clear_preview.stdout)["preview_id"],
    )
    assert cleared.returncode == 0, cleared.stderr
    assert json.loads(cleared.stdout)["draft"]["is_empty"] is True

    # Clear of already-empty draft must not fail.
    empty_preview = run_tg("--json", "draft", "clear", "me", "--preview")
    assert empty_preview.returncode == 0, empty_preview.stderr
    empty_clear = run_tg(
        "--json",
        "draft",
        "clear",
        "--commit",
        json.loads(empty_preview.stdout)["preview_id"],
    )
    assert empty_clear.returncode == 0, empty_clear.stderr


def assert_dialog_shape(dialog):
    assert {"id", "name"} <= dialog.keys()
    assert isinstance(dialog["id"], int)
    assert isinstance(dialog["name"], str)


def assert_message_shape(message):
    assert {
        "id",
        "date",
        "from",
        "text",
        "media",
        "reply_to",
        "quote_text",
    } <= message.keys()
    assert isinstance(message["id"], int)
    assert message["date"] is None or isinstance(message["date"], str)
    assert {"id", "name"} <= message["from"].keys()
    assert message["from"]["id"] is None or isinstance(message["from"]["id"], int)
    assert message["from"]["name"] is None or isinstance(message["from"]["name"], str)
    assert isinstance(message["text"], str)
    assert message["media"] is None or isinstance(message["media"], str)
    reply_to = message["reply_to"]
    assert (
        reply_to is None
        or isinstance(reply_to, int)
        or (
            isinstance(reply_to, dict)
            and set(reply_to) == {"id", "peer"}
            and isinstance(reply_to["id"], int)
            and isinstance(reply_to["peer"], int)
        )
    )
    assert message["quote_text"] is None or isinstance(message["quote_text"], str)
