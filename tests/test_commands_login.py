"""Unit tests for accounts login (ADR-0042 Slices 2–3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
    monkeypatch.setattr(desktop, "open_url", lambda url: False)
    return {"config": config_path, "state": state}


class FakeQR:
    def __init__(self, url="tg://login?token=tok123", *, fail_password=False):
        self.url = url
        self._fail_password = fail_password
        self.recreate_calls = 0
        self.wait_calls = 0
        self._wait_behavior = "ok"
        self._refresh_expires()

    def _refresh_expires(self) -> None:
        self.expires = datetime.now(UTC) + timedelta(seconds=30)

    def set_wait(self, behavior: str):
        self._wait_behavior = behavior

    async def wait(self, timeout=None):
        self.wait_calls += 1
        if self._wait_behavior == "timeout":
            raise TimeoutError
        if self._wait_behavior == "password":
            from telethon.errors import SessionPasswordNeededError

            raise SessionPasswordNeededError(request=None)
        if self._wait_behavior == "timeout_once":
            self._wait_behavior = "ok"
            raise TimeoutError
        return SimpleNamespace(id=1)

    async def recreate(self):
        self.recreate_calls += 1
        self.url = f"tg://login?token=tok-re{self.recreate_calls}"
        self._refresh_expires()
        return None


class FakeAuthClient:
    def __init__(self):
        self.qr = FakeQR()
        self.sign_in_calls = []
        self.send_code_calls = []
        self.disconnected = False
        self._me = SimpleNamespace(id=42, username="u", phone="79991234589")
        self._send_code_error = None
        self._sign_in_error = None
        self._authorized_probe = False

    async def qr_login(self):
        return self.qr

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


def test_mask_phone_shapes():
    assert mask_phone("+79991234589") == "+7…89"
    assert mask_phone("79991234589") == "79…89"
    assert mask_phone("12") == "…12"
    assert mask_phone("") == ""
    assert mask_phone(None) == ""


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

    data = await login_cmd.start_login(
        load_config(),
        "work",
        phone=None,
        api_id=None,
        api_hash=None,
        force=True,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
    assert data["status"] == "authorized"
    assert str(old_session) == data["session"]
    assert not wrong_path.exists()
    assert (env["state"] / "sessions" / "work-real.session.bak").is_file()


@pytest.mark.asyncio
async def test_qr_happy_path_promotes(env, fake_client):
    config = load_config()
    data = await login_cmd.start_login(
        config,
        "tmp",
        phone=None,
        api_id=99,
        api_hash="newhash",
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
    assert data["status"] == "authorized"
    assert data["method"] == "qr"
    assert data["user"]["phone"] == "+7…89"
    dest = env["state"] / "sessions" / "tmp.session"
    assert dest.is_file()
    assert "tmp" in load_config().accounts
    assert fake_client.disconnected


@pytest.mark.asyncio
async def test_force_required_when_authorized(env, fake_client):
    (env["state"] / "sessions" / "main.session").write_bytes(b"live")
    fake_client._authorized_probe = True
    with pytest.raises(PolicyError, match="--force"):
        await login_cmd.start_login(
            load_config(),
            "main",
            phone=None,
            api_id=None,
            api_hash=None,
            force=False,
            timeout=30,
            qr_format="link",
            password_stdin=False,
        )


@pytest.mark.asyncio
async def test_no_probe_when_session_absent(env, fake_client, monkeypatch):
    probes = []

    async def track(account):
        probes.append(account.alias)
        return False

    monkeypatch.setattr(login_cmd.authclient, "probe_authorized", track)
    await login_cmd.start_login(
        load_config(),
        "main",
        phone=None,
        api_id=None,
        api_hash=None,
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
    assert probes == []


@pytest.mark.asyncio
async def test_qr_timeout_keeps_attempt(env, fake_client):
    fake_client.qr.set_wait("timeout")
    with pytest.raises(login_cmd.LoginTimeoutError) as excinfo:
        await login_cmd.start_login(
            load_config(),
            "tmp",
            phone=None,
            api_id=1,
            api_hash="h",
            force=False,
            timeout=0.01,
            qr_format="link",
            password_stdin=False,
        )
    assert excinfo.value.exit_code == 1
    assert "login_id" in excinfo.value.details
    assert "--continue" not in str(excinfo.value)
    login_id = excinfo.value.details["login_id"]
    assert (env["state"] / "logins" / f"{login_id}.json").exists()


@pytest.mark.asyncio
async def test_qr_recreate_on_token_expiry(env, fake_client):
    fake_client.qr.set_wait("timeout_once")
    data = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=None,
        api_id=1,
        api_hash="h",
        force=False,
        timeout=5,
        qr_format="link",
        password_stdin=False,
    )
    assert data["status"] == "authorized"
    assert fake_client.qr.recreate_calls == 1


@pytest.mark.asyncio
async def test_qr_format_text_skips_open_url(env, fake_client, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(desktop, "open_url", lambda url: opened.append(url) or True)
    monkeypatch.setattr(
        login_cmd.desktop, "open_url", lambda url: opened.append(url) or True
    )
    await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=None,
        api_id=1,
        api_hash="h",
        force=False,
        timeout=30,
        qr_format="text",
        password_stdin=False,
    )
    assert opened == []
    err = capsys.readouterr().err
    assert "tok123" in err
    assert "tg://login" not in err or "token" in err


@pytest.mark.asyncio
async def test_qr_2fa_headless_returns_pending(env, fake_client):
    fake_client.qr.set_wait("password")
    data = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=None,
        api_id=1,
        api_hash="h",
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
    assert data["status"] == "pending"
    assert data["next"] == "password"
    assert data["login_id"].startswith("l_")


@pytest.mark.asyncio
async def test_continue_password_stdin(env, fake_client, monkeypatch):
    fake_client.qr.set_wait("password")
    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=None,
        api_id=1,
        api_hash="h",
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
    monkeypatch.setattr("sys.stdin", SimpleNamespace(readline=lambda: "cloud-secret\n"))
    data = await login_cmd.continue_login(
        login_id=pending["login_id"], code=None, password_stdin=True
    )
    assert data["status"] == "authorized"
    call = fake_client.sign_in_calls[-1]
    assert call["args"] == ()
    assert call["kwargs"] == {"password": "cloud-secret"}


@pytest.mark.asyncio
async def test_wrong_password_keeps_attempt(env, fake_client, monkeypatch):
    from telethon.errors import PasswordHashInvalidError

    fake_client.qr.set_wait("password")
    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=None,
        api_id=1,
        api_hash="h",
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
    fake_client._sign_in_error = PasswordHashInvalidError(request=None)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(readline=lambda: "wrong\n"))
    with pytest.raises(ConfigError, match="password"):
        await login_cmd.continue_login(
            login_id=pending["login_id"], code=None, password_stdin=True
        )
    assert (env["state"] / "logins" / f"{pending['login_id']}.json").exists()


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
    await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=None,
        api_id=1,
        api_hash="h",
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
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
        timeout=30,
        qr_format="link",
        password_stdin=False,
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
async def test_phone_code_invalid_keeps_attempt(env, fake_client):
    from telethon.errors import PhoneCodeInvalidError

    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone="+79991234589",
        api_id=1,
        api_hash="h",
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
    fake_client._sign_in_error = PhoneCodeInvalidError(request=None)
    with pytest.raises(ConfigError, match="code"):
        await login_cmd.continue_login(
            login_id=pending["login_id"], code="00000", password_stdin=False
        )
    assert (env["state"] / "logins" / f"{pending['login_id']}.json").exists()


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
        timeout=30,
        qr_format="link",
        password_stdin=False,
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
            timeout=30,
            qr_format="link",
            password_stdin=False,
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
        timeout=30,
        qr_format="link",
        password_stdin=False,
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


def test_cli_qr_json_url_on_stderr(env, fake_client, capsys):
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
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "authorized"
    assert "tg://login" in captured.err
    assert "tg://login" not in captured.out


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
            phone=None,
            api_id=1,
            api_hash="h",
            force=False,
            timeout=30,
            qr_format="link",
            password_stdin=False,
        )


@pytest.mark.asyncio
async def test_new_alias_accepts_valid_stem(env, fake_client):
    data = await login_cmd.start_login(
        load_config(),
        "valid-alias_9",
        phone=None,
        api_id=1,
        api_hash="h",
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
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
            phone=None,
            api_id=1,
            api_hash="h",
            force=False,
            timeout=30,
            qr_format="link",
            password_stdin=False,
        )
    assert calls == [{"method": "qr", "outcome": "started"}]
    assert list((env["state"] / "logins").glob("l_*.json")) == []


@pytest.mark.asyncio
async def test_continue_password_flood_wait_keeps_attempt(
    env, fake_client, monkeypatch
):
    from telethon.errors import FloodWaitError

    fake_client.qr.set_wait("password")
    pending = await login_cmd.start_login(
        load_config(),
        "tmp",
        phone=None,
        api_id=1,
        api_hash="h",
        force=False,
        timeout=30,
        qr_format="link",
        password_stdin=False,
    )
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
async def test_continue_qr_without_password_step_rejects(env, fake_client):
    record = login_state.create_attempt("tmp", "qr", api_id=1, api_hash="h", phone=None)
    login_id = record["login_id"]
    login_state.staged_session_path(login_id).write_bytes(b"staged")
    with pytest.raises(Exception) as excinfo:
        await login_cmd.continue_login(
            login_id=login_id, code=None, password_stdin=False
        )
    assert excinfo.value.exit_code == 4
    assert fake_client.sign_in_calls == []


def test_continue_rejects_timeout(env, capsys):
    code = main(
        [
            "accounts",
            "login",
            "--continue",
            "l_abc",
            "--timeout",
            "30",
            "--json",
        ]
    )
    assert code == 2


def test_continue_rejects_qr_format(env, capsys):
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
    assert code == 2


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
