"""Shared clone fakes: the source channel, clients and state seeds."""

from pathlib import Path
from types import SimpleNamespace

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli.clone import legs, state

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


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


def legacy_group():
    return types.Chat(
        id=123,
        title="Legacy group",
        photo=types.ChatPhotoEmpty(),
        participants_count=2,
        date=None,
        version=1,
    )


def message(message_id, **overrides):
    values = {
        "id": message_id,
        "message": f"message {message_id}",
        "action": None,
        "media": None,
        "reply_to": None,
        "grouped_id": None,
        "noforwards": False,
        "entities": None,
        "from_id": None,
        "sender_id": None,
        "out": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def poll_media(*, multiple_choice=False, results=None, total_voters=10, **poll_flags):
    answers = [
        types.PollAnswer(
            text=types.TextWithEntities(text="First", entities=[]), option=b"a"
        ),
        types.PollAnswer(
            text=types.TextWithEntities(text="Second", entities=[]), option=b"b"
        ),
    ]
    if results is None:
        results = [
            types.PollAnswerVoters(option=b"a", voters=4),
            types.PollAnswerVoters(option=b"b", voters=6),
        ]
    return types.MessageMediaPoll(
        poll=types.Poll(
            id=77,
            question=types.TextWithEntities(text="Choose", entities=[]),
            answers=answers,
            hash=0,
            multiple_choice=multiple_choice,
            **poll_flags,
        ),
        results=types.PollResults(
            results=results,
            total_voters=total_voters,
        ),
    )


def seed_clone(*, kind="broadcast", title="Source channel"):
    clone_state = state.CloneState.new(
        account_user_id=42,
        source_peer_id=123,
        source_title=title,
        source_kind=kind,
    )
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    return clone_state


class CloneSyncClient:
    # Telethon restores the logged-in account id from the session on connect,
    # so a connected client knows it without a get_me RPC.
    _self_id = 42

    def __init__(self, messages):
        self.source = channel(123, "Source channel")
        self.destination = channel(999, "Source channel", creator=True)
        self.messages = list(messages)
        self.destination_last_id = 1
        self.destination_actions = {}
        self.requests = []
        self.iter_messages_calls = []
        self.source_pinned_msg_id = None
        self.destination_pinned_msg_id = None
        self.full_requests = []

    async def get_entity(self, ref):
        if isinstance(ref, types.PeerChannel):
            if ref.channel_id == getattr(self.destination, "id", 999):
                return self.destination
            raise ValueError("peer not found")
        if isinstance(ref, (types.PeerUser, types.PeerChat)):
            raise ValueError("peer not found")
        assert ref == "@source"
        return self.source

    async def get_input_entity(self, ref):
        if ref is self.destination:
            return types.InputPeerChannel(channel_id=999, access_hash=1)
        if ref is self.source:
            return types.InputPeerChannel(channel_id=123, access_hash=1)
        raise ValueError("no input peer")

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_messages(self, entity, limit=None):
        if entity is self.source:
            # ADR-0049: sync asks the source for its approximate total once.
            return SimpleNamespace(total=len(self.messages))
        assert entity is self.destination
        return [
            SimpleNamespace(id=item_id, action=self.destination_actions.get(item_id))
            for item_id in range(
                self.destination_last_id, max(self.destination_last_id - limit, 0), -1
            )
        ]

    async def iter_messages(self, entity, *, min_id=0, reverse=False):
        assert entity is self.source
        self.iter_messages_calls.append((min_id, reverse))
        for item in sorted(self.messages, key=lambda value: value.id):
            if item.id > min_id:
                yield item

    async def iter_participants(self, entity, limit=None):
        # a broadcast source the account does not administer refuses its roster
        raise telethon_errors.ChatAdminRequiredError(request=None)
        yield  # pragma: no cover - marks this coroutine as an async generator

    async def __call__(self, request):
        if isinstance(request, functions.channels.GetFullChannelRequest):
            self.full_requests.append(request)
            channel_ref = request.channel
            pinned = (
                self.source_pinned_msg_id
                if getattr(channel_ref, "id", None) == self.source.id
                else self.destination_pinned_msg_id
            )
            return SimpleNamespace(full_chat=SimpleNamespace(pinned_msg_id=pinned))
        if isinstance(request, functions.users.GetFullUserRequest):
            self.full_requests.append(request)
            return SimpleNamespace(
                full_user=SimpleNamespace(pinned_msg_id=self.source_pinned_msg_id)
            )
        if isinstance(request, functions.messages.GetFullChatRequest):
            self.full_requests.append(request)
            return SimpleNamespace(
                full_chat=SimpleNamespace(pinned_msg_id=self.source_pinned_msg_id)
            )
        if isinstance(request, functions.messages.UpdatePinnedMessageRequest):
            self.requests.append(request)
            return True
        assert isinstance(request, functions.messages.ForwardMessagesRequest)
        self.requests.append(request)
        updates = []
        for random_id in request.random_id:
            self.destination_last_id += 1
            updates.append(
                types.UpdateMessageID(id=self.destination_last_id, random_id=random_id)
            )
        return SimpleNamespace(updates=updates)


class CloneReuploadClient(CloneSyncClient):
    def __init__(self, messages, *, protected=False):
        super().__init__(messages)
        self.source.noforwards = protected
        self.downloads = []
        self.uploads = []
        self.part_requests = []

    async def download_media(self, message, file=None):
        path = Path(f"{file}.bin")
        path.write_bytes(b"payload")
        self.downloads.append(path)
        return str(path)

    async def upload_file(self, path):
        self.uploads.append(path)
        return types.InputFile(
            id=len(self.uploads), parts=1, name=Path(path).name, md5_checksum=b""
        )

    async def __call__(self, request):
        if isinstance(
            request,
            (
                functions.upload.SaveFilePartRequest,
                functions.upload.SaveBigFilePartRequest,
            ),
        ):
            self.part_requests.append(request)
            self.uploads.append(f"part-{request.file_part}")
            return True
        if isinstance(request, functions.messages.SendVoteRequest):
            self.requests.append(request)
            if request.options:
                return SimpleNamespace(
                    updates=[
                        types.UpdateMessagePoll(
                            poll_id=1,
                            results=types.PollResults(
                                results=[
                                    types.PollAnswerVoters(option=b"a", voters=5),
                                    types.PollAnswerVoters(option=b"b", voters=6),
                                ],
                                total_voters=11,
                            ),
                        )
                    ]
                )
            return SimpleNamespace(updates=[])
        if isinstance(
            request,
            (
                functions.messages.SendMessageRequest,
                functions.messages.SendMediaRequest,
            ),
        ):
            self.requests.append(request)
            self.destination_last_id += 1
            return SimpleNamespace(
                updates=[
                    types.UpdateMessageID(
                        id=self.destination_last_id, random_id=request.random_id
                    )
                ]
            )
        if isinstance(request, functions.messages.UploadMediaRequest):
            self.requests.append(request)
            index = len(
                [
                    item
                    for item in self.requests
                    if isinstance(item, functions.messages.UploadMediaRequest)
                ]
            )
            return types.MessageMediaPhoto(
                photo=types.Photo(
                    id=index,
                    access_hash=index * 11,
                    file_reference=b"ref",
                    date=None,
                    sizes=[],
                    dc_id=2,
                )
            )
        if isinstance(request, functions.messages.SendMultiMediaRequest):
            self.requests.append(request)
            updates = []
            for item in request.multi_media:
                self.destination_last_id += 1
                updates.append(
                    types.UpdateMessageID(
                        id=self.destination_last_id, random_id=item.random_id
                    )
                )
            return SimpleNamespace(updates=updates)
        return await super().__call__(request)


def forum_channel(channel_id, title, **overrides):
    return channel(
        channel_id, title, broadcast=False, megagroup=True, forum=True, **overrides
    )


def topic_create(message_id, title):
    return message(
        message_id,
        message=None,
        action=types.MessageActionTopicCreate(title=title, icon_color=0),
    )


class CloneForumClient(CloneReuploadClient):
    def __init__(self, messages):
        super().__init__(messages)
        self.source = forum_channel(123, "Forum chat")
        self.destination = forum_channel(999, "Forum chat", creator=True)
        self.source_topic_titles = {}

    async def __call__(self, request):
        if isinstance(request, functions.messages.CreateForumTopicRequest):
            self.requests.append(request)
            self.destination_last_id += 1
            self.destination_actions[self.destination_last_id] = (
                types.MessageActionTopicCreate(title=request.title, icon_color=0)
            )
            return SimpleNamespace(
                updates=[
                    types.UpdateMessageID(
                        id=self.destination_last_id, random_id=request.random_id
                    )
                ]
            )
        if isinstance(request, functions.messages.GetForumTopicsByIDRequest):
            self.requests.append(request)
            return SimpleNamespace(
                topics=[
                    SimpleNamespace(
                        id=topic_id, title=self.source_topic_titles[topic_id]
                    )
                    for topic_id in request.topics
                    if topic_id in self.source_topic_titles
                ]
            )
        return await super().__call__(request)


def seed_comments_clone(**overrides):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.destination_peer_id = 999
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 55
    clone_state.discussion_destination_peer_id = 888
    clone_state.discussion_linked = True
    for key, value in overrides.items():
        setattr(clone_state, key, value)
    state.save(clone_state)
    return clone_state


def anchor(message_id, post_id, *, source_channel_id=123):
    """Telegram's own auto-forward of a channel post into its linked group."""
    return message(
        message_id,
        message=f"post {post_id}",
        fwd_from=types.MessageFwdHeader(
            date=None,
            channel_post=post_id,
            saved_from_peer=types.PeerChannel(channel_id=source_channel_id),
            saved_from_msg_id=post_id,
        ),
    )


def group_sends(client):
    return [
        item
        for item in client.requests
        if getattr(item, "peer", None) is client.destination_group
        or getattr(item, "to_peer", None) is client.destination_group
    ]


class CloneCommentsClient(CloneReuploadClient):
    def __init__(self, messages, comments=()):
        super().__init__(messages)
        self.source_group = channel(
            55, "Source chat", broadcast=False, megagroup=True, forum=False
        )
        self.destination_group = channel(
            888,
            "Source chat",
            creator=True,
            broadcast=False,
            megagroup=True,
            forum=False,
        )
        self.comments = list(comments)
        self.group_last_id = 1
        self.group_tail = {}
        self.anchor_ids = {}
        self.group_members = [
            SimpleNamespace(
                id=701,
                username="member",
                first_name="Group",
                last_name=None,
                phone=None,
                bot=False,
            )
        ]

    async def iter_participants(self, entity, limit=None):
        if entity is self.source_group:
            for member in self.group_members:
                yield member
            return
        raise telethon_errors.ChatAdminRequiredError(request=None)

    async def get_entity(self, ref):
        if isinstance(ref, types.PeerChannel):
            if ref.channel_id == 55:
                return self.source_group
            if ref.channel_id == 888:
                return self.destination_group
        return await super().get_entity(ref)

    async def get_messages(self, entity, limit=None, ids=None):
        if ids is not None:
            assert entity is self.source_group
            found = [item for item in self.comments if item.id == ids]
            return found[0] if found else None
        if entity is self.source_group and limit == 0:
            # The comments leg asks its own source for its ~total (#174).
            return SimpleNamespace(total=len(self.comments))
        if entity is self.destination_group:
            # Telegram fills a live discussion group with its own anchors for
            # our posts; anything else must be planted explicitly.
            return [
                self.group_tail.get(item_id, anchor(item_id, 2, source_channel_id=999))
                for item_id in range(
                    self.group_last_id, max(self.group_last_id - limit, 0), -1
                )
            ]
        return await super().get_messages(entity, limit=limit)

    async def iter_messages(self, entity, *, min_id=0, reverse=False):
        if entity is not self.source_group:
            async for item in super().iter_messages(
                entity, min_id=min_id, reverse=reverse
            ):
                yield item
            return
        self.iter_messages_calls.append(("group", min_id, reverse))
        for item in sorted(self.comments, key=lambda value: value.id):
            if item.id > min_id:
                yield item

    async def __call__(self, request):
        if isinstance(request, functions.messages.GetDiscussionMessageRequest):
            self.requests.append(request)
            found = self.anchor_ids.get(request.msg_id)
            return SimpleNamespace(
                messages=[] if found is None else [SimpleNamespace(id=found)]
            )
        peer = getattr(request, "peer", None) or getattr(request, "to_peer", None)
        if peer is not self.destination_group:
            return await super().__call__(request)
        channel_last_id = self.destination_last_id
        self.destination_last_id = self.group_last_id
        try:
            return await super().__call__(request)
        finally:
            self.group_last_id = self.destination_last_id
            self.destination_last_id = channel_last_id


def discussion(*, id_map=None, discussion_id_map=None, source_peer_id=2):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=source_peer_id, source_title="src"
    )
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 4454061248
    clone_state.discussion_destination_peer_id = 99
    for source_id, destination_id in (id_map or {}).items():
        clone_state.record_mapping(source_id, destination_id)
    for source_id, destination_id in (discussion_id_map or {}).items():
        clone_state.record_discussion_mapping(source_id, destination_id)
    return legs.discussion(clone_state)
