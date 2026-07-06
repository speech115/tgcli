"""Exit codes live here and nowhere else (docs/CONTRACT.md §4)."""


class TgcliError(Exception):
    exit_code = 1
    code = "RUNTIME"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.details = details


class PolicyError(TgcliError):
    exit_code = 2
    code = "BLOCKED"


class ConfigError(TgcliError):
    exit_code = 3
    code = "CONFIG"


class NotFoundError(TgcliError):
    exit_code = 4
    code = "NOT_FOUND"


class RateLimitError(TgcliError):
    exit_code = 5
    code = "FLOOD_WAIT"
