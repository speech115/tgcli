"""Read-only environment and session health checks (ADR-0028 / ADR-0040)."""

import stat
from pathlib import Path

from tgcli import safety, session
from tgcli.output import note
from tgcli.config import Config, resolve_account


def _writable(directory: Path) -> bool:
    probe = directory / ".doctor-probe"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe.write_text("")
    except OSError:
        return False
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return True


def _mode_ok(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except OSError:
        return True
    return not bool(mode & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH))


def _preview_perms_ok() -> bool:
    directory = safety.previews_dir()
    if not directory.is_dir():
        return True
    return all(_mode_ok(path) for path in directory.iterdir() if path.is_file())


def _audit_perms_ok() -> bool:
    path = safety.audit_path()
    if not path.exists():
        return True
    return _mode_ok(path)


def _state_size() -> int:
    root = session.state_dir()
    if not root.exists():
        return 0
    total = 0
    for entry in root.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total


def _local_ok(checks: dict) -> bool:
    for key, value in checks.items():
        if key in ("error", "authorized", "state_size"):
            continue
        if value is False:
            return False
    return True


async def check_account(account, *, connect: bool = False) -> dict:
    session_file = session.session_path(account)
    has_session_file = session_file.is_file()
    checks: dict = {
        "session_file": has_session_file,
        "lock_free": has_session_file and not session.lock_held(session_file),
        "state_writable": _writable(safety.previews_dir()),
        "preview_perms_ok": _preview_perms_ok(),
        "audit_perms_ok": _audit_perms_ok(),
        "state_size": _state_size(),
        "authorized": None,
    }
    user = None
    if connect:
        checks["authorized"] = False
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
            except Exception as exc:
                checks["error"] = str(exc)
    ok = _local_ok(checks)
    if connect:
        ok = ok and bool(checks["authorized"])
    return {
        "alias": account.alias,
        "session": str(session_file),
        "checks": checks,
        "user": user,
        "ok": ok,
    }


async def run(
    config: Config, alias: str | None = None, *, connect: bool = False
) -> dict:
    if alias:
        accounts = [resolve_account(config, alias)]
    else:
        accounts = list(config.accounts.values())
    reports = [await check_account(account, connect=connect) for account in accounts]
    if any(report["checks"]["preview_perms_ok"] is False for report in reports):
        note(
            "preview files are readable by other users; tighten them with: "
            "tg store cleanup --confirm"
        )
    return {"accounts": reports, "ok": all(report["ok"] for report in reports)}


def to_rows(data: dict) -> list[tuple]:
    rows = []
    for report in data["accounts"]:
        authorized = report["checks"].get("authorized")
        if not report["ok"]:
            status = "fail"
        elif authorized is None:
            status = "unknown"
        else:
            status = "ok"
        failures = [
            key
            for key, value in report["checks"].items()
            if key not in ("error", "state_size") and value is False
        ]
        rows.append(
            (
                report["alias"],
                status,
                (report["user"] or {}).get("username"),
                ", ".join(failures) or None,
            )
        )
    return rows
