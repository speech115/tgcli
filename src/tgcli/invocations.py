"""Private metadata-only JSONL journal for completed CLI invocations."""

import json
from datetime import UTC, datetime

from tgcli.output import note
from tgcli.state_paths import (
    ensure_state_dir,
    open_private_append,
    restrict_file,
    state_dir,
)


def log_invocation(
    *,
    command: str,
    exit_code: int,
    duration_ms: int,
    account: str | None = None,
    error: str | None = None,
    error_site: str | None = None,
    error_type: str | None = None,
    role: str | None = None,
    retry_after: int | None = None,
    stop_reason: str | None = None,
) -> None:
    """Append one completed invocation.

    ``retry_after`` is the wait a flood error asked for; ``stop_reason`` says
    why a run stopped normally before its work was done. ``error_site``
    (``module:function``) and ``error_type`` (an exception class name) tell
    failures apart without their text. No message text, no chat refs, no
    request parameters.
    """
    entry = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        "account": account,
        "role": role,
        "exit_code": exit_code,
        "error": error,
        "error_site": error_site,
        "error_type": error_type,
        "duration_ms": duration_ms,
        "retry_after": retry_after,
        "stop_reason": stop_reason,
    }
    path = state_dir() / "invocations.jsonl"
    try:
        ensure_state_dir()
        with open_private_append(path) as handle:
            restrict_file(path)
            handle.write(
                json.dumps(
                    {key: value for key, value in entry.items() if value is not None}
                )
                + "\n"
            )
    except OSError as exc:
        note(f"warning: invocation journal not written: {exc}")
