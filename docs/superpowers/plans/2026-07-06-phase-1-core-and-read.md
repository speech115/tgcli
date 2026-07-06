# Phase 1: Core + First Reads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** working stateless `tg` CLI with `accounts list`, `dialogs`, `read` and the full output/exit-code contract.

**Architecture:** commands return plain dicts; only `cli.py` prints via `output.py`; `errors.py` owns exit codes; `session.py` owns locking and the Telethon client lifecycle. No daemons.

**Tech Stack:** Python 3.12, Telethon >=1.36, uv, pytest + pytest-asyncio, argparse (stdlib).

## Global Constraints

- Python `>=3.12` (tomllib in stdlib), package layout `src/tgcli/`.
- Runtime dependency: `telethon>=1.36` only. Dev: `pytest>=8`, `pytest-asyncio>=0.24`.
- stdout carries only contract data; all diagnostics to stderr (docs/CONTRACT.md §2).
- Exit codes exactly as CONTRACT.md §4: 0/1/2/3/4/5.
- State dir env override `TGCLI_STATE_DIR`, config override `TGCLI_CONFIG` (tests rely on these; never touch real `~/.config`).
- Every task ends with `pytest -q` green and a commit.

---

### Task 1: Scaffold + errors module

**Files:**
- Create: `pyproject.toml`, `src/tgcli/__init__.py`, `src/tgcli/errors.py`,
  `tests/__init__.py` (empty — makes `tests.conftest` importable in Tasks 6–7)
- Test: `tests/test_errors.py`

**Interfaces:**
- Produces: `TgcliError(message, **details)` with `.exit_code=1`, `.code="RUNTIME"`, `.details: dict`; subclasses `PolicyError` (2, `"BLOCKED"`), `ConfigError` (3, `"CONFIG"`), `NotFoundError` (4, `"NOT_FOUND"`), `RateLimitError` (5, `"FLOOD_WAIT"`). `tgcli.__version__: str`.

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "tgcli"
version = "0.1.0"
description = "Stateless Telegram CLI for humans, scripts, and AI agents"
requires-python = ">=3.12"
dependencies = ["telethon>=1.36"]

[project.scripts]
tg = "tgcli.cli:entrypoint"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/tgcli"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create venv and install**

Run: `uv venv && uv pip install -e . --group dev`
Expected: installs telethon, pytest without errors.

- [ ] **Step 3: Write the failing test**

`tests/test_errors.py`:
```python
from tgcli.errors import (
    ConfigError,
    NotFoundError,
    PolicyError,
    RateLimitError,
    TgcliError,
)


def test_exit_codes_match_contract():
    assert TgcliError("x").exit_code == 1
    assert PolicyError("x").exit_code == 2
    assert ConfigError("x").exit_code == 3
    assert NotFoundError("x").exit_code == 4
    assert RateLimitError("x").exit_code == 5


def test_error_carries_code_and_details():
    err = RateLimitError("flood", retry_after=42)
    assert err.code == "FLOOD_WAIT"
    assert err.details == {"retry_after": 42}
    assert str(err) == "flood"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_errors.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.errors'`

- [ ] **Step 5: Implement**

`src/tgcli/__init__.py`:
```python
__version__ = "0.1.0"
```

`src/tgcli/errors.py`:
```python
"""Exit codes live here and nowhere else (docs/CONTRACT.md §4)."""


class TgcliError(Exception):
    exit_code = 1
    code = "RUNTIME"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class PolicyError(TgcliError):
    exit_code = 2
    code = "BLOCKED"


class ConfigError(TgcliError):
    exit_code = 3
    code = "CONFIG"


class NotFoundError(TgcliError):
    exit_code = 4
    code = "NOT_FOUND"


class RateLimitError(TgcliError):
    exit_code = 5
    code = "FLOOD_WAIT"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_errors.py -q`
Expected: `2 passed`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/tgcli/__init__.py src/tgcli/errors.py tests/test_errors.py
git commit -m "Add project scaffold and errors module with contract exit codes"
```

---

### Task 2: Output module

**Files:**
- Create: `src/tgcli/output.py`
- Test: `tests/test_output.py`

**Interfaces:**
- Consumes: `TgcliError` from Task 1.
- Produces: `emit_json(data: dict) -> None` (one JSON doc + newline to stdout, `ensure_ascii=False`, `default=str`); `emit_plain(rows: list[tuple]) -> None` (TSV to stdout, `None` → empty string); `note(message: str) -> None` (stderr); `emit_error(err: TgcliError, *, as_json: bool) -> None` (stderr; JSON object line when `as_json`).

- [ ] **Step 1: Write the failing test**

`tests/test_output.py`:
```python
import json

from tgcli import output
from tgcli.errors import RateLimitError


def test_emit_json_writes_one_document_to_stdout(capsys):
    output.emit_json({"a": 1, "s": "приве\tт"})
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"a": 1, "s": "приве\tт"}
    assert captured.out.endswith("\n")
    assert captured.err == ""


def test_emit_plain_writes_tsv_with_empty_for_none(capsys):
    output.emit_plain([(1, "x", None), (2, "y", "z")])
    assert capsys.readouterr().out == "1\tx\t\n2\ty\tz\n"


def test_note_goes_to_stderr_only(capsys):
    output.note("working...")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "working...\n"


def test_emit_error_json_mode_is_machine_readable(capsys):
    output.emit_error(RateLimitError("flood", retry_after=42), as_json=True)
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "error": {"code": "FLOOD_WAIT", "message": "flood", "retry_after": 42}
    }


def test_emit_error_human_mode(capsys):
    output.emit_error(RateLimitError("flood", retry_after=42), as_json=False)
    assert capsys.readouterr().err == "error: flood\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_output.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.output'`

- [ ] **Step 3: Implement**

`src/tgcli/output.py`:
```python
"""The only module allowed to write to stdout (docs/CONTRACT.md §2)."""

import json
import sys

from tgcli.errors import TgcliError


def emit_json(data) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, default=str)
    sys.stdout.write("\n")


def emit_plain(rows) -> None:
    for row in rows:
        sys.stdout.write(
            "\t".join("" if cell is None else str(cell) for cell in row) + "\n"
        )


def note(message: str) -> None:
    sys.stderr.write(message + "\n")


def emit_error(err: TgcliError, *, as_json: bool) -> None:
    if as_json:
        payload = {"error": {"code": err.code, "message": str(err), **err.details}}
        sys.stderr.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    else:
        note(f"error: {err}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_output.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/tgcli/output.py tests/test_output.py
git commit -m "Add output module enforcing stdout/stderr contract"
```

---

### Task 3: Config + accounts registry

**Files:**
- Create: `src/tgcli/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `ConfigError` from Task 1.
- Produces: `Account(alias: str, api_id: int, api_hash: str, session: str)` frozen dataclass; `Config(default_account: str | None, accounts: dict[str, Account])`; `load_config(path: Path | None = None) -> Config` (default path `$TGCLI_CONFIG` or `~/.config/tgcli/config.toml`); `resolve_account(config: Config, alias: str | None) -> Account` (flag > `TGCLI_ACCOUNT` env > config default; raises `ConfigError`).

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
import pytest

from tgcli.config import load_config, resolve_account
from tgcli.errors import ConfigError

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"

[accounts.pl]
api_id = 67890
api_hash = "fedcba9876543210"
session = "poland"
"""


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    return path


def test_load_config_parses_accounts(config_file):
    config = load_config(config_file)
    assert config.default_account == "main"
    assert config.accounts["main"].api_id == 12345
    assert config.accounts["main"].session == "main"  # defaults to alias
    assert config.accounts["pl"].session == "poland"


def test_missing_config_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_account_missing_api_id_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[accounts.bad]\napi_hash = "x"\n')
    with pytest.raises(ConfigError):
        load_config(path)


def test_resolve_priority_flag_env_default(config_file, monkeypatch):
    config = load_config(config_file)
    monkeypatch.setenv("TGCLI_ACCOUNT", "pl")
    assert resolve_account(config, "main").alias == "main"  # flag wins
    assert resolve_account(config, None).alias == "pl"      # env wins
    monkeypatch.delenv("TGCLI_ACCOUNT")
    assert resolve_account(config, None).alias == "main"    # config default


def test_resolve_unknown_alias_raises(config_file):
    config = load_config(config_file)
    with pytest.raises(ConfigError):
        resolve_account(config, "ghost")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.config'`

- [ ] **Step 3: Implement**

`src/tgcli/config.py`:
```python
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tgcli.errors import ConfigError


def default_config_path() -> Path:
    return Path(os.environ.get("TGCLI_CONFIG", "~/.config/tgcli/config.toml")).expanduser()


@dataclass(frozen=True)
class Account:
    alias: str
    api_id: int
    api_hash: str
    session: str


@dataclass(frozen=True)
class Config:
    default_account: str | None
    accounts: dict[str, Account]


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw = tomllib.loads(path.read_text())
    accounts: dict[str, Account] = {}
    for alias, entry in raw.get("accounts", {}).items():
        try:
            accounts[alias] = Account(
                alias=alias,
                api_id=int(entry["api_id"]),
                api_hash=str(entry["api_hash"]),
                session=str(entry.get("session", alias)),
            )
        except KeyError as exc:
            raise ConfigError(f"account {alias!r}: missing key {exc}") from exc
    return Config(default_account=raw.get("default_account"), accounts=accounts)


def resolve_account(config: Config, alias: str | None) -> Account:
    alias = alias or os.environ.get("TGCLI_ACCOUNT") or config.default_account
    if not alias:
        raise ConfigError(
            "no account selected: pass --account, set TGCLI_ACCOUNT, "
            "or set default_account in config"
        )
    if alias not in config.accounts:
        raise ConfigError(f"unknown account alias: {alias!r}")
    return config.accounts[alias]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/tgcli/config.py tests/test_config.py
git commit -m "Add config module with accounts registry and resolution order"
```

---

### Task 4: Session manager (lock + client lifecycle)

**Files:**
- Create: `src/tgcli/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `Account` (Task 3), `ConfigError` (Task 1).
- Produces: `state_dir() -> Path` (`$TGCLI_STATE_DIR` or `~/.local/state/tgcli`); `session_path(account: Account) -> Path`; `client(account: Account)` async context manager yielding a connected, authorized `telethon.TelegramClient`; raises `ConfigError` if lock busy or unauthorized. Internal factory hook `_make_client(path, account)` so tests can substitute a fake.

- [ ] **Step 1: Write the failing test**

`tests/test_session.py`:
```python
import fcntl

import pytest

from tgcli import session
from tgcli.config import Account
from tgcli.errors import ConfigError

ACCOUNT = Account(alias="t", api_id=1, api_hash="h", session="t")


class FakeTelethonClient:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.connected = False

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def disconnect(self):
        self.connected = False


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    return tmp_path


async def test_client_connects_and_disconnects(state, monkeypatch):
    fake = FakeTelethonClient()
    monkeypatch.setattr(session, "_make_client", lambda path, account: fake)
    async with session.client(ACCOUNT) as tg:
        assert tg.connected is True
    assert fake.connected is False


async def test_busy_lock_fails_fast_with_config_error(state, monkeypatch):
    fake = FakeTelethonClient()
    monkeypatch.setattr(session, "_make_client", lambda path, account: fake)
    lock_path = session.session_path(ACCOUNT).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(ConfigError, match="busy"):
        async with session.client(ACCOUNT):
            pass
    holder.close()


async def test_unauthorized_session_raises_config_error(state, monkeypatch):
    fake = FakeTelethonClient(authorized=False)
    monkeypatch.setattr(session, "_make_client", lambda path, account: fake)
    with pytest.raises(ConfigError, match="not authorized"):
        async with session.client(ACCOUNT):
            pass
    assert fake.connected is False  # cleaned up even on failure
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_session.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.session'`

- [ ] **Step 3: Implement**

`src/tgcli/session.py`:
```python
"""Session files, per-account locks, Telethon client lifecycle (ADR-0004)."""

import fcntl
import os
from contextlib import asynccontextmanager
from pathlib import Path

from telethon import TelegramClient

from tgcli.config import Account
from tgcli.errors import ConfigError


def state_dir() -> Path:
    return Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser()


def session_path(account: Account) -> Path:
    return state_dir() / "sessions" / f"{account.session}.session"


def _make_client(path: Path, account: Account) -> TelegramClient:
    return TelegramClient(str(path), account.api_id, account.api_hash)


@asynccontextmanager
async def client(account: Account):
    path = session_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = open(path.with_suffix(".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ConfigError(
            f"session {account.session!r} is busy (another tg process is using it); "
            "retry in a few seconds"
        ) from None
    tg = _make_client(path, account)
    try:
        await tg.connect()
        if not await tg.is_user_authorized():
            raise ConfigError(
                f"session {account.session!r} is not authorized; "
                "run: tg accounts import (phase 6) or authorize manually"
            )
        yield tg
    finally:
        await tg.disconnect()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_session.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/tgcli/session.py tests/test_session.py
git commit -m "Add session manager with per-account flock and client lifecycle"
```

---

### Task 5: CLI dispatch + `tg accounts list`

**Files:**
- Create: `src/tgcli/cli.py`, `src/tgcli/commands/__init__.py`, `src/tgcli/commands/accounts.py`
- Test: `tests/test_cli_accounts.py`

**Interfaces:**
- Consumes: `load_config`, `resolve_account` (Task 3), `output.*` (Task 2), `TgcliError` (Task 1).
- Produces: `build_parser() -> argparse.ArgumentParser`; `main(argv: list[str] | None = None) -> int`; `entrypoint() -> None` (console script); `accounts.list_accounts(config: Config) -> dict`; `accounts.to_rows(data: dict) -> list[tuple]`. Later tasks add branches to `_run_network(args, account)`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli_accounts.py`:
```python
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
    return path


def test_accounts_list_json(config_env, capsys):
    code = main(["--json", "accounts", "list"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "default_account": "main",
        "accounts": [{"alias": "main", "session": "main"}],
    }


def test_accounts_list_never_leaks_api_hash(config_env, capsys):
    main(["--json", "accounts", "list"])
    assert "abcdef" not in capsys.readouterr().out


def test_missing_config_exits_3(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_CONFIG", str(tmp_path / "nope.toml"))
    code = main(["--json", "accounts", "list"])
    assert code == 3
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout stays clean on errors
    assert json.loads(captured.err)["error"]["code"] == "CONFIG"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_accounts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tgcli.cli'`

- [ ] **Step 3: Implement**

`src/tgcli/commands/__init__.py`: empty file.

`src/tgcli/commands/accounts.py`:
```python
from tgcli.config import Config


def list_accounts(config: Config) -> dict:
    return {
        "default_account": config.default_account,
        "accounts": [
            {"alias": account.alias, "session": account.session}
            for account in config.accounts.values()
        ],
    }


def to_rows(data: dict) -> list[tuple]:
    return [(entry["alias"], entry["session"]) for entry in data["accounts"]]
```

`src/tgcli/cli.py`:
```python
import argparse
import asyncio
import sys

from tgcli import __version__, output
from tgcli.commands import accounts as accounts_cmd
from tgcli.config import load_config, resolve_account
from tgcli.errors import TgcliError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tg", description="Stateless Telegram CLI")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--account", help="account alias from config")
    parser.add_argument("--json", action="store_true", help="JSON to stdout")
    parser.add_argument("--plain", action="store_true", help="TSV to stdout")
    parser.add_argument("--timeout", type=float, default=60.0)
    sub = parser.add_subparsers(dest="command", required=True)

    p_accounts = sub.add_parser("accounts", help="Manage accounts")
    accounts_sub = p_accounts.add_subparsers(dest="subcommand", required=True)
    accounts_sub.add_parser("list", help="List configured accounts")

    return parser


async def _run_network(args, account) -> tuple[dict, list[tuple]]:
    raise AssertionError(f"unhandled network command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config()
        if args.command == "accounts":
            data = accounts_cmd.list_accounts(config)
            rows = accounts_cmd.to_rows(data)
        else:
            account = resolve_account(config, args.account)
            data, rows = asyncio.run(
                asyncio.wait_for(_run_network(args, account), timeout=args.timeout)
            )
    except TgcliError as err:
        output.emit_error(err, as_json=args.json)
        return err.exit_code
    if args.json:
        output.emit_json(data)
    else:
        output.emit_plain(rows)
    return 0


def entrypoint() -> None:
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli_accounts.py -q`
Expected: `3 passed`

- [ ] **Step 5: Smoke the console script**

Run: `.venv/bin/tg --version`
Expected: `0.1.0`

- [ ] **Step 6: Commit**

```bash
git add src/tgcli/cli.py src/tgcli/commands/ tests/test_cli_accounts.py
git commit -m "Add CLI dispatch and accounts list command"
```

---

### Task 6: `tg dialogs`

**Files:**
- Create: `src/tgcli/commands/dialogs.py`, `tests/conftest.py`
- Modify: `src/tgcli/cli.py` (add subparser + `_run_network` branch)
- Test: `tests/test_cli_dialogs.py`

**Interfaces:**
- Consumes: `session.client` (Task 4), CLI plumbing (Task 5).
- Produces: `dialogs.fetch_dialogs(tg, limit: int) -> dict` matching CONTRACT.md §5 shape; `dialogs.to_rows(data) -> list[tuple]` with frozen column order `(id, kind, username, name, unread)`; shared test fakes in `tests/conftest.py` (`FakeClient`, `make_session_fake`).

- [ ] **Step 1: Write shared fakes**

`tests/conftest.py`:
```python
from contextlib import asynccontextmanager
from types import SimpleNamespace


class FakeClient:
    """Duck-typed stand-in for TelegramClient used by command tests."""

    def __init__(self, dialogs=(), messages=(), entities=None):
        self._dialogs = list(dialogs)
        self._messages = list(messages)
        self._entities = entities or {}

    async def iter_dialogs(self, limit=None):
        for dialog in self._dialogs[:limit]:
            yield dialog

    async def iter_messages(self, entity, limit=None):
        for message in self._messages[:limit]:
            yield message

    async def get_entity(self, key):
        if key not in self._entities:
            raise ValueError(f"no entity {key!r}")
        return self._entities[key]


def make_session_fake(monkeypatch, fake_client):
    """Route tgcli.cli's session.client(...) to a FakeClient."""
    from tgcli import cli

    @asynccontextmanager
    async def fake_session(account):
        yield fake_client

    monkeypatch.setattr(cli.session, "client", fake_session)


def ns(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)
```

- [ ] **Step 2: Write the failing test**

`tests/test_cli_dialogs.py`:
```python
import datetime as dt
import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
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


def make_dialog():
    return ns(
        id=-1001234,
        name="Channel",
        is_channel=True,
        is_group=False,
        entity=ns(username="chan"),
        unread_count=3,
        date=dt.datetime(2026, 7, 6, 11, 59, tzinfo=dt.timezone.utc),
    )


def test_dialogs_json_matches_contract(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_dialog()]))
    code = main(["--json", "dialogs"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "dialogs": [
            {
                "id": -1001234,
                "name": "Channel",
                "kind": "channel",
                "username": "chan",
                "unread": 3,
                "last_message_at": "2026-07-06T11:59:00+00:00",
            }
        ]
    }


def test_dialogs_plain_column_order_frozen(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_dialog()]))
    main(["--plain", "dialogs"])
    assert capsys.readouterr().out == "-1001234\tchannel\tchan\tChannel\t3\n"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_dialogs.py -q`
Expected: FAIL — argparse error `invalid choice: 'dialogs'`

- [ ] **Step 4: Implement**

`src/tgcli/commands/dialogs.py`:
```python
def _kind(dialog) -> str:
    if dialog.is_channel:
        return "channel"
    if dialog.is_group:
        return "group"
    return "user"


async def fetch_dialogs(tg, limit: int = 50) -> dict:
    dialogs = []
    async for dialog in tg.iter_dialogs(limit=limit):
        dialogs.append(
            {
                "id": dialog.id,
                "name": dialog.name,
                "kind": _kind(dialog),
                "username": getattr(dialog.entity, "username", None),
                "unread": dialog.unread_count,
                "last_message_at": dialog.date.isoformat() if dialog.date else None,
            }
        )
    return {"dialogs": dialogs}


def to_rows(data: dict) -> list[tuple]:
    return [
        (d["id"], d["kind"], d["username"], d["name"], d["unread"])
        for d in data["dialogs"]
    ]
```

Modify `src/tgcli/cli.py` — add imports and wire the command:
```python
from tgcli import session
from tgcli.commands import dialogs as dialogs_cmd
```
In `build_parser()`, after the accounts block:
```python
    p_dialogs = sub.add_parser("dialogs", help="List dialogs")
    p_dialogs.add_argument("--limit", type=int, default=50)
```
Replace `_run_network` with:
```python
async def _run_network(args, account) -> tuple[dict, list[tuple]]:
    async with session.client(account) as tg:
        if args.command == "dialogs":
            data = await dialogs_cmd.fetch_dialogs(tg, limit=args.limit)
            return data, dialogs_cmd.to_rows(data)
        raise AssertionError(f"unhandled network command: {args.command}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: all tests pass (accounts tests still green — dispatch unchanged for them).

- [ ] **Step 6: Commit**

```bash
git add src/tgcli/commands/dialogs.py src/tgcli/cli.py tests/conftest.py tests/test_cli_dialogs.py
git commit -m "Add dialogs command with contract JSON and frozen TSV columns"
```

---

### Task 7: `tg read` + FloodWait mapping + live smoke

**Files:**
- Create: `src/tgcli/commands/read.py`, `tests/live/test_live_smoke.py`
- Modify: `src/tgcli/cli.py` (subparser, `_run_network` branch, FloodWait mapping)
- Test: `tests/test_cli_read.py`

**Interfaces:**
- Consumes: fakes from Task 6, `NotFoundError`/`RateLimitError` (Task 1).
- Produces: `read.fetch_messages(tg, chat: str, limit: int) -> dict` matching CONTRACT.md §5 `tg read` shape; `read.to_rows(data)` columns `(id, date, from_name, text)`; `cli.py` converts `telethon.errors.FloodWaitError` → `RateLimitError` with `retry_after`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli_read.py`:
```python
import datetime as dt
import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
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


def make_fake():
    entity = ns(id=-1001234, title="Channel")
    message = ns(
        id=42,
        date=dt.datetime(2026, 7, 6, 10, 0, tzinfo=dt.timezone.utc),
        sender_id=111,
        sender=ns(first_name="Alice", last_name=None),
        text="hello",
        media=None,
        reply_to_msg_id=None,
    )
    return FakeClient(messages=[message], entities={"@chan": entity})


def test_read_json_matches_contract(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake())
    code = main(["--json", "read", "@chan", "--limit", "5"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "dialog": {"id": -1001234, "name": "Channel"},
        "messages": [
            {
                "id": 42,
                "date": "2026-07-06T10:00:00+00:00",
                "from": {"id": 111, "name": "Alice"},
                "text": "hello",
                "media": None,
                "reply_to": None,
            }
        ],
    }


def test_read_unknown_dialog_exits_4(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake())
    code = main(["--json", "read", "@ghost"])
    assert code == 4
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "NOT_FOUND"


def test_floodwait_maps_to_exit_5(config_env, monkeypatch, capsys):
    from telethon import errors as tg_errors

    fake = make_fake()

    async def flood(entity, limit=None):
        raise tg_errors.FloodWaitError(request=None, capture=42)
        yield  # pragma: no cover — makes this an async generator

    fake.iter_messages = flood
    make_session_fake(monkeypatch, fake)
    code = main(["--json", "read", "@chan"])
    assert code == 5
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["code"] == "FLOOD_WAIT"
    assert err["retry_after"] == 42
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_read.py -q`
Expected: FAIL — argparse error `invalid choice: 'read'`

- [ ] **Step 3: Implement**

`src/tgcli/commands/read.py`:
```python
from tgcli.errors import NotFoundError


def _sender_name(message) -> str | None:
    sender = getattr(message, "sender", None)
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(p for p in parts if p)
    return name or getattr(sender, "title", None) or getattr(sender, "username", None)


def _dialog_name(entity, fallback: str) -> str:
    return (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or getattr(entity, "username", None)
        or fallback
    )


async def fetch_messages(tg, chat: str, limit: int = 20) -> dict:
    try:
        entity = await tg.get_entity(chat)
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None
    messages = []
    async for message in tg.iter_messages(entity, limit=limit):
        messages.append(
            {
                "id": message.id,
                "date": message.date.isoformat() if message.date else None,
                "from": {"id": message.sender_id, "name": _sender_name(message)},
                "text": message.text or "",
                "media": type(message.media).__name__ if message.media else None,
                "reply_to": message.reply_to_msg_id,
            }
        )
    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "messages": messages,
    }


def to_rows(data: dict) -> list[tuple]:
    return [
        (m["id"], m["date"], m["from"]["name"], m["text"].replace("\n", " "))
        for m in data["messages"]
    ]
```

Modify `src/tgcli/cli.py`:

Add import:
```python
from telethon import errors as telethon_errors

from tgcli.commands import read as read_cmd
from tgcli.errors import RateLimitError, TgcliError
```
In `build_parser()`:
```python
    p_read = sub.add_parser("read", help="Read recent messages from a dialog")
    p_read.add_argument("chat", help="@username, t.me link, or dialog id")
    p_read.add_argument("--limit", type=int, default=20)
```
In `_run_network`, add the branch and FloodWait mapping:
```python
async def _run_network(args, account) -> tuple[dict, list[tuple]]:
    try:
        async with session.client(account) as tg:
            if args.command == "dialogs":
                data = await dialogs_cmd.fetch_dialogs(tg, limit=args.limit)
                return data, dialogs_cmd.to_rows(data)
            if args.command == "read":
                data = await read_cmd.fetch_messages(tg, args.chat, limit=args.limit)
                return data, read_cmd.to_rows(data)
            raise AssertionError(f"unhandled network command: {args.command}")
    except telethon_errors.FloodWaitError as exc:
        raise RateLimitError(
            f"rate limited for {exc.seconds}s", retry_after=exc.seconds
        ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Add gated live smoke**

`tests/live/test_live_smoke.py`:
```python
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
```

Note: `python -m tgcli.cli` requires a `__main__` guard — add at the bottom of `cli.py`:
```python
if __name__ == "__main__":
    entrypoint()
```

- [ ] **Step 6: Run the full suite (live skipped by default)**

Run: `.venv/bin/pytest -q`
Expected: unit tests pass, 2 live tests skipped.

- [ ] **Step 7: Update MAP.md statuses and DEVLOG, commit**

Mark `pyproject.toml`, `__init__.py`, `cli.py`, `output.py`, `errors.py`,
`config.py`, `session.py`, `commands/accounts.py`, `commands/dialogs.py`,
`commands/read.py`, `tests/` as `[done]` in docs/MAP.md. Append a DEVLOG entry.

```bash
git add -A
git commit -m "Add read command, FloodWait mapping, and gated live smoke"
```

---

## Phase 1 Acceptance (from PLAN.md)

- [ ] `pytest -q` green.
- [ ] With a real config for account `main` (api_id/api_hash from old stack's
      `mcp/.env`, session copied or freshly authorized):
      `tg dialogs --json | jq .` returns real dialogs.
- [ ] Two concurrent `tg read` calls on the same account: second exits 3
      with "busy" message; session file intact afterwards.
