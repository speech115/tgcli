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

from telethon.tl import types

from tgcli import atomic
from tgcli.clone import progress as clone_progress, state
from tgcli.errors import PolicyError
from tgcli.output import note
from tgcli.state_paths import ensure_state_dir
from tgcli.tg_errors import TRANSIENT_ERRORS
from tgcli.transfer import (
    CHUNK_SIZE,
    CLONE_TRANSFER_PARALLEL,
    download_resumable,
    keep_proven_bytes,
    media_byte_size,
    media_identity,
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


async def uploaded_thumb(tg, message, document, path: Path):
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
        downloaded = await tg.download_media(message, file=path, thumb=thumb)
        if downloaded is None:
            return None
        return await tg.upload_file(downloaded)
    except TRANSIENT_ERRORS:
        # Not a refused thumb: dropping it would lose the preview for good.
        raise
    except Exception as exc:
        note(f"warning: clone thumb skipped for source message {message.id}: {exc}")
        return None


def _plain_video(attributes) -> bool:
    """A video that is not a GIF: Telegram must keep showing it as a video."""
    return any(
        isinstance(item, types.DocumentAttributeVideo) for item in attributes
    ) and not any(
        isinstance(item, types.DocumentAttributeAnimated) for item in attributes
    )


# Telegram takes an uploaded photo's format from its file name and refuses one
# without an image extension (PHOTO_EXT_INVALID). Telegram serves photos as JPEG.
PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


def upload_name(message, path) -> str:
    """The file name the upload declares: the cache file's, except that a photo
    streamed to the extensionless ``src-<id>`` cache name gets ``.jpg``."""
    path = Path(path)
    if isinstance(message.media, types.MessageMediaPhoto) and (
        path.suffix.lower() not in PHOTO_EXTENSIONS
    ):
        return f"{path.stem}.jpg"
    return path.name


async def uploaded_media(tg, message, path, progress=None):
    input_file = await upload_parts(
        tg,
        path,
        parallel=CLONE_TRANSFER_PARALLEL,
        file_name=upload_name(message, path),
        progress=clone_progress.transfer_of(progress, message, "upload"),
    )
    # A blur the source put on purpose is content: dropping it publishes the
    # media uncovered.
    spoiler = getattr(message.media, "spoiler", None) or None
    if isinstance(message.media, types.MessageMediaPhoto):
        return types.InputMediaUploadedPhoto(file=input_file, spoiler=spoiler)
    document = message.media.document
    attributes: list = list(getattr(document, "attributes", None) or ())
    return types.InputMediaUploadedDocument(
        file=input_file,
        mime_type=getattr(document, "mime_type", None) or "application/octet-stream",
        attributes=attributes,
        # Unset, Telegram may turn a video without sound into a GIF (as in
        # `tg send --file`); a source GIF carries its own Animated attribute.
        nosound_video=_plain_video(attributes) or None,
        spoiler=spoiler,
        thumb=await uploaded_thumb(
            tg, message, document, Path(path).with_name(f"thumb-{message.id}")
        ),
    )


def cache_dir(clone_state: state.CloneState) -> Path:
    """Per-clone reupload download cache (ADR-0052). Survives a failed batch."""
    return state.clones_dir() / f"{clone_state.clone_id}-media"


def ensure_cache_dir(clone_state: state.CloneState) -> Path:
    """`cache_dir`, created with every level under the state root 0700."""
    return ensure_state_dir("clones", cache_dir(clone_state).name)


def complete_marker(target: Path) -> Path:
    """Sibling file that means "this cached download finished".

    Size alone cannot prove it: a pre-fix binary killed mid-stripe left a
    full-size sparse file at the final name, which the reuse check would
    upload as if it were the real media (ADR-0052 cache survives runs).
    """
    return target.with_name(f"{target.name}.done")


def completed_download(target: Path) -> Path | None:
    """The finished download for ``target``, or None.

    The marker names the file when Telethon chose it (a one-chunk download
    gets an extension); an empty marker means ``target`` itself.
    """
    try:
        name = complete_marker(target).read_text().strip()
    except OSError:
        return None
    if not name or Path(name).name != name:
        return target
    return target.with_name(name)


def download_checkpoint(part: Path) -> Path:
    """Sidecar recording how much of ``part`` is proven media bytes."""
    return part.with_name(f"{part.name}.offset")


def _write_checkpoint(part: Path, size: int, media_id: int | None, offset: int) -> None:
    atomic.replace_text(
        download_checkpoint(part),
        json.dumps({"size": size, "media_id": media_id, "offset": offset}),
    )


def resume_offset(part: Path, size: int, media_id: int | None) -> int:
    """Bytes of ``part`` that may be kept, truncating it to that point.

    Without a checkpoint no byte is proven — a killed pre-resume run left a
    full-size *sparse* `.part` behind, and reusing it would upload zeroes as
    media. A checkpoint whose recorded size or media id differs describes
    another file. Either way the partial is dropped and the download restarts.
    """
    record_path = download_checkpoint(part)
    try:
        record = json.loads(record_path.read_text())
        offset = record["offset"]
        recorded = (record["size"], record["media_id"])
    except (OSError, ValueError, KeyError, TypeError):
        offset, recorded = None, None
    kept = keep_proven_bytes(part, offset) if recorded == (size, media_id) else None
    if kept is None:
        part.unlink(missing_ok=True)
        record_path.unlink(missing_ok=True)
        return 0
    return kept


async def download_for_reupload(
    tg, message, workdir: Path, clone_state, progress=None
) -> Path:
    target = workdir / f"src-{message.id}"
    marker = complete_marker(target)
    size = media_byte_size(message)
    cached = completed_download(target)
    if (
        size is not None
        and cached is not None
        and cached.is_file()
        and cached.stat().st_size == size
    ):
        return cached
    # Stale name/size (or unpredictable size): drop before re-download so the
    # partial file and download_media see a free path. Telethon never
    # overwrites: a file left at its name sends the next download to
    # `src-<id> (1).jpg`.
    if cached is not None:
        cached.unlink(missing_ok=True)
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
        media_id = media_identity(message.media)
        offset = resume_offset(part, size, media_id)
        written = await download_resumable(
            tg,
            message.media,
            part,
            offset=offset,
            size=size,
            checkpoint=lambda current: _write_checkpoint(part, size, media_id, current),
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
    if path.parent == target.parent:
        # Telethon picks the name (`src-<id>.jpg`): the marker records it.
        atomic.replace_text(marker, "" if path == target else path.name)
    return path
