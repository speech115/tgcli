"""--limit bounds of the Telegram reads, refused in preflight before a session.

dialogs and search accept 0 (it asks only whether anything matches, through
`more`); every other read needs a positive --limit.
"""

import json

import pytest

from tgcli.cli import main

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


REFUSALS = [
    (["dialogs", "--limit", "-1"], "dialogs --limit must not be negative"),
    (["search", "@chan", "hi", "--limit", "-1"], "search --limit must not be negative"),
    (["search", "--all", "hi", "--limit", "-1"], "search --limit must not be negative"),
    (["read", "@chan", "--limit", "0"], "read --limit must be positive"),
    (["read", "@chan", "--limit", "-1"], "read --limit must be positive"),
    (["thread", "@chan", "7", "--limit", "0"], "thread --limit must be positive"),
    (
        ["media", "manifest", "@chan", "--limit", "0"],
        "media manifest --limit must be positive",
    ),
    (
        ["export", "messages", "@chan", "--output", "out.jsonl", "--limit", "0"],
        "export messages --limit must be positive",
    ),
    (
        ["export", "subscribers", "@chan", "--output", "out.csv", "--limit", "-1"],
        "export subscribers --limit must be positive",
    ),
]


@pytest.mark.parametrize(
    ("argv", "message"), REFUSALS, ids=[" ".join(a) for a, _ in REFUSALS]
)
def test_read_limit_refusals_exit_2(config_env, capsys, argv, message):
    assert main([*argv, "--json"]) == 2
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["code"] == "BLOCKED"
    assert error["message"] == message
