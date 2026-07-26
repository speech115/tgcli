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


class CommandTimeoutError(TgcliError):
    """The `--timeout` deadline expired; same taxonomy code as a login timeout."""

    code = "TIMEOUT"


class ExportError(TgcliError):
    code = "RUNTIME"


class PartialFailure(TgcliError):
    """Command produced a result document but should exit nonzero (ADR-0032)."""

    code = "PARTIAL"

    def __init__(
        self,
        message: str,
        data: dict,
        *,
        cause: TgcliError,
        rows: list | None = None,
    ):
        super().__init__(message)
        self.data = data
        self.cause = cause
        self.exit_code = cause.exit_code
        self.rows = rows
