"""Unit tests for the clone sync stderr progress emitter (ADR-0049)."""

from __future__ import annotations

import re
from types import SimpleNamespace

from tgcli.clone import progress


ANSI = re.compile(r"\x1b\[")


def collector() -> tuple[list[str], object]:
    lines: list[str] = []
    return lines, lines.append


def test_batch_line_states_count_total_and_transport():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=120, copied=50, write=write)

    reporter.batch(3, "forwarded")

    assert lines == ["[sync 123] 53/~120 · forwarded"]


def test_batch_counts_accumulate_across_batches():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=10, write=write)

    reporter.batch(2, "forwarded")
    reporter.batch(1, "reuploaded")

    assert lines == [
        "[sync 123] 2/~10 · forwarded",
        "[sync 123] 3/~10 · reuploaded",
    ]


def test_unknown_total_renders_as_a_question_mark():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=None, write=write)

    reporter.batch(1, "snapshots")

    assert lines == ["[sync 123] 1/~? · snapshots"]


def test_phase_line_names_the_leg_and_resets_the_total():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=120, copied=120, write=write)

    reporter.phase("comments")
    reporter.batch(1, "forwarded")
    reporter.phase("roster")

    assert lines == [
        "[sync 123] 120/~120 · comments",
        "[sync 123] 121/~? · forwarded",
        "[sync 123] 121/~? · roster",
    ]


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

    async def invoke(make):
        return await make()

    tg = FakeTg()
    source = object()
    await reporter.resolve_total(tg, source, invoke)
    reporter.batch(1, "forwarded")
    reporter.phase("comments")
    await reporter.resolve_total(tg, source, invoke)
    reporter.batch(1, "forwarded")

    assert calls == [(source, 0), (source, 0)]
    assert lines == [
        "[sync 123] 11/~50 · forwarded",
        "[sync 123] 11/~50 · comments",
        "[sync 123] 12/~7 · forwarded",
    ]


def test_transfer_callback_reports_every_five_megabytes():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=4, copied=1, write=write)
    size = 12 * progress.MEGABYTE
    report = reporter.transfer("clip.mp4", "download")

    report(1 * progress.MEGABYTE, size)  # below the threshold — silent
    report(6 * progress.MEGABYTE, size)
    report(7 * progress.MEGABYTE, size)  # only 1 MB since the last line
    report(size, size)

    assert lines == [
        "[sync 123] 1/~4 · reupload · clip.mp4 · download 6.0/12.0 MB (50%)",
        "[sync 123] 1/~4 · reupload · clip.mp4 · download 12.0/12.0 MB (100%)",
    ]


def test_each_transfer_throttles_independently():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=None, write=write)
    size = 6 * progress.MEGABYTE

    first = reporter.transfer("a.bin", "download")
    second = reporter.transfer("b.bin", "upload")
    first(size, size)
    second(size, size)

    assert lines == [
        "[sync 123] 0/~? · reupload · a.bin · download 6.0/6.0 MB (100%)",
        "[sync 123] 0/~? · reupload · b.bin · upload 6.0/6.0 MB (100%)",
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


def test_lines_carry_no_carriage_returns_or_ansi_control():
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=9, write=write)

    reporter.batch(1, "forwarded")
    reporter.phase("comments")
    reporter.transfer("clip.mp4", "upload")(
        9 * progress.MEGABYTE, 9 * progress.MEGABYTE
    )

    assert lines
    for line in lines:
        assert "\r" not in line
        assert "\n" not in line
        assert not ANSI.search(line)


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


def test_transfer_line_strips_control_characters_from_the_filename():
    """A Telegram filename must not smuggle \\r or ESC into a plain line."""
    lines, write = collector()
    reporter = progress.SyncProgress(123, total=None, write=write)

    reporter.transfer("cl\x1bip\r.m\x08p4\nx", "download")(5 * progress.MEGABYTE, None)

    assert lines == ["[sync 123] 0/~? · reupload · clip.mp4x · download 5.0 MB"]
    assert not ANSI.search(lines[0])
    assert not any(ch in lines[0] for ch in "\x1b\r\n\x08\t")
