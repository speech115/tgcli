import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tgcli.errors import ConfigError

_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]+$")
RESERVED_ROLE = "primary"


def default_config_path() -> Path:
    return Path(
        os.environ.get("TGCLI_CONFIG", "~/.config/tgcli/config.toml")
    ).expanduser()


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


def validate_role_name(role: str) -> str:
    """Alias-grade role names; ``primary`` is reserved (ADR-0062)."""
    if not role or not _ALIAS_RE.match(role):
        raise ConfigError(
            f"invalid session role {role!r}; "
            "use letters, digits, underscore, or hyphen only"
        )
    if role.casefold() == RESERVED_ROLE:
        raise ConfigError(
            "session role 'primary' is reserved for the default session; "
            "omit --session-role to use it"
        )
    return role


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw = tomllib.loads(path.read_text())
    accounts: dict[str, Account] = {}
    raw_accounts = raw.get("accounts", {})
    if not isinstance(raw_accounts, dict):
        raise ConfigError("accounts must be a table of [accounts.<alias>] entries")
    for alias, entry in raw_accounts.items():
        if not isinstance(entry, dict):
            raise ConfigError(
                f"account {alias!r} must be an [accounts.{alias}] table "
                "with api_id and api_hash"
            )
        try:
            accounts[alias] = Account(
                alias=alias,
                api_id=int(entry["api_id"]),
                api_hash=str(entry["api_hash"]),
                session=str(entry.get("session", alias)),
            )
        except KeyError as exc:
            raise ConfigError(f"account {alias!r}: missing key {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"account {alias!r}: invalid value: {exc}") from exc
    _reject_colliding_sessions(accounts)
    return Config(default_account=raw.get("default_account"), accounts=accounts)


def _reject_colliding_sessions(accounts: dict[str, Account]) -> None:
    """Fail closed when two accounts share one session file (ADR-0042).

    macOS is a first-class target and its filesystem is case-insensitive, so
    `Work.session` and `work.session` are the same file: two Telegram
    accounts would share one authorization and whichever logs in last wins.
    Compared under casefold() rather than probed on disk so the same config
    is rejected everywhere.
    """
    seen: dict[str, str] = {}
    for alias, account in accounts.items():
        key = account.session.casefold()
        other = seen.get(key)
        if other is not None:
            raise ConfigError(
                f"accounts {other!r} and {alias!r} share one session file: "
                f"{accounts[other].session!r} and {account.session!r} differ "
                "only by case, which is the same file on a case-insensitive "
                "filesystem (macOS); give one of them a distinct session ="
            )
        seen[key] = alias


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
