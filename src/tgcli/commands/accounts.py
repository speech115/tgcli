from tgcli.config import Config


def list_accounts(config: Config) -> dict:
    return {
        "default_account": config.default_account,
        "accounts": [
            {"alias": account.alias, "session": account.session}
            for account in config.accounts.values()
        ],
    }


def to_rows(data: dict) -> list[tuple]:
    return [(entry["alias"], entry["session"]) for entry in data["accounts"]]
