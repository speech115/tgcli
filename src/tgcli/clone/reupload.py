"""Reupload media: the still-thumb picker, the upload, and the download cache.

Everything a reuploaded batch needs to turn a source message into
`InputMediaUploaded*` bytes on disk and back, kept out of the command module
so the sync path reads as decisions rather than transfer mechanics
(ADR-0049/0052/0055).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli import atomic
from tgcli.clone import progress as clone_progress, state
from tgcli.errors import PolicyError
from tgcli.output import note
from tgcli.transfer import (
    CHUNK_SIZE,
    CLONE_TRANSFER_PARALLEL,
    download_resumable,
    media_byte_size,
    upload_parts,
)

# The still-image sizes Telethon accepts back as a `thumb=` argument. A
# `PhotoPathSize` is an outline, a `VideoSize` is a video preview, and a
# `PhotoSizeProgressive` is not selectable by object, so none of them qualify.
STILL_THUMB_SIZES = (
    types.PhotoSize,
    types.PhotoCachedSize,
    types.PhotoStrippedSize,
)


def thumb_weight(thumb) -> int:
    stored = getattr(thumb, "bytes", None)
    return len(stored) if stored is not None else getattr(thumb, "size", 0)


def document_thumb(document):
    """The document's largest still-image thumb size, or None.

    The size *object* is returned, never an index into ``document.thumbs``:
    Telethon sorts the sizes and drops `PhotoPathSize` before it indexes, so an
    index taken from the original list is out of range on an animated sticker.
    """
    stills = [
        thumb
        for thumb in getattr(document, "thumbs", None) or ()
        if isinstance(thumb, STILL_THUMB_SIZES)
    ]
    return max(stills, key=thumb_weight) if stills else None


async def uploaded_thumb(tg, message, document, path: Path, invoke):
    """Upload the source document's still preview, or None.

    Telegram will not regenerate a document preview (a PDF page, a sticker
    still) from the bytes alone, so a reupload without it degrades to a bare
    file row. A preview is fidelity and not content: any refusal below a
    FloodWait drops the thumb and lets the reupload finish.
    """
    thumb = document_thumb(document)
    if thumb is None:
        return None
    try:
        downloaded = await invoke(
            lambda: tg.download_media(message, file=path, thumb=thumb)
        )
        if downloaded is None:
            return None
        return await invoke(lambda: tg.upload_file(downloaded))
    except telethon_errors.FloodWaitError:
        raise
    except Exception as exc:
        note(f"warning: clone thumb skipped for source message {message.id}: {exc}")
        return None


async def uploaded_media(tg, message, path, progress=None):
    async def invoke(make_awaitable):
        return await make_awaitable()

    input_file = await upload_parts(
        tg,
        path,
        parallel=CLONE_TRANSFER_PARALLEL,
        invoke=invoke,
        progress=clone_progress.transfer_of(progress, message, "upload"),
    )
    if isinstance(message.media, types.MessageMediaPhoto):
        return types.InputMediaUploadedPhoto(file=input_file)
    document = message.media.document
    return types.InputMediaUploadedDocument(
        file=input_file,
        mime_type=getattr(document, "mime_type", None) or "application/octet-stream",
        attributes=list(getattr(document, "attributes", None) or ()),
        thumb=await uploaded_thumb(
            tg, message, document, Path(path).with_name(f"thumb-{message.id}"), invoke
        ),
    )


def cache_dir(clone_state: state.CloneState) -> Path:
    """Per-clone reupload download cache (ADR-0052). Survives a failed batch."""
    return state.clones_dir() / f"{clone_state.clone_id}-media"


def complete_marker(target: Path) -> Path:
    """Sibling file that means "this cached download finished".

    Size alone cannot prove it: a pre-fix binary killed mid-stripe left a
    full-size sparse file at the final name, which the reuse check would
    upload as if it were the real media (ADR-0052 cache survives runs).
    """
    return target.with_name(f"{target.name}.done")


def download_checkpoint(part: Path) -> Path:
    """Sidecar recording how much of ``part`` is proven media bytes."""
    return part.with_name(f"{part.name}.offset")


def _write_checkpoint(part: Path, size: int, offset: int) -> None:
    atomic.replace_text(
        download_checkpoint(part), json.dumps({"size": size, "offset": offset})
    )


def resume_offset(part: Path, size: int) -> int:
    """Bytes of ``part`` that may be kept, truncating it to that point.

    Without a checkpoint no byte is proven — a killed pre-resume run left a
    full-size *sparse* `.part` behind, and reusing it would upload zeroes as
    media. A checkpoint from a different `size` belongs to another revision
    of the media, not to this one. Either way the partial file is dropped and
    the download starts over.
    """
    record_path = download_checkpoint(part)
    try:
        record = json.loads(record_path.read_text())
        offset = record["offset"]
        recorded_size = record["size"]
    except (OSError, ValueError, KeyError, TypeError):
        offset, recorded_size = None, None
    on_disk = part.stat().st_size if part.is_file() else None
    if (
        type(offset) is not int
        or offset < 0
        or recorded_size != size
        or on_disk is None
        or offset > on_disk
    ):
        part.unlink(missing_ok=True)
        record_path.unlink(missing_ok=True)
        return 0
    if offset < on_disk:
        with part.open("r+b") as handle:
            handle.truncate(offset)
    return offset


async def download_for_reupload(
    tg, message, workdir: Path, clone_state, progress=None
) -> Path:
    target = workdir / f"src-{message.id}"
    marker = complete_marker(target)
    size = media_byte_size(message)
    if (
        size is not None
        and marker.is_file()
        and target.is_file()
        and target.stat().st_size == size
    ):
        return target
    # Stale name/size (or unpredictable size): drop before re-download so the
    # partial file and download_media see a free path.
    target.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    if size is not None and size > CHUNK_SIZE:
        # Stream into a sibling .part and rename, as `media download` does:
        # the final name must mean "complete", or a killed run leaves a
        # full-size sparse file the reuse check would upload as real media
        # (ADR-0052). The stream is serial and checkpointed rather than
        # striped, so a FloodWait costs the current chunk instead of the
        # whole file (ADR-0083 superseding ADR-0047's download leg).
        # Only this path reports bytes: a sub-chunk file is over before it
        # could reach a progress mark (ADR-0049).
        part = target.with_name(f"{target.name}.part")
        offset = resume_offset(part, size)
        written = await download_resumable(
            tg,
            message.media,
            part,
            offset=offset,
            size=size,
            checkpoint=lambda current: _write_checkpoint(part, size, current),
            progress=clone_progress.transfer_of(progress, message, "download"),
        )
        if written != size:
            # A stream that ended early is not a complete file, and the final
            # name means complete. Keep the partial for the next run's resume.
            raise PolicyError(
                f"clone media download stopped at {written}/{size} bytes "
                f"for source message {message.id}"
            )
        os.replace(part, target)
        download_checkpoint(part).unlink(missing_ok=True)
        marker.touch()
        return target
    downloaded = await tg.download_media(message, file=target)
    if downloaded is None:
        raise PolicyError(f"clone media download failed at source message {message.id}")
    path = Path(downloaded)
    if path == target:
        marker.touch()
    return path
