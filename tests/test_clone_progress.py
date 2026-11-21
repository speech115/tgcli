"""Unit tests for the clone sync stderr progress emitter."""

from __future__ import annotations

from types import SimpleNamespace

from tgcli.clone import progress


def collector() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


async def test_resolve_total_requeries_after_phase_reset():
    """phase() must clear the resolved flag so the next leg can re-fetch ~total."""
    lines, write = collector()
    reporter = progress.SyncProgress(123, copied=10, write=write)
    totals = [50, 7]
    calls: list[tuple[object, int | None]] = []

    class FakeTg:
        async def get_messages(self, entity, limit=None):
            calls.append((entity, limit))
            return SimpleNamespace(total=totals[len(calls) - 1])

    tg = FakeTg()
    posts = object()
    comments = object()
    await reporter.resolve_total(tg, posts)
    reporter.batch(1, "forwarded")
    reporter.phase("comments")
    await reporter.resolve_total(tg, comments)
    reporter.batch(1, "forwarded")

    assert calls == [(posts, 0), (comments, 0)]
    assert lines == [
        "[sync 123] 11/~50 · forwarded",
        "[sync 123] 0/~? · comments",
        "[sync 123] 1/~7 · forwarded",
    ]


def test_transfer_callback_reports_every_five_megabytes():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=4, copied=1, write=write)
    size = 12 * progress.MEGABYTE
    report = reporter.transfer("clip.mp4", "download")

    report(1 * progress.MEGABYTE, size)
    report(6 * progress.MEGABYTE, size)
    report(7 * progress.MEGABYTE, size)
    report(size, size)

    assert lines == [
        "[sync 123] 1/~4 · reupload · clip.mp4 · download 6.0/12.0 MB (50%)",
        "[sync 123] 1/~4 · reupload · clip.mp4 · download 12.0/12.0 MB (100%)",
    ]


def test_transfer_tolerates_a_missing_or_zero_size():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=None, write=write)

    reporter.transfer("a.bin", "download")(5 * progress.MEGABYTE, 0)
    reporter.transfer("b.bin", "upload")(5 * progress.MEGABYTE, None)

    assert lines == [
        "[sync 123] 0/~? · reupload · a.bin · download 5.0 MB",
        "[sync 123] 0/~? · reupload · b.bin · upload 5.0 MB",
    ]


def test_media_label_prefers_the_source_filename():
    assert progress.media_label(_message(7, name="report.pdf")) == "report.pdf"
    assert progress.media_label(_message(7)) == "message-7"


def _message(message_id: int, *, name: str | None = None):
    class _File:
        def __init__(self, value):
            self.name = value

    class _Message:
        def __init__(self):
            self.id = message_id
            self.file = None if name is None else _File(name)

    return _Message()
