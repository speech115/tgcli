"""Plain single-line `clone sync` progress on stderr (ADR-0049).

The lines are informative, not contract data: stdout stays exactly one JSON
document and these are free text on stderr, safe to silence with `2>/dev/null`.
No carriage returns, cursor control, or colors — the same stream has to read
correctly in a terminal, a log file, `tail -f`, and an agent transcript.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from telethon import errors as telethon_errors

from tgcli.output import note, sanitize

MEGABYTE = 1024 * 1024
# A transfer line every ~5 MB: frequent enough that a large file never looks
# stuck, rare enough that a long sync does not flood a transcript.
PROGRESS_EVERY_BYTES = 5 * MEGABYTE

# Zero-arg thunk → fresh awaitable (ADR-0052 task 1); matches transfer.Invoke.
Invoke = Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]]


async def approximate_total(tg, entity, invoke: Invoke) -> int | None:
    """Best-effort source size for the `~total`; never fails a sync.

    FloodWait still propagates through ``invoke`` so a hot account keeps the
    ADR-0045 exit-5 posture instead of syncing on a rate-limited session.
    """
    try:
        return (await invoke(lambda: tg.get_messages(entity, limit=0))).total
    except telethon_errors.FloodWaitError:
        raise
    except (telethon_errors.RPCError, AttributeError, ValueError):
        return None


def transfer_of(progress, message, direction: str):
    """None-safe transfer callback, so the reupload legs stay unconditional."""
    if progress is None:
        return None
    return progress.transfer(media_label(message), direction)


def media_label(message) -> str:
    """Best-effort human name for the file a reupload is moving."""
    name = getattr(getattr(message, "file", None), "name", None)
    if isinstance(name, str) and name:
        return name
    return f"message-{getattr(message, 'id', '?')}"


def comments_unstarted(clone_state) -> bool:
    """Warn while the clone's discussion group holds only anchors.

    Telegram auto-forwards every channel post into the linked group, and those
    anchors are what comments hang from. Until the comments leg copies
    anything, the destination group is a bare anchor list that reads as a
    pointless duplicate of the channel — a live clone was reported as
    "duplicated posts" in exactly this state. Nothing is wrong with the copy;
    the missing half is what makes the present half look wrong, so say it out
    loud instead of leaving the operator to infer it from `discussion_cursor`.

    Returns whether the warning was written, so a single run does not repeat
    it. Informative stderr only (ADR-0049), never contract data.
    """
    if clone_state.comments != "enabled":
        return False
    if clone_state.cursor == 0 or clone_state.discussion_cursor != 0:
        return False
    note(
        "warning: clone comments not started; the destination discussion group "
        "holds only Telegram's post anchors and reads as a duplicate of the "
        "channel until a further sync copies the comments"
    )
    return True


class SyncProgress:
    """Counts copied messages and renders the sync's current activity."""

    def __init__(
        self,
        source_id: int,
        *,
        total: int | None = None,
        copied: int = 0,
        write: Callable[[str], None] = note,
    ) -> None:
        self._source_id = source_id
        self._total = total
        self._copied = copied
        self._write = write
        self._total_resolved = total is not None

    async def resolve_total(self, tg, entity, invoke: Invoke) -> None:
        """Fetch `~total` once, lazily, on the first batch that reports.

        A sync with nothing to copy spends no RPC on a cosmetic number — the
        idle keep-up-to-date call stays as cheap as it was (ADR-0045).
        """
        if self._total_resolved:
            return
        self._total_resolved = True
        self._total = await approximate_total(tg, entity, invoke)

    def _prefix(self) -> str:
        total = "?" if self._total is None else self._total
        return f"[sync {self._source_id}] {self._copied}/~{total}"

    def batch(self, count: int, transport: str) -> None:
        """Report a finished batch and the transport it went out through."""
        self._copied += count
        self._write(f"{self._prefix()} · {transport}")

    def phase(self, name: str, *, copied: int = 0) -> None:
        """Start a new leg: announce it, and restart both counters for it.

        The counters are per leg, not per run (#174). Carrying the posts
        count into the comments phase printed `998/~997` — more copied than
        there is work — because the copied side counted every mapped message
        while the total came from the channel the posts leg reads. Each leg
        now counts what *it* has copied against what *its* source holds, and
        the total re-resolves against that leg's own entity.
        """
        self._copied = copied
        self._total = None
        self._total_resolved = False
        self._write(f"{self._prefix()} · {name}")

    def transfer(self, filename: str, direction: str) -> Callable[..., None]:
        """Return a byte-progress callback throttled to one line per ~5 MB."""
        # The name is Telegram's, so it is what could carry a `\r` or an escape
        # into a line CONTRACT.md §2 promises is plain.
        filename = sanitize(filename)
        reported = 0

        def report(current: int, total: int | None) -> None:
            nonlocal reported
            complete = isinstance(total, int) and total > 0 and current >= total
            if not complete and current - reported < PROGRESS_EVERY_BYTES:
                return
            reported = current
            done = f"{current / MEGABYTE:.1f}"
            if isinstance(total, int) and total > 0:
                percent = int(current * 100 / total)
                measure = f"{done}/{total / MEGABYTE:.1f} MB ({percent}%)"
            else:
                measure = f"{done} MB"
            self._write(
                f"{self._prefix()} · reupload · {filename} · {direction} {measure}"
            )

        return report
