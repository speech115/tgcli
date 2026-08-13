"""Unit tests for accounts login (ADR-0042 Slices 2–3)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tgcli import authclient, desktop, login_state, safety
from tgcli.cli import main
from tgcli.commands import login as login_cmd
from tgcli.config import load_config
from tgcli.errors import ConfigError, PolicyError
from tgcli.formatting import mask_phone

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "hash-main"
session = "main"
"""

PHONE = "+79991234589"


@pytest.fixture
def env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(SAMPLE)
    state = tmp_path / "state"
    state.mkdir()
    (state / "sessions").mkdir()
    monkeypatch.setenv("TGCLI_CONFIG", str(config_path))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    # Headless: no native dialog unless a test opts in.
    monkeypatch.setattr(desktop, "dialog_available", lambda: False)
    return {"config": config_path, "state": state}


class FakeAuthClient:
    def __init__(self):
        self.sign_in_calls = []
        self.send_code_calls = []
        self.disconnected = False
        self._me = SimpleNamespace(id=42, username="u", phone="79991234589")
        self._send_code_error = None
        self._sign_in_error = None
        self._authorized_probe = False

    async def send_code_request(self, phone):
        assert isinstance(phone, str)
        self.send_code_calls.append(phone)
        if self._send_code_error:
            raise self._send_code_error
        return SimpleNamespace(phone_code_hash="hash-abc")

    async def sign_in(self, *args, **kwargs):
        self.sign_in_calls.append({"args": args, "kwargs": kwargs})
        if self._sign_in_error:
            err = self._sign_in_error
            self._sign_in_error = None
            raise err
        return self._me

    async def get_me(self):
        return self._me

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self._authorized_probe

    async def connect(self):
        return None


@pytest.fixture
def fake_client(env, monkeypatch):
    client = FakeAuthClient()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_unauthorized(path, api_id, api_hash):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"staged")
        yield client

    monkeypatch.setattr(authclient, "unauthorized_client", fake_unauthorized)
    monkeypatch.setattr(login_cmd.authclient, "unauthorized_client", fake_unauthorized)

    async def fake_probe(account):
        return client._authorized_probe

    monkeypatch.setattr(authclient, "probe_authorized", fake_probe)
    monkeypatch.setattr(login_cmd.authclient, "probe_authorized", fake_probe)
    return client


async def _complete_phone_login(pending: dict) -> dict:
    return await login_cmd.continue_login(
        login_id=pending["login_id"], code="12345", password_stdin=False
    )


def test_mask_phone_shapes():
    assert mask_phone("+79991234589") == "+7…89"
    assert mask_phone("79991234589") == "79…89"
    assert mask_phone("12") == "…12"
    assert mask_phone("") == ""
    assert mask_phone(None) == ""


@pytest.mark.asyncio
async def test_attempt_json_has_no_raw_phone_on_disk(env, fake_client):
    """T08 / ADR-0042 §5: the attempt json on disk must never carry the raw
    phone, only the continue path may resolve it (from its own sidecar)."""
    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=PHONE,
        api_id=1,
        api_hash="h",
        force=False,
    )
    attempt_path = env["state"] / "logins" / f"{pending['login_id']}.json"
    raw = attempt_path.read_text()
    assert PHONE not in raw
    assert "phone" not in json.loads(raw)

    data = await _complete_phone_login(pending)
    assert data["status"] == "authorized"
    assert fake_client.sign_in_calls[0]["args"][0] == PHONE


@pytest.mark.asyncio
async def test_login_promotes_to_configured_session_stem(env, fake_client):
    env["config"].write_text(
        env["config"].read_text()
        + """
[accounts.work]
api_id = 54321
api_hash = "hash-work"
session = "work-real"
"""
    )
    old_session = env["state"] / "sessions" / "work-real.session"
    old_session.write_bytes(b"old-live-session")
    wrong_path = env["state"] / "sessions" / "work.session"

    pending = await login_cmd.start_login(
        load_config(),
        "work",
        phone=PHONE,
        api_id=None,
        api_hash=None,
        force=True,
    )
    data = await _complete_phone_login(pending)
    assert data["status"] == "authorized"
    assert str(old_session) == data["session"]
    assert not wrong_path.exists()
    assert (env["state"] / "sessions" / "work-real.session.bak").is_file()


@pytest.mark.asyncio
async def test_force_required_when_authorized(env, fake_client):
    (env["state"] / "sessions" / "main.session").write_bytes(b"live")
    fake_client._authorized_probe = True
    with pytest.raises(PolicyError, match="--force"):
        await login_cmd.start_login(
            load_config(),
            "main",
            phone=PHONE,
            api_id=None,
            api_hash=None,
            force=False,
        )


@pytest.mark.asyncio
async def test_new_alias_orphan_session_requires_force(env, fake_client):
    """Orphan session for an unconfigured alias still needs --force."""
    (env["state"] / "sessions" / "tmp.session").write_bytes(b"orphan-live")
    fake_client._authorized_probe = True
    with pytest.raises(PolicyError, match="--force"):
        await login_cmd.start_login(
            load_config(),
            "tmp",
            phone=PHONE,
            api_id=1,
            api_hash="h",
            force=False,
        )


@pytest.mark.asyncio
async def test_new_alias_orphan_session_keeps_backup(env, fake_client):
    """Phone promotion for a new alias must .bak an existing destination."""
    orphan = env["state"] / "sessions" / "tmp.session"
    orphan.write_bytes(b"orphan-previous")
    fake_client._authorized_probe = False

    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=PHONE,
        api_id=1,
        api_hash="h",
        force=False,
    )
    data = await _complete_phone_login(pending)
    assert data["status"] == "authorized"
    assert data["backup"] is not None
    bak = env["state"] / "sessions" / "tmp.session.bak"
    assert bak.is_file()
    assert bak.read_bytes() == b"orphan-previous"
    assert orphan.read_bytes() == b"staged"


@pytest.mark.asyncio
async def test_no_probe_when_session_absent(env, fake_client, monkeypatch):
    probes = []

    async def track(account):
        probes.append(account.alias)
        return False

    monkeypatch.setattr(login_cmd.authclient, "probe_authorized", track)
    pending = await login_cmd.start_login(
        load_config(),
        "main",
        phone=PHONE,
        api_id=None,
        api_hash=None,
        force=False,
    )
    await _complete_phone_login(pending)
    assert probes == []


def test_continue_rejects_phone_force_api(env, capsys):
    code = main(
        [
            "accounts",
            "login",
            "--continue",
            "l_abc",
            "--phone",
            "+1",
            "--json",
        ]
    )
    assert code == 2


def test_login_readonly_gate(env, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_READONLY", "1")
    code = main(
        ["accounts", "login", "tmp", "--api-id", "1", "--api-hash", "h", "--json"]
    )
    assert code == 2


@pytest.mark.asyncio
async def test_audit_before_promote(env, fake_client, monkeypatch):
    order: list[str] = []
    real_audit = safety.append_audit
    real_promote = login_state.promote

    def track_audit(action, account, details):
        order.append("audit")
        real_audit(action, account, details)

    def track_promote(*a, **k):
        order.append("promote")
        return real_promote(*a, **k)

    monkeypatch.setattr(login_cmd.safety, "append_audit", track_audit)
    monkeypatch.setattr(login_cmd.login_state, "promote", track_promote)
    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=PHONE,
        api_id=1,
        api_hash="h",
        force=False,
    )
    await _complete_phone_login(pending)
    assert order == ["audit", "audit", "promote"]


@pytest.mark.asyncio
async def test_phone_three_step_handshake(env, fake_client, monkeypatch):
    from telethon.errors import SessionPasswordNeededError

    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone="+79991234589",
        api_id=1,
        api_hash="h",
        force=False,
    )
    assert pending["next"] == "code"
    assert fake_client.send_code_calls == ["+79991234589"]

    fake_client._sign_in_error = SessionPasswordNeededError(request=None)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(readline=lambda: "12345\n"))
    mid = await login_cmd.continue_login(
        login_id=pending["login_id"], code="-", password_stdin=False
    )
    assert mid["next"] == "password"
    sign = fake_client.sign_in_calls[0]
    assert sign["args"] == ("+79991234589", "12345")
    assert sign["kwargs"] == {"phone_code_hash": "hash-abc"}
    assert isinstance(sign["args"][0], str)
    assert isinstance(sign["args"][1], str)
    assert isinstance(sign["kwargs"]["phone_code_hash"], str)

    monkeypatch.setattr("sys.stdin", SimpleNamespace(readline=lambda: "cloud\n"))
    done = await login_cmd.continue_login(
        login_id=pending["login_id"], code=None, password_stdin=True
    )
    assert done["status"] == "authorized"
    assert fake_client.sign_in_calls[-1]["kwargs"] == {"password": "cloud"}


@pytest.mark.asyncio
async def test_wrong_phone_login_password_keeps_attempt(env, fake_client, monkeypatch):
    from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=PHONE,
        api_id=1,
        api_hash="h",
        force=False,
    )
    fake_client._sign_in_error = SessionPasswordNeededError(request=None)
    password_pending = await login_cmd.continue_login(
        login_id=pending["login_id"], code="12345", password_stdin=False
    )
    assert password_pending["next"] == "password"

    fake_client._sign_in_error = PasswordHashInvalidError(request=None)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(readline=lambda: "wrong\n"))
    with pytest.raises(ConfigError, match="password"):
        await login_cmd.continue_login(
            login_id=pending["login_id"], code=None, password_stdin=True
        )
    assert (env["state"] / "logins" / f"{pending['login_id']}.json").exists()


@pytest.mark.asyncio
async def test_phone_code_invalid_keeps_attempt(env, fake_client):
    from telethon.errors import PhoneCodeInvalidError

    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone="+79991234589",
        api_id=1,
        api_hash="h",
        force=False,
    )
    fake_client._sign_in_error = PhoneCodeInvalidError(request=None)
    with pytest.raises(ConfigError, match="code"):
        await login_cmd.continue_login(
            login_id=pending["login_id"], code="00000", password_stdin=False
        )
    assert (env["state"] / "logins" / f"{pending['login_id']}.json").exists()


@pytest.mark.asyncio
async def test_phone_code_empty_keeps_attempt(env, fake_client):
    from telethon.errors import PhoneCodeEmptyError

    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone="+79991234589",
        api_id=1,
        api_hash="h",
        force=False,
    )
    fake_client._sign_in_error = PhoneCodeEmptyError(request=None)
    with pytest.raises(ConfigError, match="code"):
        await login_cmd.continue_login(
            login_id=pending["login_id"], code="x", password_stdin=False
        )
    assert (env["state"] / "logins" / f"{pending['login_id']}.json").exists()


@pytest.mark.asyncio
async def test_headless_code_requires_flag(env, fake_client):
    """Without a dialog or --code, headless must not block on stdin."""
    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone="+79991234589",
        api_id=1,
        api_hash="h",
        force=False,
    )
    with pytest.raises(ConfigError, match="--code"):
        await login_cmd.continue_login(
            login_id=pending["login_id"], code=None, password_stdin=False
        )
    assert (env["state"] / "logins" / f"{pending['login_id']}.json").exists()


@pytest.mark.asyncio
async def test_empty_code_rejected_before_sign_in(env, fake_client):
    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone="+79991234589",
        api_id=1,
        api_hash="h",
        force=False,
    )
    with pytest.raises(ConfigError, match="--code"):
        await login_cmd.continue_login(
            login_id=pending["login_id"], code="", password_stdin=False
        )
    assert fake_client.sign_in_calls == []


@pytest.mark.asyncio
async def test_phone_code_expired_discards(env, fake_client):
    from telethon.errors import PhoneCodeExpiredError

    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone="+7999",
        api_id=1,
        api_hash="h",
        force=False,
    )
    fake_client._sign_in_error = PhoneCodeExpiredError(request=None)
    with pytest.raises(ConfigError, match="expired"):
        await login_cmd.continue_login(
            login_id=pending["login_id"], code="1", password_stdin=False
        )
    assert not (env["state"] / "logins" / f"{pending['login_id']}.json").exists()


@pytest.mark.asyncio
async def test_flood_wait_exit_5(env, fake_client):
    from telethon.errors import FloodWaitError

    fake_client._send_code_error = FloodWaitError(request=None, capture=30)
    with pytest.raises(Exception) as excinfo:
        await login_cmd.start_login(
            load_config(),
            "tmp",
            phone="+7999",
            api_id=1,
            api_hash="h",
            force=False,
        )
    assert excinfo.value.exit_code == 5


@pytest.mark.asyncio
async def test_audit_masks_phone_no_secrets(env, fake_client, monkeypatch):
    await login_cmd.start_login(
        load_config(),
        "tmp",
        phone="+79991234589",
        api_id=1,
        api_hash="h",
        force=False,
    )
    monkeypatch.setattr("sys.stdin", SimpleNamespace(readline=lambda: "99999\n"))
    await login_cmd.continue_login(
        login_id=json.loads(
            next((env["state"] / "logins").glob("l_*.json")).read_text()
        )["login_id"],
        code="99999",
        password_stdin=False,
    )
    audit = (env["state"] / "audit.jsonl").read_text()
    assert "+79991234589" not in audit
    assert "99999" not in audit
    assert "+7…89" in audit or "phone" in audit


def test_initial_login_requires_phone_before_authclient(env, fake_client, capsys):
    code = main(
        [
            "accounts",
            "login",
            "tmp",
            "--api-id",
            "1",
            "--api-hash",
            "h",
            "--json",
        ]
    )
    assert code == 2
    assert "--phone" in capsys.readouterr().err
    assert fake_client.send_code_calls == []


def test_initial_login_rejects_an_empty_phone_before_authclient(
    env, fake_client, capsys
):
    code = main(
        [
            "accounts",
            "login",
            "tmp",
            "--phone",
            "",
            "--api-id",
            "1",
            "--api-hash",
            "h",
            "--json",
        ]
    )
    assert code == 2
    assert "non-empty" in capsys.readouterr().err
    assert fake_client.send_code_calls == []


def test_cli_phone_start_returns_a_resumable_code_step(env, fake_client, capsys):
    code = main(
        [
            "accounts",
            "login",
            "tmp",
            "--phone",
            PHONE,
            "--api-id",
            "1",
            "--api-hash",
            "h",
            "--json",
        ]
    )
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["method"] == "phone"
    assert data["status"] == "pending"
    assert data["next"] == "code"
    assert data["login_id"].startswith("l_")
    assert fake_client.send_code_calls == [PHONE]


def test_initial_login_rejects_password_stdin(env, capsys):
    code = main(
        [
            "accounts",
            "login",
            "main",
            "--phone",
            PHONE,
            "--password-stdin",
            "--json",
        ]
    )
    assert code == 2
    assert "without --continue" in capsys.readouterr().err


@pytest.mark.parametrize(
    "alias",
    ["/tmp/x", "../x", "a.b", ""],
)
@pytest.mark.asyncio
async def test_new_alias_rejects_invalid_stems(env, fake_client, alias):
    with pytest.raises(ConfigError, match="invalid account alias"):
        await login_cmd.start_login(
            load_config(),
            alias,
            phone=PHONE,
            api_id=1,
            api_hash="h",
            force=False,
        )


@pytest.mark.asyncio
async def test_new_alias_accepts_valid_stem(env, fake_client):
    pending = await login_cmd.start_login(
        load_config(),
        "valid-alias_9",
        phone=PHONE,
        api_id=1,
        api_hash="h",
        force=False,
    )
    data = await _complete_phone_login(pending)
    assert data["status"] == "authorized"
    assert (env["state"] / "sessions" / "valid-alias_9.session").is_file()


@pytest.mark.asyncio
async def test_audit_fail_closed_before_attempt(env, fake_client, monkeypatch):
    calls: list[dict] = []

    def fail_on_started(action, account, details):
        calls.append(details)
        if details.get("outcome") == "started":
            raise PolicyError("audit unavailable")

    monkeypatch.setattr(login_cmd.safety, "append_audit", fail_on_started)
    with pytest.raises(PolicyError, match="audit"):
        await login_cmd.start_login(
            load_config(),
            "tmp",
            phone=PHONE,
            api_id=1,
            api_hash="h",
            force=False,
        )
    assert calls == [{"method": "phone", "outcome": "started", "phone": "+7…89"}]
    assert list((env["state"] / "logins").glob("l_*.json")) == []


@pytest.mark.asyncio
async def test_continue_password_flood_wait_keeps_attempt(
    env, fake_client, monkeypatch
):
    from telethon.errors import FloodWaitError

    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=PHONE,
        api_id=1,
        api_hash="h",
        force=False,
    )
    from telethon.errors import SessionPasswordNeededError

    fake_client._sign_in_error = SessionPasswordNeededError(request=None)
    password_pending = await login_cmd.continue_login(
        login_id=pending["login_id"], code="12345", password_stdin=False
    )
    assert password_pending["next"] == "password"
    fake_client._sign_in_error = FloodWaitError(request=None, capture=45)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(readline=lambda: "pw\n"))
    with pytest.raises(Exception) as excinfo:
        await login_cmd.continue_login(
            login_id=pending["login_id"], code=None, password_stdin=True
        )
    assert excinfo.value.exit_code == 5
    assert excinfo.value.details["retry_after"] == 45
    assert (env["state"] / "logins" / f"{pending['login_id']}.json").exists()


@pytest.mark.asyncio
async def test_continue_refuses_a_pending_removed_qr_attempt(env, fake_client):
    record = login_state.create_attempt(
        "tmp", "qr", api_id=1, api_hash="h", phone=PHONE
    )
    login_id = record["login_id"]
    login_state.staged_session_path(login_id).write_bytes(b"staged")
    with pytest.raises(Exception) as excinfo:
        await login_cmd.continue_login(
            login_id=login_id, code=None, password_stdin=False
        )
    assert excinfo.value.exit_code == 4
    assert fake_client.sign_in_calls == []


def test_continue_accepts_the_global_timeout_flag(env, capsys):
    """CONTRACT §1: --timeout is global; §10 lists no conflict with --continue."""
    code = main(
        [
            "accounts",
            "login",
            "--continue",
            "l_missing",
            "--timeout",
            "30",
            "--json",
        ]
    )
    assert code == 4
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "NOT_FOUND"


def test_continue_without_timeout_reaches_attempt_lookup(env, capsys):
    code = main(
        [
            "accounts",
            "login",
            "--continue",
            "l_missing",
            "--json",
        ]
    )
    assert code == 4
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "NOT_FOUND"


def test_qr_format_is_removed_from_the_parser(env, capsys):
    code = main(
        [
            "accounts",
            "login",
            "--continue",
            "l_abc",
            "--qr-format",
            "text",
            "--json",
        ]
    )
    assert code == 1
    assert "unrecognized arguments: --qr-format" in capsys.readouterr().err


def test_initial_login_rejects_code_without_continue(env, capsys):
    code = main(
        [
            "accounts",
            "login",
            "tmp",
            "--api-id",
            "1",
            "--api-hash",
            "h",
            "--code",
            "12345",
            "--json",
        ]
    )
    assert code == 2
