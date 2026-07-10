import json
import sqlite3

import pytest

from tgcli.cli import main


def make_old_stack(root, aliases=("main", "pl"), api_id=12345, api_hash="hash-abc"):
    for alias in aliases:
        directory = root / (
            ".telegram-mcp" if alias == "main" else f".telegram-mcp-{alias}"
        )
        directory.mkdir(parents=True)
        connection = sqlite3.connect(directory / "session.session")
        connection.execute("CREATE TABLE sessions (auth_key BLOB)")
        connection.execute("INSERT INTO sessions VALUES (?)", (alias.encode(),))
        connection.commit()
        connection.close()
        (directory / "launchd.env").write_text(
            f"TELEGRAM_API_ID={api_id}\nexport TELEGRAM_API_HASH={api_hash}\n"
        )


@pytest.fixture
def import_env(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(
        'default_account = "main"\n[accounts.main]\n'
        'api_id = 1\napi_hash = "existing"\n'
    )
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    old_root = tmp_path / "oldhome"
    make_old_stack(old_root)
    return tmp_path, old_root, config


def test_import_copies_sessions_and_appends_config(import_env, capsys):
    tmp_path, old_root, config = import_env

    code = main(["--json", "accounts", "import", "--source-root", str(old_root)])

    assert code == 0
    report = {entry["alias"]: entry for entry in json.loads(capsys.readouterr().out)["imported"]}
    assert report["pl"]["status"] == "imported"
    assert report["pl"]["config"] == "added"
    copied = sqlite3.connect(tmp_path / "state" / "sessions" / "pl.session")
    assert copied.execute("SELECT auth_key FROM sessions").fetchone() == (b"pl",)
    assert "[accounts.pl]" in config.read_text()
    assert 'api_hash = "existing"' in config.read_text()


def test_import_skips_existing_session_without_force(import_env, capsys):
    tmp_path, old_root, _ = import_env
    destination = tmp_path / "state" / "sessions"
    destination.mkdir(parents=True)
    (destination / "main.session").write_bytes(b"warm")

    code = main(["--json", "accounts", "import", "--source-root", str(old_root)])

    assert code == 0
    report = {entry["alias"]: entry for entry in json.loads(capsys.readouterr().out)["imported"]}
    assert report["main"]["status"] == "skipped_existing"
    assert (destination / "main.session").read_bytes() == b"warm"


def test_import_default_tolerates_missing_sources(import_env, capsys):
    _, old_root, _ = import_env

    code = main(["--json", "accounts", "import", "--source-root", str(old_root)])

    assert code == 0
    captured = capsys.readouterr()
    report = {entry["alias"]: entry for entry in json.loads(captured.out)["imported"]}
    assert report["recklessou"]["status"] == "source_missing"
    assert "recklessou" in captured.err


def test_import_explicit_missing_alias_exits_4(import_env):
    _, old_root, _ = import_env

    code = main(["--json", "accounts", "import", "ghost", "--source-root", str(old_root)])

    assert code == 4


def test_import_missing_credentials_for_new_alias_exits_3(import_env):
    _, old_root, _ = import_env
    (old_root / ".telegram-mcp-pl" / "launchd.env").unlink()

    code = main(["--json", "accounts", "import", "pl", "--source-root", str(old_root)])

    assert code == 3
