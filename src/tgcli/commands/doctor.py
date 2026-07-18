"""Read-only environment and session health checks (ADR-0028)."""

import fcntl
from pathlib import Path

from tgcli import safety, session
from tgcli.config import Config, resolve_account
from tgcli.errors import TgcliError


def _lock_free(session_file: Path) -> bool:
    try:
        handle = open(session_file.with_suffix(".lock"), "w")
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)
        return True
    except BlockingIOError:
        return False
    finally:
        handle.close()


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".doctor-probe"
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


async def check_account(account) -> dict:
    session_file = session.session_path(account)
    checks: dict = {
        "session_file": session_file.is_file(),
        "lock_free": _lock_free(session_file),
        "state_writable": _writable(safety.previews_dir()),
        "authorized": False,
    }
    user = None
    if checks["session_file"] and checks["lock_free"]:
        try:
            async with session.client(account) as tg:
                me = await tg.get_me()
                checks["authorized"] = me is not None
                if me is not None:
                    user = {
                        "id": getattr(me, "id", None),
                        "username": getattr(me, "username", None),
                        "name": getattr(me, "first_name", None),
                    }
        except (TgcliError, OSError) as exc:
            checks["error"] = str(exc)
    ok = all(value for key, value in checks.items() if key != "error")
    return {
        "alias": account.alias,
        "session": str(session_file),
        "checks": checks,
        "user": user,
        "ok": ok,
    }


async def run(config: Config, alias: str | None = None) -> dict:
    if alias:
        accounts = [resolve_account(config, alias)]
    else:
        accounts = list(config.accounts.values())
    reports = [await check_account(account) for account in accounts]
    return {"accounts": reports, "ok": all(report["ok"] for report in reports)}


def to_rows(data: dict) -> list[tuple]:
    return [
        (
            report["alias"],
            "ok" if report["ok"] else "fail",
            (report["user"] or {}).get("username"),
            ", ".join(
                key
                for key, value in report["checks"].items()
                if key != "error" and not value
            )
            or None,
        )
        for report in data["accounts"]
    ]
