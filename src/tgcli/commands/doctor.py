"""Read-only environment and session health checks (ADR-0028 / ADR-0040 / ADR-0062)."""

import stat
import sys
from pathlib import Path

import telethon

from tgcli import safety, session
from tgcli.config import Config, resolve_account
from tgcli.errors import PolicyError
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


def _loose_previews() -> list[Path]:
    """Preview files whose mode is not 0600 — empty when the scan cannot run.

    A concurrent `store cleanup --confirm` can remove the directory between
    the probe and the walk, and a health report must not die of the housekeeping
    it exists to describe (review finding).
    """
    directory = safety.previews_dir()
    try:
        return [
            path
            for path in directory.iterdir()
            if path.is_file() and not _mode_ok(path)
        ]
    except OSError:
        return []


def _repair_allowed(readonly: bool) -> bool:
    """The ADR-0040 local-mutation gate, asked rather than enforced.

    `doctor` reports health, so a blocked repair is a check result and not a
    failed command — but the *rule* must stay the single one every other
    mutation site obeys. Asking `safety` is what keeps `--readonly` and
    `TGCLI_READONLY=1` one gate; a hand-rolled `if readonly` honoured only
    the flag and repaired previews under the environment variable (review
    finding).
    """
    try:
        safety.enforce_local_mutation_allowed(readonly)
    except PolicyError:
        return False
    return True


def _repair_preview_perms() -> int:
    """Force every loose preview back to 0600; return how many were tightened.

    Previews have been written 0600 since 1.1.2, but a file created before
    that stayed world-readable forever and kept `preview_perms_ok` false
    until the operator ran a *reaping* command — so the one command whose job
    is to report health was permanently red and its own remedy deleted state
    (#172). Repair is the honest reading of a check doctor already has to
    stat, and `session.restrict_file` fails open: an unfixable file is
    reported by the check that follows, never a doctor crash.
    """
    repaired = 0
    for path in _loose_previews():
        session.restrict_file(path)
        if _mode_ok(path):
            repaired += 1
    return repaired


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
        if key == "governor_cooldowns":
            # A cooldown is reportable state, not a failure.
            continue
        if key == "governor_degraded":
            # ADR-0089: an unopenable ledger fails closed for governed
            # traffic — doctor must report the account unhealthy.
            if value is True:
                return False
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
                async with session.client(account, role=role, govern=False) as tg:
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
    decision 1). An unreadable ledger reports ``governor_degraded: true``
    and sets per-account ``ok: false`` (ADR-0089) — governed traffic
    fails closed until the ledger is repaired.
    """
    from tgcli.governor.ledger import Ledger

    user_id = session.session_user_id(session_file)
    result: dict = {"governor_degraded": False, "governor_cooldowns": {}}
    if user_id is None:
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


async def check_account(
    account, *, connect: bool = False, preview_perms_repaired: int = 0
) -> dict:
    session_file = session.session_path(account)
    has_session_file = session_file.is_file()
    checks: dict = {
        "session_file": has_session_file,
        "lock_free": has_session_file and session.lock_held(session_file) is False,
        "state_writable": _state_writable(),
        "preview_perms_ok": not _loose_previews(),
        "preview_perms_repaired": preview_perms_repaired,
        "audit_perms_ok": _audit_perms_ok(),
        "session_perms_ok": _session_perms_ok(session_file),
        "state_size": _state_size(),
        "authorized": None,
    }
    # Governor keys are always present (empty when there is no session or
    # no cached user id) so the JSON schema is stable (review fix m4).
    checks.update(_governor_check(session_file))
    user = None
    if connect:
        checks["authorized"] = False
        if checks["session_file"] and checks["lock_free"]:
            try:
                async with session.client(account, govern=False) as tg:
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
    config: Config,
    alias: str | None = None,
    *,
    connect: bool = False,
    readonly: bool = False,
) -> dict:
    if alias:
        accounts = [resolve_account(config, alias)]
    else:
        accounts = list(config.accounts.values())
    # Previews are account-agnostic, so the repair runs once for the whole
    # invocation and every account report quotes the same number.
    allowed = _repair_allowed(readonly)
    repaired = _repair_preview_perms() if allowed else 0
    if repaired:
        note(f"tightened {repaired} preview file(s) to 0600")
    reports = [
        await check_account(account, connect=connect, preview_perms_repaired=repaired)
        for account in accounts
    ]
    if any(report["checks"]["preview_perms_ok"] is False for report in reports):
        note(
            "preview files are readable by other users; tighten them with: "
            + (
                "chmod 600 on the files under the previews directory"
                if allowed
                else "tg doctor with local state writes allowed "
                "(no --readonly, no TGCLI_READONLY=1)"
            )
        )
    return {
        "runtime": _runtime_fingerprint(),
        "accounts": reports,
        "ok": all(report["ok"] for report in reports),
    }


def _plain_failure_keys(checks: dict) -> list[str]:
    # governor_degraded uses inverted polarity (True = unhealthy), so it
    # must be excluded from the generic False-means-failed scan.
    failures = [
        key
        for key, value in checks.items()
        if key not in ("error", "state_size", "governor_degraded") and value is False
    ]
    if checks.get("governor_degraded") is True:
        failures.append("governor_degraded")
    return failures


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
        failures = _plain_failure_keys(report["checks"])
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
            role_failures = _plain_failure_keys(role["checks"])
            rows.append(
                (
                    f"{report['alias']}@{role['name']}",
                    role_status,
                    (role["user"] or {}).get("username"),
                    ", ".join(role_failures) or None,
                )
            )
    return rows
