"""A fake Telegram for `tg clone init`, shared by the clone init and lifecycle tests."""

from pathlib import Path
from types import SimpleNamespace

from telethon.tl import functions, types

from tgcli.clone import attribution, state


def channel(channel_id, title, **overrides):
    values = {
        "id": channel_id,
        "title": title,
        "broadcast": True,
        "megagroup": False,
        "noforwards": False,
        "creator": False,
        "username": None,
        "usernames": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class CloneInitClient:
    def __init__(self):
        self.source = channel(123, "Source channel", noforwards=True)
        self.source_about = ""
        self.requests = []
        self.destination = None
        self.downloads = []
        self.uploads = []
        self.linked = None
        self.linked_monoforum_id = None
        self.linked_history_error = None
        self.discussion = None
        self.notify_mute_until = {}
        self.dialog_filters = []
        self.notify_error = None
        self.folder_error = None

    async def get_entity(self, ref):
        if isinstance(ref, types.PeerChannel):
            for entity in (self.destination, self.linked, self.discussion):
                if entity is not None and entity.id == ref.channel_id:
                    return entity
            raise ValueError(f"no entity: {ref!r}")
        assert ref == "@source"
        return self.source

    async def get_input_entity(self, ref):
        entity = ref if hasattr(ref, "id") else await self.get_entity(ref)
        return types.InputPeerChannel(channel_id=entity.id, access_hash=0)

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_messages(self, entity, limit=None):
        if entity is self.linked:
            if self.linked_history_error is not None:
                raise self.linked_history_error
            return [SimpleNamespace(id=1)]
        assert entity is self.source
        assert limit == 0
        return SimpleNamespace(total=321)

    async def iter_dialogs(self):
        for entity in (self.destination, self.discussion):
            if entity is not None:
                yield SimpleNamespace(entity=entity)

    async def download_profile_photo(self, entity, file=None):
        assert entity is self.source
        path = Path(file).with_suffix(".jpg")
        path.write_bytes(b"avatar")
        self.downloads.append(path)
        return str(path)

    async def upload_file(self, path):
        self.uploads.append(Path(path))
        return types.InputFile(id=1, parts=1, name=Path(path).name, md5_checksum="")

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.account.GetNotifySettingsRequest):
            if self.notify_error is not None:
                raise self.notify_error
            peer = request.peer.peer
            channel_id = getattr(peer, "channel_id", None)
            mute_until = self.notify_mute_until.get(channel_id)
            return SimpleNamespace(mute_until=mute_until)
        if isinstance(request, functions.account.UpdateNotifySettingsRequest):
            if self.notify_error is not None:
                raise self.notify_error
            peer = request.peer.peer
            self.notify_mute_until[peer.channel_id] = request.settings.mute_until
            return True
        if isinstance(request, functions.messages.GetDialogFiltersRequest):
            if self.folder_error is not None:
                raise self.folder_error
            return SimpleNamespace(
                filters=list(self.dialog_filters), tags_enabled=False
            )
        if isinstance(request, functions.messages.UpdateDialogFilterRequest):
            if self.folder_error is not None:
                raise self.folder_error
            self.dialog_filters = [
                item
                for item in self.dialog_filters
                if getattr(item, "id", None) != request.id
            ]
            if request.filter is not None:
                self.dialog_filters.append(request.filter)
            return True
        if isinstance(request, functions.channels.GetFullChannelRequest):
            own = request.channel is self.source
            return SimpleNamespace(
                full_chat=SimpleNamespace(
                    about=self.source_about,
                    linked_chat_id=(
                        self.linked.id if own and self.linked is not None else None
                    ),
                    linked_monoforum_id=self.linked_monoforum_id if own else None,
                )
            )
        if isinstance(request, functions.messages.GetFullChatRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(about=self.source_about))
        if isinstance(request, functions.users.GetFullUserRequest):
            return SimpleNamespace(full_user=SimpleNamespace(about=self.source_about))
        if isinstance(request, functions.channels.CreateChannelRequest):
            pending = state.load(
                state.clone_id(42, 123, attribution.source_kind(self.source))
            )
            if request.title.endswith("-discussion"):
                assert pending.discussion_destination_peer_id is None
                assert request.title == f"{pending.creation_marker}-discussion"
                self.discussion = channel(
                    1001,
                    request.title,
                    creator=True,
                    broadcast=False,
                    megagroup=True,
                    forum=False,
                )
                return SimpleNamespace(chats=[self.discussion])
            assert pending.destination_peer_id is None
            assert pending.creation_marker == request.title
            self.destination = channel(
                999,
                request.title,
                creator=True,
                broadcast=request.broadcast,
                megagroup=request.megagroup,
                forum=False,
            )
            return SimpleNamespace(chats=[self.destination])
        if isinstance(request, functions.channels.ToggleForumRequest):
            self.destination.forum = True
            return SimpleNamespace()
        if isinstance(request, functions.channels.EditTitleRequest):
            request.channel.title = request.title
            return SimpleNamespace()
        if isinstance(request, functions.messages.EditChatAboutRequest):
            request.peer.about = request.about
            return True
        if isinstance(request, functions.channels.EditPhotoRequest):
            request.channel.photo = request.photo
            return SimpleNamespace()
        if isinstance(
            request,
            (
                functions.channels.TogglePreHistoryHiddenRequest,
                functions.channels.SetDiscussionGroupRequest,
            ),
        ):
            return SimpleNamespace()
        raise AssertionError(f"unexpected request: {request!r}")
