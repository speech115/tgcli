import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tgcli.errors import ConfigError


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


def load_config(path: Path | None = None) -> Config:
    path = path or default_config_path()
    if not path.exists():
        raise ConfigError(f"config not found: {path}")
    raw = tomllib.loads(path.read_text())
    accounts: dict[str, Account] = {}
    for alias, entry in raw.get("accounts", {}).items():
        try:
            accounts[alias] = Account(
                alias=alias,
                api_id=int(entry["api_id"]),
                api_hash=str(entry["api_hash"]),
                session=str(entry.get("session", alias)),
            )
        except KeyError as exc:
            raise ConfigError(f"account {alias!r}: missing key {exc}") from exc
    return Config(default_account=raw.get("default_account"), accounts=accounts)


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
