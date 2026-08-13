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
    archive_root: Path | None = None


def validate_alias(alias: str) -> str:
    """Charset an account alias must satisfy, on login and on every load.

    Historically this regex was only enforced by ``accounts login`` for a
    brand-new alias (`commands/login.py`); an alias typed straight into
    ``config.toml`` skipped it entirely. That let a stray key like
    ``[accounts."a b"]`` or ``[accounts."x@y"]`` load successfully and only
    fail later, confusingly, wherever the alias was next used (T25).
    """
    if not alias or not _ALIAS_RE.match(alias):
        raise ConfigError(
            f"invalid account alias {alias!r}; "
            "use letters, digits, underscore, or hyphen only"
        )
    return alias


def validate_session_stem(session: str, alias: str) -> str:
    """Reject a ``session`` stem that could resolve outside ``sessions/``.

    ``session_path`` joins this stem straight onto
    ``state_dir()/sessions/<stem>.session``: a stem with a path separator
    or ``..`` escapes that directory, an absolute path ignores it outright,
    a leading ``-`` risks being read as a flag by anything that shells out
    to it, ``@`` collides with the role-suffix separator (T25), and a null
    byte is rejected by the OS anyway but with a much uglier error (T07).
    """
    if not session:
        raise ConfigError(f"account {alias!r}: session must not be empty")
    if "\x00" in session:
        raise ConfigError(f"account {alias!r}: session {session!r} has a null byte")
    for bad in ("/", "\\", ".."):
        if bad in session:
            raise ConfigError(
                f"account {alias!r}: session {session!r} must not contain {bad!r} "
                "(would escape the sessions directory)"
            )
    if session.startswith("-"):
        raise ConfigError(
            f"account {alias!r}: session {session!r} must not start with '-'"
        )
    if "@" in session:
        raise ConfigError(
            f"account {alias!r}: session {session!r} must not contain '@' "
            "(reserved for role suffixes)"
        )
    return session


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
        validate_alias(alias)
        if not isinstance(entry, dict):
            raise ConfigError(
                f"account {alias!r} must be an [accounts.{alias}] table "
                "with api_id and api_hash"
            )
        try:
            session_stem = validate_session_stem(
                str(entry.get("session", alias)), alias
            )
            accounts[alias] = Account(
                alias=alias,
                api_id=int(entry["api_id"]),
                api_hash=str(entry["api_hash"]),
                session=session_stem,
            )
        except KeyError as exc:
            raise ConfigError(f"account {alias!r}: missing key {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"account {alias!r}: invalid value: {exc}") from exc
    _reject_colliding_sessions(accounts)
    archive_root = _parse_archive_root(raw.get("archive"))
    return Config(
        default_account=raw.get("default_account"),
        accounts=accounts,
        archive_root=archive_root,
    )


def _parse_archive_root(raw_archive: object) -> Path | None:
    if raw_archive is None:
        return None
    if not isinstance(raw_archive, dict):
        raise ConfigError("archive must be an [archive] table")
    if "root" not in raw_archive:
        return None
    root = raw_archive["root"]
    if not isinstance(root, str) or not root.strip():
        raise ConfigError("archive.root must be a non-empty path string")
    return Path(root).expanduser()


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
