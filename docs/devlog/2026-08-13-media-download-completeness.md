## 2026-08-13 — `media download` never publishes incomplete bytes (ADR-0091)

**Did:** thermos T03. Serial loop now requires `current == size` before
`_publish` and fsyncs the part file before every checkpoint (was flush-only).
`transfer.download_striped` raises + unlinks when its own downloaded-byte
counter falls short of `size`; `_download_parallel` maps that into
`PolicyError` so both paths exit 2 (`BLOCKED`). Tests in
`test_commands_media.py` / `test_transfer.py`; fixed three existing fakes
whose declared `file.size` never matched their fed chunk length. Fixed
`scripts/publish-thermos-backlog.py` E501s already on main.

**Decided:** fix the completeness gap now; leave routing `media download`
through `download_resumable` itself (T31) as a separate, bigger decision
about a released command's on-disk state format.

**Learned:** `download_striped`'s `truncate(size)` pre-allocation makes the
on-disk file size a false completeness signal — only the in-memory
downloaded-byte counter can prove a striped transfer actually finished.

**Next:** T31, if the owner wants it.
