"""Session files, per-account locks, Telethon client lifecycle (ADR-0004/0062)."""

import fcntl
import platform
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import IO

from telethon import TelegramClient, errors as telethon_errors

from tgcli import __version__, pace
from tgcli.config import Account, validate_role_name
from tgcli.errors import ConfigError
from tgcli.output import note
from tgcli.state_paths import create_private, ensure_state_dir, restrict_file, state_dir


def _role_stem(account: Account, role: str) -> str:
    """``<session>@<role>`` stem, reusing an on-disk casefold match when present."""
    validate_role_name(role)
    wanted = f"{account.session}@{role}".casefold()
    directory = state_dir() / "sessions"
    if directory.is_dir():
        for path in directory.glob("*.session"):
            if path.stem.casefold() == wanted:
                return path.stem
    return f"{account.session}@{role}"


def session_path(account: Account, role: str | None = None) -> Path:
    if role is None:
        stem = account.session
    else:
        stem = _role_stem(account, role)
    sessions_dir = state_dir() / "sessions"
    path = sessions_dir / f"{stem}.session"
    # Defense in depth (T07): load_config already rejects an escaping stem,
    # but an Account built directly (a future caller, a test double) must
    # not be able to make this resolve outside sessions/ either.
    if path.resolve().parent != sessions_dir.resolve():
        raise ConfigError(
            f"session {stem!r} would resolve outside the sessions directory"
        )
    return path


def list_roles(account: Account) -> list[str]:
    """Role names authorized beside the primary, sorted case-insensitively."""
    directory = state_dir() / "sessions"
    if not directory.is_dir():
        return []
    prefix = f"{account.session}@".casefold()
    roles: list[str] = []
    for path in directory.glob("*.session"):
        stem = path.stem
        if stem.casefold().startswith(prefix) and "@" in stem:
            roles.append(stem.split("@", 1)[1])
    roles.sort(key=str.casefold)
    return roles


def role_session_files(account: Account) -> dict[str, list[Path]]:
    """Every role's `.session` paths, including a role left with only its
    `.session.bak`: a promote interrupted between moving the old session to
    the backup and installing the new one leaves just the backup, which is
    still a full-access key. A case-sensitive filesystem can hold one logical
    role under several spellings of the account or the role; each is a key,
    so a role maps to all of them, keyed by its first spelling."""
    directory = state_dir() / "sessions"
    if not directory.is_dir():
        return {}
    prefix = f"{account.session}@".casefold()
    spelling: dict[str, str] = {}
    found: dict[str, list[Path]] = {}
    files = [*directory.glob("*.session"), *directory.glob("*.session.bak")]
    for path in sorted(files):
        name = path.name.removesuffix(".bak")
        stem = name.removesuffix(".session")
        if not stem.casefold().startswith(prefix):
            continue
        role = spelling.setdefault(
            stem.split("@", 1)[1].casefold(), stem.split("@", 1)[1]
        )
        paths = found.setdefault(role, [])
        if directory / name not in paths:
            paths.append(directory / name)
    return dict(sorted(found.items(), key=lambda item: item[0].casefold()))


def session_label(account: Account, role: str | None = None) -> str:
    if role is None:
        return account.session
    return _role_stem(account, role)


def lock_file_held(lock_path: Path) -> bool | None:
    """Probe whether another process holds `lock_path` right now.

    Tries LOCK_EX|LOCK_NB and releases immediately on success — never waits,
    never keeps the lock. Returns None when the probe is impossible (the lock
    path cannot be opened): `accounts show` reports that as not locked, while
    `doctor` treats an unprobeable lock as unhealthy — callers decide.
    """
    try:
        handle = lock_path.open("w")
    except OSError:
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        handle.close()


def lock_held(session_file: Path) -> bool | None:
    """Probe whether another process holds this session's lock. Side-effect
    aware: a missing session is never locked and creates no lock file
    (CONTRACT §5.1), because opening the lock path would create it.

    A held flock with no session file at all (a login in flight — see
    `_attempt_lock_held` in `commands/store.py`) is a different question with
    a different caller; this one stays scoped to "does this account's
    session look locked".
    """
    if not session_file.is_file():
        return False
    return lock_file_held(session_file.with_suffix(".lock"))


# How long a command waits for a session another tg process holds. Agents run
# in parallel; failing at once only made them retry (618 exit 3s in 30 days).
BUSY_WAIT_SECONDS = 120.0
_BUSY_POLL_SECONDS = 0.1


@contextmanager
def session_file_lock(
    session_file: Path,
    *,
    label: str | None = None,
    busy_error: type[BaseException] = ConfigError,
    wait: float = 0.0,
) -> Iterator[IO[str]]:
    """Acquire the ``.lock`` beside ``session_file`` (ADR-0099).

    ``LOCK_EX``, polling up to ``wait`` seconds while another process holds
    it. The wait counts against `--timeout` like any other time, and it never
    outlasts `--max-runtime`. Past it, raises
    ``busy_error`` (default ``ConfigError``; mutation paths pass
    ``PolicyError``). Callers must not open the flock themselves.
    """

    name = label if label is not None else session_file.stem
    remaining_runtime = pace.remaining()
    if remaining_runtime is not None:
        wait = min(wait, max(remaining_runtime, 0.0))
    deadline = time.monotonic() + wait
    announced = False
    handle = session_file.with_suffix(".lock").open("w")
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                left = deadline - time.monotonic()
                if left <= 0:
                    remedy = (
                        f"still busy after {wait:g}s"
                        if wait
                        else "retry in a few seconds"
                    )
                    raise busy_error(
                        f"session {name!r} is busy (another tg process is using "
                        f"it); {remedy}"
                    ) from exc
            if not announced:
                note(f"session {name!r} is busy; waiting up to {wait:g}s")
                announced = True
            time.sleep(min(_BUSY_POLL_SECONDS, left))
        yield handle
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


# A mutation is never retried blind: a timeout or a 500 leaves its outcome
# unknown. Telethon has no setting that resends after a flood only, and with
# no retries it would sleep the flood and raise it anyway, so a session that
# may write raises a flood at once. The call site that knows a send is safe
# to repeat resends it once through `pace.Pace.call`.
_NO_RESEND = {"request_retries": 0, "flood_sleep_threshold": 0}


def client_identity() -> tuple[str, str, str]:
    """Stable Telegram Devices identity for every tgcli connection."""
    return "tgcli", platform.system(), __version__


def build_client(path: Path, api_id: int, api_hash: str, **extra) -> TelegramClient:
    """The one TelegramClient constructor: every client, login included.

    `raise_last_call_error=True`: see TRANSIENT_ERRORS.
    """
    device_model, system_version, app_version = client_identity()
    options = {
        "flood_sleep_threshold": pace.FLOOD_SLEEP_THRESHOLD,
        "raise_last_call_error": True,
        "device_model": device_model,
        "system_version": system_version,
        "app_version": app_version,
        **extra,
    }
    return TelegramClient(str(path), api_id, api_hash, **options)


def _make_client(
    path: Path, account: Account, *, mutation_safe: bool = False
) -> TelegramClient:
    extra = _NO_RESEND if mutation_safe else {}
    return build_client(path, account.api_id, account.api_hash, **extra)


# Session files a long-lived owner keeps open (`hold`, ADR-0124), and their clients.
_held: dict[Path, TelegramClient] = {}


@asynccontextmanager
async def hold(account: Account, *, role: str | None = None):
    """Keep one session connected for the life of the block.

    Every `client()` for the same session file inside it gets this client
    instead of opening, locking and connecting the file again. The lock is held
    for the whole block, so other `tg` processes on that file wait or fail as
    they would against any long command.
    """
    path = session_path(account, role)
    if path in _held:
        raise ConfigError(f"session {session_label(account, role)!r} is already held")
    async with client(account, role=role) as tg:
        _held[path] = tg
        try:
            yield tg
        finally:
            del _held[path]


def lock_free(session_file: Path) -> bool:
    """True when a client here can open this session: no process holds its
    lock, or this one does through `hold` and `client()` reuses it."""
    return session_file in _held or lock_held(session_file) is False


@asynccontextmanager
async def _reuse(tg: TelegramClient, label: str, *, mutation_safe: bool):
    if not tg.is_connected():
        await tg.connect()
    # A fresh client per command used to drop whatever a command installed on
    # it (`tg run`'s write gate wraps `_call`); the held one must give it back.
    call = vars(tg).get("_call")
    # A held client is built for reads: a mutating command gets `_NO_RESEND`.
    retries, flood_sleep = tg._request_retries, tg.flood_sleep_threshold
    if mutation_safe:
        tg._request_retries = _NO_RESEND["request_retries"]
        tg.flood_sleep_threshold = _NO_RESEND["flood_sleep_threshold"]
    try:
        yield tg
    except telethon_errors.UnauthorizedError as exc:
        raise ConfigError(
            f"session {label!r} needs reauthentication; authorize it again"
        ) from exc
    finally:
        tg._request_retries = retries
        tg.flood_sleep_threshold = flood_sleep
        if call is None:
            vars(tg).pop("_call", None)
        else:
            tg._call = call


@asynccontextmanager
async def client(
    account: Account,
    *,
    mutation_safe: bool = False,
    role: str | None = None,
):
    path = session_path(account, role)
    label = session_label(account, role)
    if path in _held:
        # A second client would wait on the lock the holder keeps.
        async with _reuse(_held[path], label, mutation_safe=mutation_safe) as tg:
            yield tg
        return
    if role is not None and not path.is_file():
        raise ConfigError(
            f"session role {role!r} for account {account.alias!r} is not authorized; "
            f"run: tg accounts login {account.alias} --role {role} --phone PHONE"
        )
    ensure_state_dir("sessions")
    with session_file_lock(path, label=label, wait=BUSY_WAIT_SECONDS):
        create_private(path)
        tg = _make_client(path, account, mutation_safe=mutation_safe)
        # A session made before create_private existed may still be loose.
        restrict_file(path)
        try:
            await tg.connect()
            if not await tg.is_user_authorized():
                if role is not None:
                    raise ConfigError(
                        f"session role {role!r} for account {account.alias!r} "
                        "is not authorized; "
                        f"run: tg accounts login {account.alias} --role {role} "
                        "--phone PHONE"
                    )
                raise ConfigError(
                    f"session {account.session!r} is not authorized; "
                    "run: tg accounts login <alias> --phone PHONE"
                )
            yield tg
        except telethon_errors.UnauthorizedError as exc:
            # Revoked, expired, unregistered, deactivated: one remedy.
            raise ConfigError(
                f"session {label!r} needs reauthentication; authorize it again"
            ) from exc
        finally:
            await tg.disconnect()  # type: ignore  # Telethon stub: Coroutine | None
