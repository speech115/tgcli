"""Read-only JSONL batch runner (ADR-0032)."""

from __future__ import annotations

import json
from typing import Any

from telethon import errors as telethon_errors

from tgcli import read_ops
from tgcli.errors import PolicyError, RateLimitError, TgcliError
from tgcli.formatting import mask_phones_in_text

BATCH_OP_CAP = 100

ALLOWED_OPS = read_ops.BATCH_OP_NAMES


def parse_ops(raw_lines: list[str]) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    for index, line in enumerate(raw_lines, start=1):
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PolicyError(f"batch line {index}: invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PolicyError(f"batch line {index}: op must be a JSON object")
        op = payload.get("op")
        if not isinstance(op, str) or not op:
            raise PolicyError(f"batch line {index}: missing op")
        if op not in ALLOWED_OPS:
            raise PolicyError(f"batch line {index}: op {op!r} is not allowlisted")
        ops.append(payload)
    if len(ops) > BATCH_OP_CAP:
        raise PolicyError(f"tg batch accepts at most {BATCH_OP_CAP} ops per invocation")
    return ops


async def run_batch(
    tg, ops: list[dict[str, Any]], *, fail_fast: bool = False
) -> tuple[list[dict[str, Any]], int | None]:
    """Execute ops sequentially. Returns (result lines, first failure exit code)."""
    results: list[dict[str, Any]] = []
    first_exit: int | None = None
    for payload in ops:
        op = payload["op"]
        try:
            result = await read_ops.execute(tg, read_ops.from_batch(payload))
            data = result.data
            results.append({"ok": True, "op": op, "data": data})
        except TgcliError as exc:
            results.append(
                {
                    "ok": False,
                    "op": op,
                    "error": {"code": exc.code, "message": str(exc), **exc.details},
                }
            )
            if first_exit is None:
                first_exit = exc.exit_code
            if fail_fast:
                break
        except telethon_errors.FloodWaitError as exc:
            rate = RateLimitError(
                f"rate limited for {exc.seconds}s", retry_after=exc.seconds
            )
            results.append(
                {
                    "ok": False,
                    "op": op,
                    "error": {
                        "code": rate.code,
                        "message": str(rate),
                        **rate.details,
                    },
                }
            )
            if first_exit is None:
                first_exit = rate.exit_code
            if fail_fast:
                break
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "op": op,
                    "error": {
                        "code": "RUNTIME",
                        "message": mask_phones_in_text(str(exc)),
                    },
                }
            )
            if first_exit is None:
                first_exit = TgcliError.exit_code
            if fail_fast:
                break
    return results, first_exit
