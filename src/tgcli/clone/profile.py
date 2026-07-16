"""Copy source-channel profile metadata during clone initialization."""

from pathlib import Path
import tempfile

from telethon.tl import functions, types

from tgcli import safety
from tgcli.errors import PolicyError


async def copy(tg, source, destination, account_alias, clone_id, cooldown) -> None:
    full = await cooldown(tg(functions.channels.GetFullChannelRequest(source)))
    about = getattr(full.full_chat, "about", None) or ""
    if about:
        safety.append_audit("clone-init-about", account_alias, {
            "clone_id": clone_id, "source_peer_id": source.id,
        })
        await cooldown(tg(functions.messages.EditChatAboutRequest(
            peer=destination, about=about,
        )))
    photo = getattr(source, "photo", None)
    if photo is None or isinstance(photo, types.ChatPhotoEmpty):
        return
    with tempfile.TemporaryDirectory(prefix="tgcli-clone-avatar-") as workdir:
        downloaded = await cooldown(tg.download_profile_photo(
            source, file=Path(workdir) / "avatar",
        ))
        if downloaded is None:
            raise PolicyError("clone source avatar download failed")
        uploaded = await cooldown(tg.upload_file(downloaded))
        safety.append_audit("clone-init-avatar", account_alias, {
            "clone_id": clone_id, "source_peer_id": source.id,
        })
        await cooldown(tg(functions.channels.EditPhotoRequest(
            channel=destination, photo=types.InputChatUploadedPhoto(file=uploaded),
        )))
