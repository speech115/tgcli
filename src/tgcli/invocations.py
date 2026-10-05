"""Private metadata-only JSONL journal for completed CLI invocations."""

import json
from datetime import UTC, datetime

from tgcli.output import note
from tgcli.session import ensure_state_dir, restrict_file, state_dir


def log_invocation(
    *,
    command: str,
    exit_code: int,
    duration_ms: int,
    account: str | None = None,
    error: str | None = None,
    role: str | None = None,
    retry_after: int | None = None,
    request_type: str | None = None,
    provenance: str | None = None,
    stop_reason: str | None = None,
    governed_sleep_ms: int | None = None,
    request_count: int | None = None,
    lane: str | None = None,
    selected: int | None = None,
    completed: int | None = None,
    deferred: int | None = None,
    failed: int | None = None,
    cancelled: int | None = None,
) -> None:
    """Append one completed invocation.

    ``retry_after``, ``request_type``, ``provenance``, ``stop_reason``,
    ``governed_sleep_ms`` and ``request_count`` come from the governor's
    per-invocation accounting (ADR-0072 decision 4, plan phase 6). The lane and
    aggregate counters describe a jobs runner without storing a key, target,
    spec, result, or error (ADR-0087). No message text, no chat refs, no raw API
    parameters — a request *type* is not a chat reference.
    """
    entry = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": command,
        "account": account,
        "role": role,
        "exit_code": exit_code,
        "error": error,
        "duration_ms": duration_ms,
        "retry_after": retry_after,
        "request_type": request_type,
        "provenance": provenance,
        "stop_reason": stop_reason,
        "governed_sleep_ms": governed_sleep_ms,
        "request_count": request_count,
        "lane": lane,
        "selected": selected,
        "completed": completed,
        "deferred": deferred,
        "failed": failed,
        "cancelled": cancelled,
    }
    path = state_dir() / "invocations.jsonl"
    try:
        ensure_state_dir()
        with path.open("a") as handle:
            restrict_file(path)
            handle.write(
                json.dumps(
                    {key: value for key, value in entry.items() if value is not None}
                )
                + "\n"
            )
    except OSError as exc:
        note(f"warning: invocation journal not written: {exc}")
