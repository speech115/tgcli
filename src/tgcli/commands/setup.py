"""`tg setup`: log in through a page in the person's own browser.

The page is for someone who never opens a terminal: an agent runs the
command, the person reads the page. `SetupFlow` is the login as a state
machine over the steps the page shows; `tgcli.setup_web` serves it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import tomllib
from collections.abc import Awaitable, Callable, Iterator
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import segno
from telethon import errors as telethon_errors, functions

from tgcli import authclient, desktop, login_state, setup_web
from tgcli.atomic import replace_text
from tgcli.commands.accounts import _reject_colliding_alias
from tgcli.commands.login import (
    _audit_login,
    _destination_path,
    _finish_authorized,
    _request_code,
    _session_stem,
    _sign_in_password,
    _user_dict,
)
from tgcli.config import (
    Account,
    Config,
    default_config_path,
    load_config,
    validate_alias,
)
from tgcli.errors import ConfigError, PolicyError, RateLimitError
from tgcli.formatting import mask_phone
from tgcli.output import note
from tgcli.session import session_file_lock
from tgcli.state_paths import ensure_state_dir
from tgcli.tg_errors import FLOOD_ERRORS

Step = Literal[
    "keys", "connected", "qr", "phone", "code", "password", "done", "cancelled"
]
Outcome = Literal["ok", "invalid", "conflict", "unknown", "failed"]
FINAL_STEPS: frozenset[str] = frozenset({"done", "cancelled"})
QR_MIN_REFRESH_S = 5.0
LINGER_S = 10.0
_API_HASH = re.compile(r"[0-9a-f]{32}")
_PHONE = re.compile(r"\+?[0-9]{6,15}")


class FormError(Exception):
    """Something the person can fix on the page; shown there, not raised."""

    def __init__(self, code: str, **details: Any):
        super().__init__(code)
        self.code = code
        self.details = details


class _SetupBusy(Exception):
    """Another `tg setup` holds the setup lock."""


@contextlib.contextmanager
def _telegram_errors() -> Iterator[None]:
    try:
        yield
    except FLOOD_ERRORS as exc:
        raise FormError("flood", retry_after=exc.seconds) from exc
    except RateLimitError as exc:
        raise FormError("flood", retry_after=exc.details.get("retry_after")) from exc
    except telethon_errors.ApiIdInvalidError as exc:
        raise FormError("bad_keys") from exc
    except telethon_errors.RPCError as exc:
        raise FormError("telegram", detail=exc.message) from exc
    except (ConnectionError, OSError) as exc:
        raise FormError("network") from exc


def parse_keys(form: dict) -> tuple[int, str]:
    raw_id = str(form.get("api_id") or "").strip()
    raw_hash = str(form.get("api_hash") or "").strip().lower()
    if not raw_id.isdigit() or not 0 < int(raw_id) < 2**31:
        raise FormError("api_id")
    if not _API_HASH.fullmatch(raw_hash):
        raise FormError("api_hash")
    return int(raw_id), raw_hash


def parse_phone(raw: object) -> str:
    phone = re.sub(r"[\s().-]", "", str(raw or ""))
    if not _PHONE.fullmatch(phone):
        raise FormError("phone")
    return phone if phone.startswith("+") else f"+{phone}"


def _qr_svg(url: str) -> str:
    return segno.make_qr(url, error="m").svg_inline(
        omitsize=True, border=4, dark="#0b1520", svgclass="qr-code", lineclass=None
    )


def _home(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def _ensure_default_account(config_path: Path, alias: str) -> None:
    """Give a config with no `default_account` this one, so every other
    command works without `--account`. A top-level key goes before the
    first table, so it is prepended, never appended."""
    text = config_path.read_text()
    if "default_account" in tomllib.loads(text):
        return
    updated = f"default_account = {json.dumps(alias)}\n{text}"
    if tomllib.loads(updated).get("default_account") != alias:
        raise ConfigError(f"could not set default_account in {config_path}")
    replace_text(config_path, updated)


async def _signed_in_user(account: Account, session_file: Path) -> dict | None:
    if not session_file.is_file():
        return None
    async with authclient.unauthorized_client(
        session_file, account.api_id, account.api_hash
    ) as tg:
        me = await tg.get_me()
    return _user_dict(me) if me is not None else None


class SetupFlow:
    """One login, from api keys to an authorized session, as the page sees it.

    Actions arrive from the page one at a time (`act` holds a lock); the QR
    watcher is the only other writer and takes the same lock. Each attempt
    owns one staged session and one connected client until it is promoted
    or discarded.
    """

    def __init__(self, alias: str, config: Config):
        self.alias = alias
        self.step: Step = "keys"
        self.error: dict | None = None
        self.user: dict | None = None
        self.hint: str | None = None
        self.finished = asyncio.Event()
        self._account = config.accounts.get(alias)
        self._keys: tuple[int, str] | None = (
            (self._account.api_id, self._account.api_hash) if self._account else None
        )
        self._is_new = self._account is None
        self._session_file = _destination_path(_session_stem(config, alias))
        self._lock = asyncio.Lock()
        self._client: Any = None
        self._stack: AsyncExitStack | None = None
        self._login_id: str | None = None
        self._watcher: asyncio.Task | None = None
        self._qr_id = 0
        self._qr_svg: str | None = None
        self._qr_expires: datetime | None = None
        self._phone: str | None = None
        self._phone_code_hash: str | None = None
        self._method = "qr"
        self._result: dict | None = None
        self._failure: BaseException | None = None

    async def start(self) -> None:
        if self._account is None:
            return
        self.user = await _signed_in_user(self._account, self._session_file)
        if self.user is not None:
            self.step = "connected"
            return
        try:
            await self._begin_configured_qr()
        except FormError as exc:
            self._show_error(exc)

    def view(self) -> dict:
        view: dict = {
            "step": self.step,
            "alias": self.alias,
            "error": self.error,
            "paths": {
                "config": _home(default_config_path()),
                "session": _home(self._session_file),
            },
        }
        if self.step == "qr" and self._qr_svg is not None and self._qr_expires:
            left = (self._qr_expires - datetime.now(UTC)).total_seconds()
            view["qr"] = {
                "id": self._qr_id,
                "svg": self._qr_svg,
                "expires_in": max(0, round(left)),
            }
        if self.step == "code":
            view["phone"] = mask_phone(self._phone)
        if self.step == "password":
            view["hint"] = self.hint
        if self.user is not None:
            view["user"] = {
                "first_name": self.user.get("first_name"),
                "username": self.user.get("username"),
            }
        return view

    async def act(self, action: str, form: dict) -> Outcome:
        """Run one page action. `invalid` leaves the reason in `error`;
        `failed` ends the flow with the exception `outcome` re-raises."""
        entry = TRANSITIONS.get(action)
        if entry is None:
            return "unknown"
        allowed, handler = entry
        async with self._lock:
            if self.step not in allowed:
                return "conflict"
            self.error = None
            try:
                await handler(self, form)
            except FormError as exc:
                self._show_error(exc)
                return "invalid"
            except Exception as exc:
                self._fail(exc)
                return "failed"
        return "ok"

    def is_final(self) -> bool:
        return self.step in FINAL_STEPS

    def outcome(self) -> dict:
        if self._failure is not None:
            raise self._failure
        if self._result is None:
            raise PolicyError("setup was cancelled in the browser; nothing was saved")
        return self._result

    async def close(self) -> None:
        await self._stop_watcher()
        await self._close_attempt()

    def _show_error(self, exc: FormError) -> None:
        self.error = {"code": exc.code, **exc.details}

    def _fail(self, exc: BaseException) -> None:
        self._failure = exc
        self.finished.set()

    async def _open_attempt(self) -> None:
        assert self._keys is not None
        api_id, api_hash = self._keys
        attempt = login_state.create_attempt(
            self.alias, "qr", api_id=api_id, api_hash=api_hash, is_new=self._is_new
        )
        login_id: str = attempt["login_id"]
        self._login_id = login_id
        stack = AsyncExitStack()
        staged = login_state.staged_session_path(login_id)
        try:
            self._client = await stack.enter_async_context(
                authclient.unauthorized_client(staged, api_id, api_hash)
            )
        except BaseException:
            await stack.aclose()
            await self._close_attempt()
            raise
        self._stack = stack

    async def _close_attempt(self) -> None:
        stack, self._stack, self._client = self._stack, None, None
        if stack is not None:
            await stack.aclose()
        login_id, self._login_id = self._login_id, None
        if login_id is not None:
            login_state.discard_attempt(login_id)

    async def _ensure_client(self) -> None:
        if self._client is None:
            with _telegram_errors():
                await self._open_attempt()

    async def _stop_watcher(self) -> None:
        task, self._watcher = self._watcher, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _begin_qr(self) -> None:
        await self._stop_watcher()
        self.step = "qr"
        self._qr_svg = None
        await self._ensure_client()
        _audit_login(self.alias, method="qr", outcome="started")
        with _telegram_errors():
            qr = await self._client.qr_login()
        self._show_qr(qr)
        self._watcher = asyncio.create_task(self._watch_qr(qr))

    async def _begin_configured_qr(self) -> None:
        try:
            await self._begin_qr()
        except FormError as exc:
            if exc.code == "bad_keys":
                raise ConfigError(
                    f"Telegram rejected the api_id/api_hash of account "
                    f"{self.alias!r}; fix them in {default_config_path()}"
                ) from exc
            raise

    def _show_qr(self, qr) -> None:
        self._qr_id += 1
        self._qr_svg = _qr_svg(qr.url)
        self._qr_expires = qr.expires

    async def _watch_qr(self, qr) -> None:
        """Wait for the scan, then finish under the lock. Only the Telegram
        calls map to page errors: a local failure after Telegram accepted
        the scan ends the flow, since the client is already disconnected."""
        try:
            with _telegram_errors():
                needs_password = await self._await_scan(qr)
            async with self._lock:
                if self.step != "qr":
                    return
                if needs_password:
                    await self._need_password("qr")
                else:
                    await self._authorized("qr")
        except FormError as exc:
            self._qr_svg = None
            self._show_error(exc)
        except Exception as exc:
            self._fail(exc)

    async def _await_scan(self, qr) -> bool:
        """True when the account needs its cloud password next."""
        while True:
            left = (qr.expires - datetime.now(UTC)).total_seconds()
            try:
                await qr.wait(timeout=max(left, QR_MIN_REFRESH_S))
            except TimeoutError:
                async with self._lock:
                    await qr.recreate()
                    self._show_qr(qr)
                continue
            except telethon_errors.SessionPasswordNeededError:
                return True
            return False

    async def _need_password(self, method: str) -> None:
        self._method = method
        try:
            password = await self._client(functions.account.GetPasswordRequest())
            self.hint = getattr(password, "hint", None) or None
        except (telethon_errors.RPCError, ConnectionError):
            self.hint = None
        self.step = "password"

    async def _authorized(self, method: str) -> None:
        assert self._keys is not None and self._login_id is not None
        if self._is_new:
            self._refuse_if_added_meanwhile()
        api_id, api_hash = self._keys
        result = await _finish_authorized(
            self._client,
            login_id=self._login_id,
            alias=self.alias,
            method=method,
            phone=self._phone if method == "phone" else None,
            is_new=self._is_new,
            api_id=api_id,
            api_hash=api_hash,
            dest=self._session_file,
            keep_backup=self._session_file.exists(),
        )
        self._login_id = None
        _ensure_default_account(default_config_path(), self.alias)
        self._finish("authorized", method, result["user"], result["backup"])

    def _refuse_if_added_meanwhile(self) -> None:
        """Another login may have set up this alias while the page was open;
        promoting now would replace that session before the config refuses."""
        if self.alias in _existing_config().accounts:
            raise ConfigError(
                f"account {self.alias!r} was set up by another login while this "
                "page was open; nothing was replaced"
            )
        _reject_colliding_alias(default_config_path(), self.alias)

    def _finish(
        self, status: str, method: str | None, user: dict | None, backup: str | None
    ) -> None:
        self.user = user
        self._result = {
            "alias": self.alias,
            "status": status,
            "method": method,
            "user": user,
            "session": str(self._session_file),
            "config": str(default_config_path()),
            "backup": backup,
        }
        self.step = "done"
        self.finished.set()

    async def _submit_keys(self, form: dict) -> None:
        self._keys = parse_keys(form)
        try:
            await self._begin_qr()
        except FormError:
            await self._close_attempt()
            self._keys = None
            self.step = "keys"
            raise

    async def _restart_qr(self, form: dict) -> None:
        await self._begin_qr()

    async def _use_phone(self, form: dict) -> None:
        await self._stop_watcher()
        self._qr_svg = None
        self.step = "phone"

    async def _submit_phone(self, form: dict) -> None:
        phone = parse_phone(form.get("phone"))
        await self._ensure_client()
        _audit_login(self.alias, method="phone", outcome="started", phone=phone)
        with _telegram_errors():
            try:
                phone_code_hash = await _request_code(self._client, phone)
            except telethon_errors.PhoneNumberInvalidError as exc:
                raise FormError("bad_phone") from exc
            except telethon_errors.PhoneNumberBannedError as exc:
                raise FormError("banned") from exc
        self._phone, self._phone_code_hash = phone, phone_code_hash
        self.step = "code"

    async def _submit_code(self, form: dict) -> None:
        code = re.sub(r"\D", "", str(form.get("code") or ""))
        if not code:
            raise FormError("code")
        with _telegram_errors():
            try:
                await self._client.sign_in(
                    self._phone, code, phone_code_hash=self._phone_code_hash
                )
            except telethon_errors.SessionPasswordNeededError:
                await self._need_password("phone")
                return
            except (
                telethon_errors.PhoneCodeInvalidError,
                telethon_errors.PhoneCodeEmptyError,
            ) as exc:
                raise FormError("bad_code") from exc
            except telethon_errors.PhoneCodeExpiredError as exc:
                self.step = "phone"
                raise FormError("code_expired") from exc
        await self._authorized("phone")

    async def _submit_password(self, form: dict) -> None:
        password = form.get("password")
        if not isinstance(password, str) or not password:
            raise FormError("password")
        with _telegram_errors():
            try:
                await _sign_in_password(self._client, password)
            except telethon_errors.PasswordHashInvalidError as exc:
                raise FormError("bad_password") from exc
        await self._authorized(self._method)

    async def _keep(self, form: dict) -> None:
        _ensure_default_account(default_config_path(), self.alias)
        self._finish("already_authorized", None, self.user, None)

    async def _relogin(self, form: dict) -> None:
        await self._begin_configured_qr()

    async def _cancel(self, form: dict) -> None:
        await self._stop_watcher()
        self.step = "cancelled"
        self.finished.set()


Handler = Callable[[SetupFlow, dict], Awaitable[None]]
_LIVE: frozenset[str] = frozenset(
    {"keys", "connected", "qr", "phone", "code", "password"}
)
TRANSITIONS: dict[str, tuple[frozenset[str], Handler]] = {
    "keys": (frozenset({"keys"}), SetupFlow._submit_keys),
    "qr": (frozenset({"qr", "phone", "code"}), SetupFlow._restart_qr),
    "use-phone": (frozenset({"qr", "code"}), SetupFlow._use_phone),
    "phone": (frozenset({"phone"}), SetupFlow._submit_phone),
    "code": (frozenset({"code"}), SetupFlow._submit_code),
    "password": (frozenset({"password"}), SetupFlow._submit_password),
    "keep": (frozenset({"connected"}), SetupFlow._keep),
    "relogin": (frozenset({"connected"}), SetupFlow._relogin),
    "cancel": (_LIVE, SetupFlow._cancel),
}


def _existing_config() -> Config:
    if default_config_path().exists():
        return load_config()
    return Config(default_account=None, accounts={})


def resolve_alias(alias: str | None) -> str:
    """The alias asked for, else the config default, else `main`: the first
    account of a new person is `main`."""
    return alias or _existing_config().default_account or "main"


async def run(alias: str, *, open_browser: bool, timeout: float | None) -> dict:
    config = _existing_config()
    validate_alias(alias)
    if alias not in config.accounts:
        _reject_colliding_alias(default_config_path(), alias)
    try:
        with session_file_lock(
            ensure_state_dir() / "setup", label="setup", busy_error=_SetupBusy
        ):
            return await _serve(SetupFlow(alias, config), open_browser, timeout)
    except _SetupBusy as exc:
        raise PolicyError(
            "another tg setup is already running; finish or close it first"
        ) from exc


async def _serve(flow: SetupFlow, open_browser: bool, timeout: float | None) -> dict:
    try:
        await flow.start()
        async with setup_web.serve(flow) as site:
            _announce(site.url, open_browser=open_browser, timeout=timeout)
            await flow.finished.wait()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(site.final_delivered.wait(), LINGER_S)
    finally:
        await flow.close()
    return flow.outcome()


def _announce(url: str, *, open_browser: bool, timeout: float | None) -> None:
    note(f"tg setup: the person finishes the login on this page: {url}")
    if open_browser and not desktop.open_url(url):
        note("could not open a browser; open the link above in one")
    if timeout is not None:
        note(f"waiting up to {timeout / 60:g} min for the page to finish")


def rows(data: dict) -> list[tuple]:
    return [
        (
            data["alias"],
            data["status"],
            data["method"],
            (data["user"] or {}).get("username"),
            data["session"],
        )
    ]
