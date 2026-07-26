"""Reupload media: the still-thumb picker, the upload, and the download cache.

Everything a reuploaded batch needs to turn a source message into
`InputMediaUploaded*` bytes on disk and back, kept out of the command module
so the sync path reads as decisions rather than transfer mechanics
(ADR-0049/0052/0055).
"""

from __future__ import annotations

import os
from pathlib import Path

from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import cooldown, progress as clone_progress, state
from tgcli.errors import PolicyError
from tgcli.output import note
from tgcli.transfer import (
    CHUNK_SIZE,
    CLONE_TRANSFER_PARALLEL,
    download_striped,
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


async def uploaded_media(tg, message, path, clone_state, budget, progress=None):
    async def invoke(make_awaitable):
        return await cooldown.with_cooldown(make_awaitable, clone_state, budget)

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


async def download_for_reupload(
    tg, message, workdir: Path, clone_state, budget, progress=None
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
    # Stale name/size (or unpredictable size): drop before re-download so
    # download_striped's exclusive create and download_media see a free path.
    target.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
    if size is not None and size > CHUNK_SIZE:
        # Only the striped path reports bytes: a sub-chunk file is over before
        # it could reach a progress mark (ADR-0049).
        # Stripe into a sibling .part and rename, as `media download` does: the
        # final name must mean "complete", or a killed run leaves a full-size
        # sparse file the reuse check would upload as real media (ADR-0052).
        part = target.with_name(f"{target.name}.part")
        part.unlink(missing_ok=True)
        await cooldown.with_cooldown(
            lambda: download_striped(
                tg,
                message.media,
                part,
                size=size,
                parallel=CLONE_TRANSFER_PARALLEL,
                progress=clone_progress.transfer_of(progress, message, "download"),
            ),
            clone_state,
            budget,
        )
        os.replace(part, target)
        marker.touch()
        return target
    downloaded = await cooldown.with_cooldown(
        lambda: tg.download_media(message, file=target),
        clone_state,
        budget,
    )
    if downloaded is None:
        raise PolicyError(f"clone media download failed at source message {message.id}")
    path = Path(downloaded)
    if path == target:
        marker.touch()
    return path
