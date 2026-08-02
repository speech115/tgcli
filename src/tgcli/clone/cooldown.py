"""The clone flood gate: cooldown enforcement and the RPC seam (ADR-0045/0052).

Every clone command refuses to start while a cooldown is armed. Requests
themselves go through the governed ``_call`` seam (ADR-0072): a flood arms
the account-wide per-type cooldown in the governor's ledger and the command
exits 5 locally. The ADR-0045 account-scoped JSON record and the ADR-0052
foreground retry are retired; only the per-clone ``retry_not_before``
deadline gate remains here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil

from tgcli.errors import RateLimitError


def raise_if_cooling(deadline: datetime) -> None:
    retry_after = ceil((deadline - datetime.now(UTC)).total_seconds())
    if retry_after > 0:
        raise RateLimitError(
            f"rate limited for {retry_after}s", retry_after=retry_after
        )


def enforce(clone_state) -> None:
    deadline = clone_state.cooldown_deadline()
    if deadline is not None:
        raise_if_cooling(deadline)


async def mutate(tg, request):
    """Send one mutation through the governed seam.

    Floods are handled by the governor's ``_call`` wrapper: it arms the
    per-type cooldown from the server's own ``retry_after`` and the command
    exits 5 locally on the next attempt (ADR-0072).
    """
    return await tg(request)
