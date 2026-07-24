"""The CONTRACT §4 exit-code table as one executable source of truth.

Every command tests its own exits locally; this file pins the shared law so a
new command cannot silently redefine a code (the exit-3-vs-4 unknown-alias
drift caught in the ADR-0042 review is the motivating case).
"""

from __future__ import annotations

import json

import pytest

from tgcli.cli import main
from tgcli.errors import (
    ConfigError,
    NotFoundError,
    PolicyError,
    RateLimitError,
    TgcliError,
)

CONTRACT_TABLE = [
    (TgcliError, 1, "RUNTIME"),
    (PolicyError, 2, "BLOCKED"),
    (ConfigError, 3, "CONFIG"),
    (NotFoundError, 4, "NOT_FOUND"),
    (RateLimitError, 5, "FLOOD_WAIT"),
]


@pytest.mark.parametrize("exc_class, exit_code, code", CONTRACT_TABLE)
def test_error_classes_match_contract_table(exc_class, exit_code, code):
    assert exc_class.exit_code == exit_code
    assert exc_class.code == code


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "hash-main"
session = "main"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(SAMPLE)
    state = tmp_path / "state"
    (state / "sessions").mkdir(parents=True)
    monkeypatch.setenv("TGCLI_CONFIG", str(config_path))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    monkeypatch.delenv("TGCLI_READONLY", raising=False)
    monkeypatch.delenv("TGCLI_NO_SEND", raising=False)
    return {"config": config_path, "state": state}


def _error_code(capsys) -> str:
    return json.loads(capsys.readouterr().err)["error"]["code"]


def test_unknown_alias_lookup_is_exit_4(env, capsys):
    """`accounts show|remove` look up a registry entry (ADR-0042 §11)."""
    assert main(["accounts", "show", "ghost", "--json"]) == 4
    assert _error_code(capsys) == "NOT_FOUND"


def test_unknown_account_resolution_is_exit_3(env, capsys):
    """`--account` resolves the operating context (CONTRACT §4, doctor rule)."""
    assert main(["doctor", "--account", "ghost", "--json"]) == 3
    assert _error_code(capsys) == "CONFIG"


def test_readonly_mutation_is_exit_2(env, capsys):
    args = ["accounts", "remove", "main", "--confirm", "--readonly", "--json"]
    assert main(args) == 2
    assert _error_code(capsys) == "BLOCKED"
