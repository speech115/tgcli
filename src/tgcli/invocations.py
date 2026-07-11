"""Private metadata-only JSONL journal for completed CLI invocations."""

import json
import sys
from datetime import UTC, datetime

from tgcli.session import state_dir


def log_invocation(
    *,
    command: str,
    exit_code: int,
    duration_ms: int,
    account: str | None = None,
    error: str | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        "account": account,
        "exit_code": exit_code,
        "error": error,
        "duration_ms": duration_ms,
    }
    path = state_dir() / "invocations.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps({key: value for key, value in entry.items() if value is not None}) + "\n")
    except OSError as exc:
        print(f"warning: invocation journal not written: {exc}", file=sys.stderr)
