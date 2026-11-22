"""`tg setup` end to end: argv in, a real loopback page driven over HTTP, the
JSON result, the config and session files out. Telegram is a fake client
whose QR scan, cloud password and confirmation code the test controls."""

from __future__ import annotations

import asyncio
import http.client
import json
import queue
import stat
import threading
import time
import tomllib
import urllib.parse
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors, functions

from tests.fakes.snapshot import assert_snapshot
from tgcli import desktop, session as session_mod
from tgcli.cli import main
from tgcli.commands import setup as setup_cmd
from tgcli.errors import PolicyError
from tgcli.session import session_file_lock

API_ID = "1234567"
API_HASH = "0123456789abcdef0123456789abcdef"
CONFIGURED = f"""
[accounts.main]
api_id = {API_ID}
api_hash = "{API_HASH}"
session = "main"
"""


class World:
    """What Telegram knows: who logs in, and what the person does on the phone."""

    def __init__(self):
        self.me = SimpleNamespace(
            id=42, username="anna", first_name="Анна", phone="79991234589"
        )
        self.scanned = threading.Event()
        self.password: str | None = None
        self.hint = "the cat's name"
        self.code = "12345"
        self.qr_error: Exception | None = None
        self.qr_lifetime = 30.0
        self.qr_tokens = 0
        self.authorized: set[str] = set()
        self.clients: list[FakeTelegram] = []
        self.sent_codes: list[str] = []


class FakeQR:
    def __init__(self, tg: FakeTelegram):
        self.tg = tg
        self._issue()

    def _issue(self) -> None:
        self.tg.world.qr_tokens += 1
        self.url = f"tg://login?token=fake{self.tg.world.qr_tokens}"
        self.expires = datetime.now(UTC) + timedelta(seconds=self.tg.world.qr_lifetime)

    async def recreate(self):
        self._issue()

    async def wait(self, timeout=None):
        deadline = time.monotonic() + timeout
        while not self.tg.world.scanned.is_set():
            if time.monotonic() >= deadline:
                raise TimeoutError
            await asyncio.sleep(0.01)
        if self.tg.world.password:
            raise telethon_errors.SessionPasswordNeededError(request=None)
        self.tg.world.authorized.add(self.tg.path)
        return self.tg.world.me


class FakeTelegram:
    def __init__(self, world: World, path, api_id, api_hash):
        self.world = world
        self.path = str(path)
        self.api_id = api_id
        self.api_hash = api_hash
        self.connected = False
        world.clients.append(self)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    async def get_me(self):
        return self.world.me if self.path in self.world.authorized else None

    async def qr_login(self):
        if self.world.qr_error is not None:
            raise self.world.qr_error
        return FakeQR(self)

    async def send_code_request(self, phone):
        self.world.sent_codes.append(phone)
        return SimpleNamespace(phone_code_hash="hash-1")

    async def sign_in(self, phone=None, code=None, *, password=None, **kwargs):
        if password is not None:
            if password != self.world.password:
                raise telethon_errors.PasswordHashInvalidError(request=None)
        elif code != self.world.code:
            raise telethon_errors.PhoneCodeInvalidError(request=None)
        elif self.world.password:
            raise telethon_errors.SessionPasswordNeededError(request=None)
        self.world.authorized.add(self.path)
        return self.world.me

    async def __call__(self, request):
        if isinstance(request, functions.account.GetPasswordRequest):
            return SimpleNamespace(hint=self.world.hint)
        raise AssertionError(f"unexpected request {request!r}")


class Browser:
    """The person's tab: plain HTTP against the page's own URL."""

    def __init__(self, url: str):
        parts = urllib.parse.urlsplit(url)
        self.host, self.port, self.prefix = parts.hostname, parts.port, parts.path

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def state(self) -> dict:
        status, body, _ = self.request("GET", self.prefix + "state")
        assert status == 200
        return json.loads(body)

    def post(self, action: str, form: dict | None = None, **headers):
        status, body, _ = self.request(
            "POST",
            self.prefix + action,
            json.dumps(form or {}).encode(),
            {"Content-Type": "application/json", **headers},
        )
        return status, json.loads(body) if body.startswith(b"{") else None

    def wait_for(self, step: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            state = self.state()
            if state["step"] == step:
                return state
            if time.monotonic() > deadline:
                raise AssertionError(f"page is at {state['step']!r}, not {step!r}")
            time.sleep(0.02)


class Run:
    def __init__(self, thread: threading.Thread, box: dict, browser: Browser):
        self.thread, self.box, self.browser = thread, box, browser

    def exit_code(self) -> int:
        self.thread.join(timeout=20)
        assert not self.thread.is_alive(), "tg setup did not exit"
        return self.box["code"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    config = tmp_path / "config" / "config.toml"
    state = tmp_path / "state"
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    monkeypatch.setattr(setup_cmd, "LINGER_S", 2.0)
    return SimpleNamespace(tmp=tmp_path, config=config, state=state)


@pytest.fixture
def world(monkeypatch) -> World:
    world = World()
    monkeypatch.setattr(
        session_mod,
        "Client",
        lambda path, api_id, api_hash, **_: FakeTelegram(world, path, api_id, api_hash),
    )
    return world


@pytest.fixture
def opened(monkeypatch) -> queue.Queue:
    links: queue.Queue = queue.Queue()

    def open_url(url):
        links.put(url)
        return True

    monkeypatch.setattr(desktop, "open_url", open_url)
    return links


def start(argv: list[str], opened: queue.Queue) -> Run:
    box: dict = {}
    thread = threading.Thread(
        target=lambda: box.setdefault("code", main(argv)), daemon=True
    )
    thread.start()
    return Run(thread, box, Browser(opened.get(timeout=10)))


def result(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def staged_files(env) -> list[str]:
    logins = env.state / "logins"
    if not logins.is_dir():
        return []
    return sorted(p.name for p in logins.iterdir() if not p.name.endswith(".lock"))


def enter_keys(page: Browser) -> dict:
    status, state = page.post("keys", {"api_id": API_ID, "api_hash": API_HASH})
    assert status == 200
    return state


def test_page_answers_only_with_its_token_and_its_own_host(env, world, opened, capsys):
    run = start(["setup", "--json"], opened)
    page = run.browser

    assert page.request("GET", "/")[0] == 403
    assert page.request("GET", "/not-the-token/")[0] == 403
    assert (
        page.request("GET", page.prefix + "state", headers={"Host": "evil.test"})[0]
        == 403
    )
    status, body, headers = page.request("GET", page.prefix)
    assert status == 200
    assert b'"step": "keys"' in body
    assert "nonce-" in headers["Content-Security-Policy"]
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "no-store"

    assert page.post("cancel")[0] == 200
    assert run.exit_code() == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "BLOCKED"


def test_actions_take_only_a_json_post_from_the_page_itself(env, world, opened):
    run = start(["setup", "--json"], opened)
    page = run.browser

    assert page.request("GET", page.prefix + "keys")[0] == 405
    assert (
        page.request(
            "POST", page.prefix + "keys", b"api_id=1", {"Content-Type": "text/plain"}
        )[0]
        == 415
    )
    assert page.post("keys", {}, Origin="https://evil.test")[0] == 403
    assert page.post("no-such-action")[0] == 404
    assert page.post("keep")[0] == 409

    page.post("cancel")
    assert run.exit_code() == 2
    assert not env.config.exists()


def test_page_language_follows_the_browser(env, world, opened):
    run = start(["setup"], opened)
    page = run.browser

    def lang(accept):
        headers = {"Accept-Language": accept} if accept else {}
        return page.request("GET", page.prefix, headers=headers)[1]

    assert b'<html lang="ru">' in lang(None)
    assert b'<html lang="ru">' in lang("ru-RU,ru;q=0.9,en;q=0.8")
    assert b'<html lang="en">' in lang("en-US,en;q=0.9,ru;q=0.5")
    page.post("cancel")
    assert run.exit_code() == 2


@pytest.mark.parametrize(
    ("form", "field"),
    [
        ({"api_id": "12ab", "api_hash": API_HASH}, "api_id"),
        ({"api_id": API_ID, "api_hash": "not-a-hash"}, "api_hash"),
        ({"api_id": API_ID, "api_hash": API_HASH[:-1]}, "api_hash"),
    ],
)
def test_keys_are_checked_before_telegram_is_asked(env, world, opened, form, field):
    run = start(["setup"], opened)

    status, state = run.browser.post("keys", form)

    assert status == 400
    assert state["step"] == "keys"
    assert state["error"] == {"code": field}
    assert world.clients == []
    run.browser.post("cancel")
    assert run.exit_code() == 2


def test_qr_login_saves_the_account_and_prints_the_result(env, world, opened, capsys):
    run = start(["setup", "--json"], opened)
    page = run.browser

    state = enter_keys(page)
    assert state["step"] == "qr"
    assert state["qr"]["svg"].startswith("<svg")
    assert world.clients[-1].api_id == int(API_ID)
    world.scanned.set()
    done = page.wait_for("done")

    assert run.exit_code() == 0
    assert done["user"] == {"first_name": "Анна", "username": "anna"}
    output = capsys.readouterr().out
    assert API_HASH not in output
    data = json.loads(output)
    assert_snapshot("setup", json.loads(output.replace(str(env.tmp), "<tmp>")))
    assert data["status"] == "authorized"
    assert data["method"] == "qr"
    session = env.state / "sessions" / "main.session"
    assert data["session"] == str(session)
    assert session.is_file()
    assert stat.S_IMODE(session.stat().st_mode) == 0o600
    config = tomllib.loads(env.config.read_text())
    assert config["default_account"] == "main"
    assert config["accounts"]["main"] == {
        "api_id": int(API_ID),
        "api_hash": API_HASH,
        "session": "main",
    }
    assert stat.S_IMODE(env.config.stat().st_mode) == 0o600
    assert staged_files(env) == []


def test_keys_telegram_rejects_send_the_person_back(env, world, opened, capsys):
    world.qr_error = telethon_errors.ApiIdInvalidError(request=None)
    run = start(["setup", "--json"], opened)

    status, state = run.browser.post("keys", {"api_id": API_ID, "api_hash": API_HASH})

    assert status == 400
    assert state["step"] == "keys"
    assert state["error"] == {"code": "bad_keys"}
    assert staged_files(env) == []
    run.browser.post("cancel")
    assert run.exit_code() == 2
    assert not env.config.exists()


def test_cloud_password_after_the_qr_scan(env, world, opened, capsys):
    world.password = "hunter2"
    run = start(["setup", "--json"], opened)
    page = run.browser
    enter_keys(page)

    world.scanned.set()
    state = page.wait_for("password")
    assert state["hint"] == "the cat's name"
    status, state = page.post("password", {"password": "wrong"})
    assert (status, state["error"]) == (400, {"code": "bad_password"})
    status, state = page.post("password", {"password": "hunter2"})

    assert (status, state["step"]) == (200, "done")
    assert run.exit_code() == 0
    output = capsys.readouterr().out
    assert "hunter2" not in output
    assert json.loads(output)["method"] == "qr"


def test_phone_and_code_instead_of_the_qr(env, world, opened, capsys):
    run = start(["setup", "--json"], opened)
    page = run.browser
    enter_keys(page)

    assert page.post("use-phone")[1]["step"] == "phone"
    status, state = page.post("phone", {"phone": "8 (999)"})
    assert (status, state["error"]) == (400, {"code": "phone"})
    status, state = page.post("phone", {"phone": "+7 999 123-45-89"})
    assert (status, state["step"], state["phone"]) == (200, "code", "+7…89")
    assert world.sent_codes == ["+79991234589"]
    status, state = page.post("code", {"code": "00000"})
    assert (status, state["error"]) == (400, {"code": "bad_code"})
    status, state = page.post("code", {"code": "12 345"})

    assert (status, state["step"]) == (200, "done")
    assert run.exit_code() == 0
    output = capsys.readouterr().out
    assert "79991234589" not in output
    assert json.loads(output)["method"] == "phone"


def test_a_live_session_is_kept_as_it_is(env, world, opened, capsys):
    env.config.parent.mkdir(parents=True)
    env.config.write_text(CONFIGURED)
    session = env.state / "sessions" / "main.session"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"live")
    world.authorized.add(str(session))
    run = start(["setup", "--json"], opened)
    page = run.browser

    state = page.state()
    assert state["step"] == "connected"
    assert state["user"]["first_name"] == "Анна"
    status, state = page.post("keep")

    assert (status, state["step"]) == (200, "done")
    assert run.exit_code() == 0
    data = result(capsys)
    assert data["status"] == "already_authorized"
    assert data["method"] is None
    assert session.read_bytes() == b"live"
    config = tomllib.loads(env.config.read_text())
    assert config["default_account"] == "main"
    assert config["accounts"]["main"]["api_hash"] == API_HASH


def test_logging_in_again_keeps_the_old_session_as_a_backup(env, world, opened, capsys):
    env.config.parent.mkdir(parents=True)
    env.config.write_text('default_account = "main"\n' + CONFIGURED)
    session = env.state / "sessions" / "main.session"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"old")
    world.authorized.add(str(session))
    run = start(["setup", "--json"], opened)
    page = run.browser

    assert page.post("relogin")[1]["step"] == "qr"
    world.scanned.set()
    page.wait_for("done")

    assert run.exit_code() == 0
    data = result(capsys)
    assert data["backup"] == str(session) + ".bak"
    assert (env.state / "sessions" / "main.session.bak").read_bytes() == b"old"
    assert env.config.read_text().count("default_account") == 1


def test_configured_account_without_a_session_goes_straight_to_the_qr(
    env, world, opened, capsys
):
    env.config.parent.mkdir(parents=True)
    env.config.write_text('default_account = "main"\n' + CONFIGURED)
    run = start(["setup", "--json"], opened)

    assert run.browser.state()["step"] == "qr"
    world.scanned.set()
    run.browser.wait_for("done")

    assert run.exit_code() == 0
    assert result(capsys)["status"] == "authorized"
    assert tomllib.loads(env.config.read_text())["accounts"].keys() == {"main"}


def test_the_qr_code_is_renewed_before_it_expires(env, world, opened, monkeypatch):
    monkeypatch.setattr(setup_cmd, "QR_MIN_REFRESH_S", 0.05)
    world.qr_lifetime = 0.05
    run = start(["setup"], opened)
    page = run.browser
    first = enter_keys(page)["qr"]

    deadline = time.monotonic() + 5
    while page.state()["qr"]["id"] < first["id"] + 2:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    renewed = page.state()["qr"]

    assert renewed["svg"] != first["svg"]
    page.post("cancel")
    assert run.exit_code() == 2
    assert staged_files(env) == []


def test_readonly_refuses_before_any_page_opens(
    env, world, opened, capsys, monkeypatch
):
    monkeypatch.setenv("TGCLI_READONLY", "1")

    assert main(["setup", "--json"]) == 2

    assert json.loads(capsys.readouterr().out)["error"]["code"] == "BLOCKED"
    assert opened.empty()


def test_no_browser_only_prints_the_link(env, world, monkeypatch, capsys):
    monkeypatch.setattr(desktop, "open_url", lambda url: pytest.fail("opened"))
    box: dict = {}
    thread = threading.Thread(
        target=lambda: box.setdefault("code", main(["setup", "--no-browser"])),
        daemon=True,
    )
    thread.start()
    printed = ""
    deadline = time.monotonic() + 10
    while "http://127.0.0.1:" not in printed:
        assert time.monotonic() < deadline
        time.sleep(0.02)
        printed += capsys.readouterr().err
    url = printed[printed.index("http://") :].split()[0]

    Browser(url).post("cancel")
    thread.join(timeout=20)
    assert box["code"] == 2


def test_only_one_setup_runs_at_a_time(env, world, opened, capsys):
    env.state.mkdir(parents=True)
    with session_file_lock(env.state / "setup", busy_error=PolicyError):
        assert main(["setup", "--json"]) == 2

    error = json.loads(capsys.readouterr().out)["error"]
    assert "already running" in error["message"]
    assert opened.empty()


def test_setup_ends_at_the_timeout(env, world, opened, capsys):
    run = start(["setup", "--json", "--timeout", "3"], opened)
    enter_keys(run.browser)

    assert run.exit_code() == 1

    assert json.loads(capsys.readouterr().out)["error"]["code"] == "TIMEOUT"
    assert staged_files(env) == []
    assert not env.config.exists()


def test_doctor_and_a_missing_config_point_to_setup(env, capsys):
    assert main(["doctor"]) == 3
    assert "run: tg setup" in capsys.readouterr().err

    env.config.parent.mkdir(parents=True)
    env.config.write_text(CONFIGURED)
    assert main(["doctor"]) == 0
    assert "run: tg setup main" in capsys.readouterr().err


def test_doctor_does_not_send_a_busy_session_to_setup(env, capsys):
    env.config.parent.mkdir(parents=True)
    env.config.write_text(CONFIGURED)
    session = env.state / "sessions" / "main.session"
    session.parent.mkdir(parents=True)
    session.write_bytes(b"live")

    with session_file_lock(session):
        assert main(["doctor", "--connect"]) == 0

    assert "tg setup" not in capsys.readouterr().err
