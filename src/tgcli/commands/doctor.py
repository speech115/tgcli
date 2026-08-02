"""Read-only environment and session health checks (ADR-0028 / ADR-0040 / ADR-0062)."""

import stat
import sys
from pathlib import Path

import telethon

from tgcli import safety, session
from tgcli.config import Config, resolve_account
from tgcli.output import note


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
    return not bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


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


def _session_perms_ok(session_file: Path) -> bool:
    """Missing files are ok; a loose .session or .session.bak is not."""
    backup = Path(str(session_file) + ".bak")
    return _mode_ok(session_file) and _mode_ok(backup)


def _state_writable() -> bool:
    try:
        return _writable(session.ensure_state_dir("previews"))
    except OSError:
        return False


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


def _runtime_fingerprint() -> dict[str, str]:
    """Identify the interpreter and Telethon build running this command."""
    return {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "telethon": telethon.__version__,
    }


def _local_ok(checks: dict) -> bool:
    for key, value in checks.items():
        if key in ("error", "authorized", "state_size"):
            continue
        if key in ("governor_cooldowns", "governor_degraded"):
            # A cooldown is reportable state, not a failure; a degraded
            # ledger is a warning the governor already fails open on.
            continue
        if value is False:
            return False
    return True


async def _check_role(account, role: str, *, connect: bool) -> dict:
    session_file = session.session_path(account, role=role)
    has_session_file = session_file.is_file()
    checks: dict = {
        "session_file": has_session_file,
        "lock_free": has_session_file and session.lock_held(session_file) is False,
        "session_perms_ok": _session_perms_ok(session_file),
        "authorized": None,
    }
    user = None
    if connect:
        checks["authorized"] = False
        if checks["session_file"] and checks["lock_free"]:
            try:
                async with session.client(account, role=role) as tg:
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
        "name": role,
        "session": str(session_file),
        "checks": checks,
        "user": user,
        "ok": ok,
    }


def _governor_check(session_file: Path) -> dict:
    """Active governor cooldowns for this session's account, or None.

    Reads the ledger directly, no RPC: `doctor` is the one command that
    must work precisely when everything else is refusing (ADR-0072
    decision 1). An unreadable ledger reports ``governor_degraded`` rather
    than failing the check — the governor failing open is the design.
    """
    from tgcli.governor.ledger import Ledger

    user_id = session.session_user_id(session_file)
    result: dict = {"governor_degraded": False}
    if user_id is None:
        result["governor_cooldowns"] = None
        return result
    with Ledger.open() as ledger:
        result["governor_degraded"] = ledger.degraded
        result["governor_cooldowns"] = {
            request_type: deadline.isoformat()
            for request_type, deadline in sorted(
                ledger.active_cooldowns(user_id).items()
            )
        }
    return result


async def check_account(account, *, connect: bool = False) -> dict:
    session_file = session.session_path(account)
    has_session_file = session_file.is_file()
    checks: dict = {
        "session_file": has_session_file,
        "lock_free": has_session_file and session.lock_held(session_file) is False,
        "state_writable": _state_writable(),
        "preview_perms_ok": _preview_perms_ok(),
        "audit_perms_ok": _audit_perms_ok(),
        "session_perms_ok": _session_perms_ok(session_file),
        "state_size": _state_size(),
        "authorized": None,
    }
    if has_session_file:
        checks.update(_governor_check(session_file))
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
    roles = [
        await _check_role(account, role, connect=connect)
        for role in session.list_roles(account)
    ]
    ok = _local_ok(checks) and all(role["ok"] for role in roles)
    if connect:
        ok = ok and bool(checks["authorized"])
    return {
        "alias": account.alias,
        "session": str(session_file),
        "checks": checks,
        "user": user,
        "roles": roles,
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
    return {
        "runtime": _runtime_fingerprint(),
        "accounts": reports,
        "ok": all(report["ok"] for report in reports),
    }


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
        for role in report.get("roles") or []:
            role_auth = role["checks"].get("authorized")
            if not role["ok"]:
                role_status = "fail"
            elif role_auth is None:
                role_status = "unknown"
            else:
                role_status = "ok"
            role_failures = [
                key
                for key, value in role["checks"].items()
                if key not in ("error", "state_size") and value is False
            ]
            rows.append(
                (
                    f"{report['alias']}@{role['name']}",
                    role_status,
                    (role["user"] or {}).get("username"),
                    ", ".join(role_failures) or None,
                )
            )
    return rows
