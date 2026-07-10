import json
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY

import pytest

from tgcli.errors import PolicyError
from tgcli import safety


@pytest.mark.parametrize(
    ("readonly", "environment"),
    [
        (True, {}),
        (False, {"TGCLI_READONLY": "1"}),
        (False, {"TGCLI_NO_SEND": "1"}),
    ],
)
def test_mutation_kill_switches_raise_policy_error(monkeypatch, readonly, environment):
    for key, value in environment.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(PolicyError):
        safety.enforce_mutation_allowed(readonly)


def test_preview_expires_after_five_minutes_and_is_single_use():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    preview = safety.create_preview({"chat": "@alice", "text": "hello"}, now=now)

    assert preview["preview_id"].startswith("p_")
    assert preview["expires_at"] == "2026-07-10T12:05:00+00:00"
    assert safety.consume_preview(preview["preview_id"], now=now) == {
        "chat": "@alice",
        "text": "hello",
    }
    with pytest.raises(PolicyError, match="already used or does not exist"):
        safety.consume_preview(preview["preview_id"], now=now)


def test_expired_preview_is_blocked():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=UTC)
    preview = safety.create_preview({"chat": "@alice", "text": "hello"}, now=now)

    with pytest.raises(PolicyError, match="expired"):
        safety.consume_preview(preview["preview_id"], now=now + timedelta(seconds=301))


def test_audit_appends_one_json_object_per_line():
    safety.append_audit("send", "main", {"preview_id": "p_test"})
    safety.append_audit("api", "main", {"method": "messages.sendMessage"})

    lines = safety.audit_path().read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {
            "action": "send",
            "account": "main",
            "preview_id": "p_test",
            "timestamp": ANY,
        },
        {
            "action": "api",
            "account": "main",
            "method": "messages.sendMessage",
            "timestamp": ANY,
        },
    ]
