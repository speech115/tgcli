"""Grammar and pre-session checks owned by parser.py and preflight.py.

`--confirm` and `--write` are only gates if they have to be typed in full, and
a `--params` typo is a purely local mistake that must never cost a session
open. Both are decided before anything reaches the network.
"""

import json
from contextlib import asynccontextmanager

import pytest

from tgcli import session
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


@pytest.fixture
def opened_sessions(monkeypatch):
    """Record every session open and refuse it, so network work is visible."""
    opened = []

    @asynccontextmanager
    async def refuse(account, *, mutation_safe=False, role=None):
        opened.append(account)
        raise AssertionError("a session was opened before the local checks passed")
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(session, "client", refuse)
    return opened


def test_store_cleanup_confirm_must_be_spelled_in_full(config_env, capsys):
    assert main(["store", "cleanup", "--c", "--json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments: --c" in captured.err


@pytest.mark.parametrize(
    ("argv", "diagnostic"),
    [
        # Rejected by the top-level parser.
        (["dialogs", "+79991234567"], "unrecognized arguments: +7"),
        # Rejected by a subparser.
        (["media", "+79991234567"], "invalid choice: '+7"),
        # Rejected by a type converter.
        (["dialogs", "--limit", "+79991234567x"], "invalid int value: '+7"),
    ],
)
def test_parse_errors_mask_phones_on_stderr(config_env, capsys, argv, diagnostic):
    """CONTRACT §10 masks phones in every error, but argparse wrote its own
    diagnostics straight to stderr, so `tg dialogs +79991234567` leaked the
    number into agent transcripts."""
    assert main(argv) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert diagnostic in captured.err
    assert "usage: tg" in captured.err
    assert "79991234567" not in captured.err


def test_store_cleanup_confirm_still_deletes_when_spelled_in_full(
    config_env, capsys, tmp_path
):
    from tgcli.session import state_dir

    spent = state_dir() / "previews" / "p_spent.used"
    spent.parent.mkdir(parents=True)
    spent.write_text("{}")

    assert main(["store", "cleanup", "--confirm", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["confirmed"] is True
    assert payload["removed"] == ["p_spent.used"]
    assert not spent.exists()
