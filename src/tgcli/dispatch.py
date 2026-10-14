"""Network dispatch: one open session, one command, one (data, rows) pair.

The command's handler comes from `tgcli.registry`; read commands go through
the `read_ops` seam (ADR-0034). Telethon rate-limit errors are translated
into the CLI's own error contract at this boundary.
"""

from __future__ import annotations

from telethon import errors as telethon_errors

from tgcli import registry, session
from tgcli.errors import RateLimitError


async def run_network(args, account) -> tuple[dict, list[tuple]]:
    spec = registry.lookup(args)
    # cli._execute already holds the audit-role context var for the whole
    # invocation (including this coroutine); only the network session itself
    # needs the role here.
    role = getattr(args, "session_role", None)
    try:
        async with session.client(
            account, mutation_safe=spec.retries(args), role=role
        ) as tg:
            return await spec.run(tg, args, account)
    except telethon_errors.TakeoutInitDelayError as exc:
        raise RateLimitError(
            f"takeout is unavailable for {exc.seconds}s; retry after {exc.seconds}s",
            retry_after=exc.seconds,
        ) from exc
    except telethon_errors.FloodWaitError as exc:
        raise RateLimitError(
            f"rate limited for {exc.seconds}s", retry_after=exc.seconds
        ) from exc
