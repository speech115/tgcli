import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import state, topics
from tgcli.errors import PolicyError
from tgcli import session


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


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
    def __init__(self, messages):
        self.source = channel(123, "Source channel")
        self.destination = channel(999, "Source channel", creator=True)
        self.messages = list(messages)
        self.destination_last_id = 1
        self.destination_actions = {}
        self.requests = []
        self.iter_messages_calls = []

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
        raise ValueError("no input peer")

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_messages(self, entity, limit=None):
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


def test_sync_copies_posts_before_comments(config_env, monkeypatch, capsys):
    """Phase 1 sends every post before phase 2 sends any comment."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)],
        [
            anchor(10, 2),
            anchor(11, 3),
            message(
                12,
                message="nice",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500, 3: 600}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 3
    sends = [
        item
        for item in client.requests
        if isinstance(
            item,
            (
                functions.messages.ForwardMessagesRequest,
                functions.messages.SendMessageRequest,
            ),
        )
    ]
    peers = [item.to_peer if hasattr(item, "to_peer") else item.peer for item in sends]
    assert peers == [client.destination, client.destination, client.destination_group]


def test_sync_snapshots_discussion_roster_when_comments_enabled(
    config_env, monkeypatch, capsys
):
    """The source channel refuses its roster (not admin) but its readable
    discussion group is snapshotted to the participant sidecar."""
    from tgcli.clone import roster

    clone_state = seed_comments_clone()
    client = CloneCommentsClient([message(2)], [anchor(10, 2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    participants = json.loads(capsys.readouterr().out)["sync"]["participants"]
    assert participants["source"]["status"] == "unavailable"
    assert participants["discussion"]["status"] == "collected"
    assert participants["discussion"]["count"] == 1
    lines = [
        json.loads(line)
        for line in roster.path_for(clone_state.clone_id).read_text().splitlines()
    ]
    assert lines == [
        {
            "peer": "discussion",
            "id": 701,
            "username": "member",
            "first_name": "Group",
            "last_name": None,
            "phone": None,
            "is_bot": False,
        }
    ]


def test_sync_disabled_comments_skips_comment_phase_and_discussion_roster(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.comments = "disabled"
    state.save(clone_state)

    class TrackingClient(CloneSyncClient):
        def __init__(self):
            super().__init__([message(2)])
            self.iter_participants_calls = []

        async def iter_participants(self, entity, limit=None):
            self.iter_participants_calls.append(entity)
            if False:
                yield None

    client = TrackingClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["sync"]["discussion_cursor"] == 0
    assert result["sync"]["participants"]["discussion"] == {
        "peer_id": None,
        "status": "none",
        "count": 0,
        "reason": None,
    }
    assert client.iter_participants_calls == [client.source]
    # posts-only: never walks a discussion entity
    assert client.iter_messages_calls == [(0, True)]
    assert state.load(clone_state.clone_id).discussion_id_map == {}


def test_sync_skips_source_autoforwards(config_env, monkeypatch, capsys):
    """Anchors in the source discussion group are not copied;
    sync["skipped_autoforward"] counts them."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)], [anchor(10, 2), anchor(11, 3)]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["skipped_autoforward"] == 2
    assert sync["copied"] == 2
    assert sync["discussion_cursor"] == 11
    assert group_sends(client) == []
    assert state.load(clone_state.clone_id).discussion_id_map == {}


def test_sync_refuses_to_post_before_the_discussion_group_is_linked(
    config_env, monkeypatch, capsys
):
    """Init created the channel and enabled comments, then died before linking
    the group (a FLOOD_WAIT on the second peer does exactly this, live-proven).
    Posting now would burn every anchor: Telegram only creates them at send
    time with the link already in place, and they cannot be backfilled. Refuse
    instead, and send the user back to init."""
    seed_comments_clone(discussion_destination_peer_id=None, discussion_linked=False)
    client = CloneCommentsClient([message(2), message(3)], [])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)["error"]
    assert "not linked" in error["message"]
    assert client.requests == []


def test_sync_skips_album_autoforward_anchors(config_env, monkeypatch, capsys):
    """Telegram auto-forwards an album post as an album, so its anchor arrives
    as a multi-message batch. It is still an anchor and must not be copied."""
    clone_state = seed_comments_clone()
    album = [
        message(
            2,
            grouped_id=7,
            media=types.MessageMediaPhoto(
                photo=types.Photo(
                    id=1,
                    access_hash=1,
                    file_reference=b"r",
                    date=None,
                    sizes=[],
                    dc_id=2,
                )
            ),
        ),
        message(
            3,
            grouped_id=7,
            media=types.MessageMediaPhoto(
                photo=types.Photo(
                    id=2,
                    access_hash=2,
                    file_reference=b"r",
                    date=None,
                    sizes=[],
                    dc_id=2,
                )
            ),
        ),
    ]
    anchors = [anchor(10, 2), anchor(11, 3)]
    for item in anchors:
        item.grouped_id = 7
    client = CloneCommentsClient(album, anchors)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["skipped_autoforward"] == 2
    assert sync["discussion_cursor"] == 11
    assert group_sends(client) == []
    assert state.load(clone_state.clone_id).discussion_id_map == {}


def test_sync_keeps_the_quote_of_a_direct_comment(config_env, monkeypatch, capsys):
    """A comment replying straight to the anchor gets a rebuilt reply header;
    its quote must survive the rebuild."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12,
                message="agreed",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=10,
                    quote=True,
                    quote_text="the claim",
                    quote_offset=4,
                    quote_entities=[types.MessageEntityBold(offset=0, length=3)],
                ),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [send] = group_sends(client)
    assert send.reply_to.reply_to_msg_id == 500
    assert send.reply_to.quote_text == "the claim"
    assert send.reply_to.quote_offset == 4
    assert send.reply_to.quote_entities == [types.MessageEntityBold(offset=0, length=3)]


def test_sync_attaches_a_comment_to_its_post_thread(config_env, monkeypatch, capsys):
    """Comment replying to the source anchor is sent into the destination
    group with reply_to.reply_to_msg_id == the destination anchor id from
    getDiscussionMessage(dest_channel, dest_post_id)."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12,
                message="nice",
                from_id=types.PeerUser(77),
                sender_id=77,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex", username="alex")
        return await CloneCommentsClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [lookup] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.GetDiscussionMessageRequest)
    ]
    assert lookup.peer is client.destination and lookup.msg_id == 2
    [send] = group_sends(client)
    assert isinstance(send, functions.messages.SendMessageRequest)
    assert send.message == "Alex (@alex): \n\nnice"
    assert send.reply_to.reply_to_msg_id == 500
    assert send.reply_to.top_msg_id is None
    assert sync["reply_flattened"] == 0
    assert state.load(clone_state.clone_id).discussion_dest_for(12) == 501


def test_sync_maps_comment_on_comment_replies(config_env, monkeypatch, capsys):
    """Nested comment: reply_to_msg_id maps through discussion_id_map,
    top_msg_id through the anchor path."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12,
                message="root",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
            message(
                13,
                message="nested",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=12, reply_to_top_id=10
                ),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    root, nested = group_sends(client)
    assert root.reply_to.reply_to_msg_id == 500
    assert nested.reply_to.reply_to_msg_id == 501
    assert nested.reply_to.top_msg_id == 500


def test_sync_flattens_comments_with_unmapped_anchors(config_env, monkeypatch, capsys):
    """Anchor that maps to no destination post: message is still copied,
    sync["reply_flattened"] == 1."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 99),
            message(
                12,
                message="orphan",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["reply_flattened"] == 1
    assert sync["copied"] == 2
    [send] = group_sends(client)
    assert send.reply_to is None
    assert state.load(clone_state.clone_id).discussion_dest_for(12) is not None


def test_sync_copies_off_thread_group_messages(config_env, monkeypatch, capsys):
    """A plain group message with no reply header clones into the destination
    group through the megagroup transport rules, keeping its author header."""
    seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [message(12, message="hello", from_id=types.PeerUser(77), sender_id=77)],
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    [send] = group_sends(client)
    assert isinstance(send, functions.messages.ForwardMessagesRequest)
    assert send.from_peer is client.source_group
    assert send.id == [12]
    assert send.drop_author is False


def test_sync_advances_the_discussion_cursor_per_batch(config_env, monkeypatch, capsys):
    """discussion_cursor is saved after each confirmed batch and a rerun
    copies nothing."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2)],
        [
            anchor(10, 2),
            message(
                12, message="one", reply_to=types.MessageReplyHeader(reply_to_msg_id=10)
            ),
            message(
                13, message="two", reply_to=types.MessageReplyHeader(reply_to_msg_id=10)
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    client.group_last_id = 500
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    first = json.loads(capsys.readouterr().out)["sync"]
    assert first["discussion_cursor"] == 13
    saved = state.load(clone_state.clone_id)
    assert saved.discussion_cursor == 13
    assert [saved.discussion_dest_for(12), saved.discussion_dest_for(13)] == [501, 502]

    assert main(["clone", "sync", "@source", "--json"]) == 0
    rerun = json.loads(capsys.readouterr().out)["sync"]
    assert rerun["copied"] == 0
    assert rerun["skipped_autoforward"] == 0
    assert len(group_sends(client)) == 2
    assert client.iter_messages_calls == [
        (0, True),
        ("group", 0, True),
        (2, True),
        ("group", 13, True),
    ]


def test_sync_limit_spends_phase_one_first(config_env, monkeypatch, capsys):
    """--limit 1 with pending posts and comments: only a post batch is
    copied, more is True, discussion_cursor unchanged."""
    clone_state = seed_comments_clone()
    client = CloneCommentsClient(
        [message(2), message(3)],
        [
            anchor(10, 2),
            message(
                12,
                message="nice",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=10),
            ),
        ],
    )
    client.anchor_ids = {2: 500}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "1", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["more"] is True
    assert sync["discussion_cursor"] == 0
    assert sync["skipped_autoforward"] == 0
    assert group_sends(client) == []
    assert client.iter_messages_calls == [(0, True)]
    assert state.load(clone_state.clone_id).discussion_cursor == 0


def test_sync_tolerates_destination_autoforwards_in_the_tail(
    config_env, monkeypatch, capsys
):
    """Telegram's anchors in the destination group are not 'unexpected
    tail messages'."""
    clone_state = seed_comments_clone(discussion_cursor=12)
    clone_state.record_mapping(2, 2)
    clone_state.record_discussion_mapping(12, 3)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneCommentsClient([message(2)], [anchor(10, 2), message(12)])
    client.destination_last_id = 2
    client.group_last_id = 5
    client.group_tail = {
        4: anchor(4, 2, source_channel_id=999),
        5: anchor(5, 2, source_channel_id=999),
    }
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 0
    assert group_sends(client) == []


def test_sync_blocks_unexpected_discussion_destination_tail(
    config_env, monkeypatch, capsys
):
    clone_state = seed_comments_clone(discussion_cursor=12)
    clone_state.record_mapping(2, 2)
    clone_state.record_discussion_mapping(12, 3)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneCommentsClient([message(2)], [anchor(10, 2), message(12)])
    client.destination_last_id = 2
    client.group_last_id = 4
    client.group_tail = {4: message(4, message="stray")}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)["error"]
    assert "discussion destination has unexpected tail" in error["message"]
    assert error["unexpected"] == 1


def test_sync_skips_phase_two_when_comments_are_unavailable(
    config_env, monkeypatch, capsys
):
    """comments == "unavailable": posts copy, no discussion requests,
    skipped_autoforward == 0."""
    seed_comments_clone(
        comments="unavailable",
        discussion_destination_peer_id=None,
        discussion_linked=False,
    )
    client = CloneCommentsClient([message(2)], [anchor(10, 2), message(12)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["skipped_autoforward"] == 0
    assert sync["discussion_cursor"] == 0
    assert group_sends(client) == []
    assert client.iter_messages_calls == [(0, True)]


@pytest.mark.asyncio
async def test_create_topic_audit_failure_blocks_direct_mutation_and_state(
    config_env, monkeypatch
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    mutate_calls = []

    async def mutate(request):
        mutate_calls.append(request)
        pytest.fail("topic mutation dispatched")

    def fail_audit(action, account, details):
        assert (action, account, details) == (
            "clone-sync-topic",
            "main",
            {
                "clone_id": clone_state.clone_id,
                "source_topic_id": 2,
            },
        )
        raise PolicyError("audit write failed")

    monkeypatch.setattr(safety, "append_audit", fail_audit)

    with pytest.raises(PolicyError, match="audit write failed"):
        await topics.create_topic(
            mutate,
            forum_channel(999, "Forum chat", creator=True),
            clone_state,
            2,
            account_alias="main",
            title="News",
            icon_color=0,
        )

    assert mutate_calls == []
    assert clone_state.topic_map == {}
    assert state.load(clone_state.clone_id).topic_map == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "returned_topics",
    [
        [],
        [SimpleNamespace(id=2, title="")],
        [SimpleNamespace(id=2, title=None)],
        [SimpleNamespace(id=2, title="News"), SimpleNamespace(id=2, title="Duplicate")],
    ],
)
async def test_ensure_topic_rejects_missing_malformed_or_duplicate_lookup(
    config_env, returned_topics
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    counters = {"topics_created": 0}
    requests = []

    async def mutate(request):
        requests.append(request)
        return SimpleNamespace(topics=returned_topics)

    with pytest.raises(PolicyError, match="exactly one valid topic"):
        await topics.ensure_topic(
            mutate,
            object(),
            forum_channel(999, "Forum chat", creator=True),
            clone_state,
            2,
            counters,
            account_alias="main",
        )

    assert len(requests) == 1
    assert isinstance(requests[0], functions.messages.GetForumTopicsByIDRequest)
    assert counters == {"topics_created": 0}
    assert clone_state.topic_map == {}
    assert state.load(clone_state.clone_id).topic_map == {}
    assert not safety.audit_path().exists()


def test_clone_sync_copies_plain_text_oldest_first_and_reruns_idempotently(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(3), message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    participants = result["sync"].pop("participants")
    assert result["sync"] == {
        "copied": 2,
        "forwarded": 2,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "quote_flattened": [],
        "poll_votes": [],
        "skipped_service": 0,
        "skipped_unsupported": [],
        "skipped_autoforward": 0,
        "topics_created": 0,
        "cursor": 3,
        "discussion_cursor": 0,
        "more": False,
    }
    # a broadcast source the account does not administer reports no roster
    assert participants["source"]["status"] == "unavailable"
    assert participants["discussion"]["status"] == "none"
    assert [request.id for request in client.requests] == [[2], [3]]
    assert all(request.drop_author is True for request in client.requests)
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 3
    assert saved.dest_for(2) == 2
    assert saved.dest_for(3) == 3
    assert saved.last_synced_at is not None
    assert client.session_mutation_safe is True
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert [record["action"] for record in audits] == [
        "clone-sync-forward",
        "clone-sync-forward",
    ]

    assert main(["clone", "sync", "@source", "--json"]) == 0
    rerun = json.loads(capsys.readouterr().out)
    assert rerun["sync"]["copied"] == 0
    assert len(client.requests) == 2
    assert client.iter_messages_calls == [(0, True), (3, True)]


def test_clone_sync_keeps_native_forward_header_for_reforwarded_broadcast_post(
    config_env, monkeypatch, capsys
):
    """A broadcast source's own post drops its author (clone looks native), but a
    post that is itself a forward keeps drop_author=False so Telegram restores
    the original forward header instead of erasing the re-forward's origin."""
    seed_clone()
    own = message(2)
    reforward = message(
        3, fwd_from=types.MessageFwdHeader(date=None, from_name="Original Author")
    )
    client = CloneSyncClient([own, reforward])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    by_id = {
        tuple(request.id): request.drop_author
        for request in client.requests
        if isinstance(request, functions.messages.ForwardMessagesRequest)
    }
    assert by_id == {(2,): True, (3,): False}


def test_clone_sync_keeps_native_forward_header_for_reforwarded_album(
    config_env, monkeypatch, capsys
):
    """A forwarded album carries fwd_from on every item, so the whole batch
    forwards with drop_author=False and keeps its origin header."""
    seed_clone()
    header = types.MessageFwdHeader(date=None, from_name="Original Author")
    album = [
        message(2, grouped_id=77, fwd_from=header),
        message(3, grouped_id=77, fwd_from=header),
    ]
    client = CloneSyncClient(album)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.ForwardMessagesRequest)
    ]
    assert request.id == [2, 3]
    assert request.drop_author is False


def test_clone_sync_creates_destination_topic_from_topic_create_service(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([topic_create(2, "News")])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.CreateForumTopicRequest)
    assert request.title == "News"
    assert sync["topics_created"] == 1
    assert sync["skipped_service"] == 0
    assert sync["copied"] == 0
    saved = state.load(clone_state.clone_id)
    assert saved.topic_dest_for(2) == 2
    assert saved.cursor == 2

    assert main(["clone", "sync", "@source", "--json"]) == 0
    rerun = json.loads(capsys.readouterr().out)["sync"]
    assert rerun["topics_created"] == 0
    assert len(client.requests) == 1


def test_clone_sync_replayed_mapped_topic_advances_without_duplicate_mutation(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 2)
    state.save(clone_state)
    client = CloneForumClient([topic_create(2, "News")])
    client.destination_last_id = 2
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["topics_created"] == 0
    assert sync["skipped_service"] == 0
    assert client.requests == []
    assert state.load(clone_state.clone_id).cursor == 2
    assert not safety.audit_path().exists()


def test_clone_sync_topic_audit_failure_blocks_before_mutation(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([topic_create(2, "News")])
    make_session_fake(monkeypatch, client)
    audit_calls = []

    def fail_audit(action, account, details):
        audit_calls.append((action, account, details))
        raise PolicyError("audit write failed")

    monkeypatch.setattr(safety, "append_audit", fail_audit)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "audit write failed" in capsys.readouterr().err
    assert audit_calls == [
        (
            "clone-sync-topic",
            "main",
            {
                "clone_id": clone_state.clone_id,
                "source_topic_id": 2,
            },
        )
    ]
    assert client.requests == []
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.topic_map == {}


def test_clone_sync_forwards_topic_message_into_mapped_topic(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneForumClient(
        [
            message(
                3,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=2, forum_topic=True),
            ),
        ]
    )
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.top_msg_id == 1002
    assert request.drop_author is False
    assert sync["forwarded"] == 1
    assert sync["reply_flattened"] == 0
    assert "clone-sync-topic" not in safety.audit_path().read_text()


def test_clone_sync_forwards_general_topic_message_without_top_id(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.top_msg_id is None
    assert "clone-sync-topic" not in safety.audit_path().read_text()


def test_clone_sync_reuploads_topic_reply_with_prefix_into_topic(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 1002)
    clone_state.record_mapping(3, 1003)
    clone_state.cursor = 3
    state.save(clone_state)
    client = CloneForumClient(
        [
            message(
                4,
                message="pong",
                from_id=types.PeerUser(77),
                sender_id=77,
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=3, reply_to_top_id=2, forum_topic=True
                ),
            ),
        ]
    )
    client.destination_last_id = 1003

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMessageRequest)
    ]
    assert request.message == "Alex: \n\npong"
    assert request.reply_to.reply_to_msg_id == 1003
    assert request.reply_to.top_msg_id == 1002
    assert sync["reuploaded"] == 1


def test_clone_sync_recovers_unmapped_topic_from_source_lookup(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient(
        [
            message(
                5,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=2, forum_topic=True),
            ),
        ]
    )
    client.source_topic_titles = {2: "Old news"}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    lookups = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.GetForumTopicsByIDRequest)
    ]
    created = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.CreateForumTopicRequest)
    ]
    assert len(lookups) == 1 and len(created) == 1
    assert created[0].title == "Old news"
    assert sync["topics_created"] == 1
    forward = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.ForwardMessagesRequest)
    ]
    assert forward[0].top_msg_id == 2


def test_clone_sync_topic_recovery_audit_failure_blocks_copy_and_state(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient(
        [
            message(
                5,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=2, forum_topic=True),
            ),
        ]
    )
    client.source_topic_titles = {2: "Old news"}
    make_session_fake(monkeypatch, client)

    def fail_topic_audit(action, account, details):
        if action == "clone-sync-topic":
            raise PolicyError("audit write failed")

    monkeypatch.setattr(safety, "append_audit", fail_topic_audit)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "audit write failed" in capsys.readouterr().err
    assert [type(item) for item in client.requests] == [
        functions.messages.GetForumTopicsByIDRequest,
    ]
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.topic_map == {} and saved.id_map == {}


def test_clone_sync_topic_recovery_reuses_mapping_after_copy_failure(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")

    class FailOnceForumClient(CloneForumClient):
        failed = False

        async def __call__(self, request):
            if (
                isinstance(request, functions.messages.ForwardMessagesRequest)
                and not self.failed
            ):
                self.failed = True
                self.requests.append(request)
                raise PolicyError("copy failed")
            return await super().__call__(request)

    client = FailOnceForumClient(
        [
            message(
                5,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=2, forum_topic=True),
            ),
        ]
    )
    client.source_topic_titles = {2: "Old news"}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2
    assert "copy failed" in capsys.readouterr().err
    failed = state.load(clone_state.clone_id)
    assert failed.topic_dest_for(2) == 2
    assert failed.cursor == 0 and failed.id_map == {}

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["topics_created"] == 0
    assert (
        len(
            [
                item
                for item in client.requests
                if isinstance(item, functions.messages.GetForumTopicsByIDRequest)
            ]
        )
        == 1
    )
    assert (
        len(
            [
                item
                for item in client.requests
                if isinstance(item, functions.messages.CreateForumTopicRequest)
            ]
        )
        == 1
    )
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 5 and saved.dest_for(5) == 3


def test_clone_sync_copies_cross_peer_forum_header_without_wedging(
    config_env, monkeypatch, capsys
):
    """Foreign-peer forum header: probe fails → quote fallback, PartialFailure."""
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    client = CloneForumClient(
        [
            message(
                5,
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=2,
                    forum_topic=True,
                    reply_to_peer_id=types.PeerChannel(321),
                ),
            ),
        ]
    )
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["reuploaded"] == 1
    assert sync["quote_flattened"] == [{"id": 5, "peer": 321, "reason": "unreachable"}]
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 5
    assert saved.dest_for(5) is not None


@pytest.mark.parametrize("topic_id", [True, "2", -2, 2_147_483_648])
def test_clone_sync_rejects_malformed_forum_placement_before_recovery(
    topic_id, config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient(
        [
            message(
                5,
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=topic_id, forum_topic=True
                ),
            ),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "reply parent is invalid" in capsys.readouterr().err
    assert client.requests == []
    assert not safety.audit_path().exists()
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.topic_map == {} and saved.id_map == {}


@pytest.mark.parametrize("topic_id", [True, "2", -2, 2_147_483_648])
def test_topic_id_of_rejects_malformed_forum_placement(topic_id):
    header = types.MessageReplyHeader(reply_to_msg_id=topic_id, forum_topic=True)

    with pytest.raises(PolicyError, match="topic id is invalid"):
        topics.topic_id_of(message(5, reply_to=header))


@pytest.mark.parametrize("topic_id", [True, "2", -2, 2_147_483_648])
def test_topic_id_of_rejects_malformed_forum_reply_topic(topic_id):
    header = types.MessageReplyHeader(
        reply_to_msg_id=3, reply_to_top_id=topic_id, forum_topic=True
    )

    with pytest.raises(PolicyError, match="topic id is invalid"):
        topics.topic_id_of(message(5, reply_to=header))


@pytest.mark.parametrize(
    "overrides,error",
    [
        ({"reply_to_top_id": True}, "reply parent is invalid"),
        ({"reply_to_top_id": "2"}, "reply parent is invalid"),
        ({"reply_to_top_id": -2}, "reply parent is invalid"),
        ({"reply_to_top_id": 2_147_483_648}, "reply parent is invalid"),
        ({"reply_to_top_id": 2, "quote_text": 7}, "reply quote is invalid"),
    ],
)
def test_clone_sync_rejects_malformed_forum_reply_before_recovery(
    overrides, error, config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient(
        [
            message(
                5,
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=3, forum_topic=True, **overrides
                ),
            ),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert error in capsys.readouterr().err
    assert client.requests == []
    assert not safety.audit_path().exists()
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.topic_map == {} and saved.id_map == {}


def test_clone_sync_rejects_oversized_forum_reply_parent_before_recovery(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient(
        [
            message(
                5,
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=2_147_483_648, reply_to_top_id=2, forum_topic=True
                ),
            ),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "reply parent is invalid" in capsys.readouterr().err
    assert client.requests == []
    assert not safety.audit_path().exists()
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.topic_map == {} and saved.id_map == {}


@pytest.mark.parametrize("top_id", [None, 2_147_483_647])
def test_topic_id_of_accepts_maximum_tl_int(top_id):
    header = types.MessageReplyHeader(
        reply_to_msg_id=2_147_483_647, reply_to_top_id=top_id, forum_topic=True
    )

    assert topics.topic_id_of(message(5, reply_to=header)) == 2_147_483_647


def test_clone_sync_rejects_mixed_topic_placement_album_before_recovery(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient(
        [
            message(
                5,
                grouped_id=44,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=2, forum_topic=True),
            ),
            message(
                6,
                grouped_id=44,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=3, forum_topic=True),
            ),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "album reply metadata is inconsistent" in capsys.readouterr().err
    assert client.requests == []
    assert not safety.audit_path().exists()
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.topic_map == {} and saved.id_map == {}


def test_clone_sync_forwards_same_topic_placement_album(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    header = types.MessageReplyHeader(reply_to_msg_id=2, forum_topic=True)
    client = CloneForumClient(
        [
            message(3, grouped_id=44, reply_to=header),
            message(4, grouped_id=44),
        ]
    )
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["forwarded"] == 2
    [request] = client.requests
    assert request.id == [3, 4] and request.top_msg_id == 1002


def test_clone_sync_flattens_forum_reply_header_for_nonforum_clone(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneSyncClient(
        [
            message(
                2,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=1, forum_topic=True),
            ),
        ]
    )
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["reply_flattened"] == 1


def test_clone_sync_forwards_megagroup_nonreply_with_author_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneSyncClient([message(2)])
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["source"]["kind"] == "megagroup"
    assert result["sync"]["forwarded"] == 1
    assert result["sync"]["reuploaded"] == 0
    assert client.requests[0].drop_author is False


def test_clone_sync_forwards_basic_group_nonreply_with_author_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="basic", title="Legacy group")
    client = CloneSyncClient([message(2)])
    client.source = legacy_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert client.requests[0].drop_author is False
    assert sync["forwarded"] == 1


def test_clone_sync_reuploads_megagroup_reply_with_prefix_and_shifted_entities(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="megagroup", title="Team chat")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    original = types.MessageEntityBold(offset=2, length=4)
    client = CloneReuploadClient(
        [
            message(
                2,
                message="😀bold",
                entities=[original],
                from_id=types.PeerUser(77),
                sender_id=77,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=1),
            )
        ]
    )
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Zoë 🚀")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message == "Zoë 🚀: \n\n😀bold"
    assert request.reply_to.reply_to_msg_id == 1001
    prefix_units = len("Zoë 🚀: \n\n".encode("utf-16-le")) // 2
    mention, shifted = request.entities
    assert mention == types.MessageEntityMentionName(
        offset=0, length=len("Zoë 🚀".encode("utf-16-le")) // 2, user_id=77
    )
    assert shifted.offset == prefix_units + 2
    assert original.offset == 2
    assert sync["reuploaded"] == 1
    assert sync["reply_flattened"] == 0


def test_clone_sync_reuploads_protected_megagroup_with_cached_author_prefix(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneReuploadClient(
        [
            message(2, message="first", from_id=types.PeerUser(77), sender_id=77),
            message(3, message="second", from_id=types.PeerUser(77), sender_id=77),
        ],
        protected=True,
    )
    client.source = channel(
        123,
        "Team chat",
        broadcast=False,
        megagroup=True,
        forum=False,
        noforwards=True,
    )
    author_lookups = 0

    async def get_entity(ref):
        nonlocal author_lookups
        if isinstance(ref, types.PeerUser):
            author_lookups += 1
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    sends = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMessageRequest)
    ]
    assert [item.message for item in sends] == ["Alex: \n\nfirst", "Alex: \n\nsecond"]
    assert author_lookups == 1
    assert sync["reuploaded"] == 2


def test_clone_sync_forwards_private_dialog_nonreply_with_author_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="dialog", title="Alex Smith")
    client = CloneSyncClient([message(2)])
    client.source = types.User(id=123, first_name="Alex", last_name="Smith")
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert client.requests[0].drop_author is False
    assert sync["forwarded"] == 1


def test_clone_sync_reuploads_private_dialog_reply_with_explicit_source_peer(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="dialog", title="Alex Smith")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1, reply_to_peer_id=types.PeerUser(123)
    )
    client = CloneReuploadClient(
        [
            message(
                2,
                message="answer",
                from_id=types.PeerUser(123),
                sender_id=123,
                reply_to=reply,
            )
        ]
    )
    client.source = types.User(id=123, first_name="Alex", last_name="Smith")
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return client.source
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message == "Alex Smith: \n\nanswer"
    assert request.reply_to.reply_to_msg_id == 1001


def test_clone_sync_reuploads_basic_group_reply_with_explicit_source_peer(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="basic", title="Legacy group")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1, reply_to_peer_id=types.PeerChat(123)
    )
    client = CloneReuploadClient([message(2, message="answer", reply_to=reply)])
    client.source = legacy_group()
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.reply_to.reply_to_msg_id == 1001


def test_clone_sync_reuploads_basic_group_reply_with_prefix(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="basic", title="Legacy group")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    client = CloneReuploadClient(
        [
            message(
                2,
                message="pong",
                from_id=types.PeerUser(77),
                sender_id=77,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=1),
            ),
        ]
    )
    client.source = legacy_group()
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMessageRequest)
    ]
    assert request.message == "Alex: \n\npong"
    assert request.reply_to.reply_to_msg_id == 1001
    assert sync["reuploaded"] == 1


def test_clone_sync_copies_basic_group_foreign_peer_reply_without_wedging(
    config_env, monkeypatch, capsys
):
    """Different-source-peer reply: unreachable probe → fallback + PartialFailure."""
    clone_state = seed_clone(kind="basic", title="Legacy group")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1, reply_to_peer_id=types.PeerChat(456)
    )
    client = CloneReuploadClient([message(2, reply_to=reply)])
    client.source = legacy_group()
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["reuploaded"] == 1
    assert sync["quote_flattened"] == [{"id": 2, "peer": 456, "reason": "unreachable"}]
    assert state.load(clone_state.clone_id).dest_for(2) is not None


def test_clone_sync_quote_fallback_exits_partial_with_result_document(
    config_env, monkeypatch, capsys
):
    """Unreachable foreign quote completes the run, emits JSON, exits PolicyError."""
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1244,
        reply_to_peer_id=types.PeerChannel(2275285084),
        quote_text="что это де-факто не наставничество",
    )
    client = CloneReuploadClient([message(2, reply_to=reply)])
    client.destination_last_id = 1
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    captured = capsys.readouterr()
    assert "reply shape is not supported" not in captured.err
    result = json.loads(captured.out)
    sync = result["sync"]
    assert sync["copied"] == 1
    assert sync["quote_flattened"] == [
        {"id": 2, "peer": 2275285084, "reason": "unreachable"}
    ]
    assert state.load(clone_state.clone_id).cursor == 2


def test_clone_sync_quote_fallback_plain_reports_count_and_exits_nonzero(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=9, reply_to_peer_id=types.PeerChannel(99)
    )
    client = CloneReuploadClient([message(2, reply_to=reply)])
    client.destination_last_id = 1
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--plain"]) == 2

    cols = capsys.readouterr().out.rstrip().split("\t")
    assert cols[0] == "1"  # copied
    assert cols[4] == "0"  # reply_flattened
    assert cols[5] == "1"  # quote_flattened_count
    assert cols[10] == clone_state.clone_id


def test_clone_sync_clean_run_has_empty_quote_flattened(
    config_env, monkeypatch, capsys
):
    seed_clone()
    client = CloneSyncClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["quote_flattened"] == []
    assert sync["copied"] == 1


def test_clone_sync_reports_attributed_unmapped_reply_forward_fallback(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneSyncClient(
        [message(2, reply_to=types.MessageReplyHeader(reply_to_msg_id=1))]
    )
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.drop_author is False
    assert sync["reply_flattened"] == 1


def test_clone_sync_flattens_private_dialog_story_reply_header(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="dialog", title="Alex Smith")
    story_reply = types.MessageReplyStoryHeader(
        peer=types.PeerUser(42),
        story_id=261,
    )
    client = CloneSyncClient([message(2, reply_to=story_reply)])
    client.source = types.User(id=123, first_name="Alex", last_name="Smith")
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.drop_author is False
    assert sync["reply_flattened"] == 1


def test_clone_sync_blocks_unexpected_destination_tail_before_copy(
    config_env, monkeypatch, capsys
):
    seed_clone()
    client = CloneSyncClient([message(2)])
    client.destination_last_id = 2
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    error = json.loads(capsys.readouterr().err)["error"]
    assert error["unexpected"] == 1
    assert "unexpected tail" in error["message"]
    assert client.requests == []
    assert client.iter_messages_calls == []
    assert not safety.audit_path().exists()


def test_clone_sync_blocks_when_recorded_tail_is_missing(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 5)
    clone_state.cursor = 1
    state.save(clone_state)
    client = CloneSyncClient([message(2)])
    client.destination_last_id = 4
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "recorded tail is missing" in capsys.readouterr().err
    assert client.requests == []
    assert client.iter_messages_calls == []
    assert not safety.audit_path().exists()


def test_clone_sync_rejects_recorded_forum_clone_with_nonforum_destination(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="forum", title="Forum chat")
    client = CloneSyncClient([message(2)])
    client.source = channel(
        123, "Forum chat", broadcast=False, megagroup=True, forum=True
    )
    client.destination = channel(
        999,
        "Forum chat",
        creator=True,
        broadcast=False,
        megagroup=True,
        forum=False,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "not a private owned forum megagroup" in capsys.readouterr().err
    assert client.requests == []
    assert client.iter_messages_calls == []


def test_clone_sync_accepts_service_only_destination_tail(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2)])
    client.destination_last_id = 2
    client.destination_actions[2] = object()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"]["copied"] == 1
    assert state.load(clone_state.clone_id).dest_for(2) == 3


def test_clone_sync_blocks_unmapped_topic_create_tail_before_retrying_create(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")

    class AcceptedWithoutConfirmationClient(CloneForumClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.CreateForumTopicRequest):
                self.requests.append(request)
                self.destination_last_id += 1
                self.destination_actions[self.destination_last_id] = (
                    types.MessageActionTopicCreate(title=request.title, icon_color=0)
                )
                return SimpleNamespace(updates=[])
            return await super().__call__(request)

    client = AcceptedWithoutConfirmationClient([topic_create(2, "News")])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2
    assert "did not confirm" in capsys.readouterr().err
    assert len(client.requests) == 1
    assert state.load(clone_state.clone_id).topic_map == {}

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "unexpected tail" in capsys.readouterr().err
    assert len(client.requests) == 1
    assert client.iter_messages_calls == [(0, True)]
    assert state.load(clone_state.clone_id).topic_map == {}


def test_clone_sync_accepts_mapped_topic_create_destination_tail(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    clone_state.record_topic(2, 2)
    state.save(clone_state)
    client = CloneForumClient([message(3)])
    client.destination_last_id = 3
    client.destination_actions[2] = types.MessageActionTopicCreate(
        title="News", icon_color=0
    )
    client.destination_actions[3] = object()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1


def test_clone_sync_skips_service_and_reports_unsupported_messages(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    poll_media = type("MessageMediaPoll", (), {})()
    dice_media = type("MessageMediaDice", (), {})()
    client = CloneSyncClient(
        [
            message(1, action=object()),
            message(2, media=poll_media),
            message(3, media=dice_media),
            message(4),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    sync.pop("participants")
    assert sync == {
        "copied": 1,
        "forwarded": 1,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "quote_flattened": [],
        "poll_votes": [],
        "skipped_service": 1,
        "skipped_unsupported": [
            {"id": 2, "kind": "MessageMediaPoll"},
            {"id": 3, "kind": "MessageMediaDice"},
        ],
        "skipped_autoforward": 0,
        "topics_created": 0,
        "cursor": 4,
        "discussion_cursor": 0,
        "more": False,
    }
    assert [request.id for request in client.requests] == [[4]]
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 4
    assert saved.dest_for(4) == 2


@pytest.mark.parametrize(
    "media",
    [
        types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7), ttl_seconds=5),
        types.MessageMediaDocument(document=types.DocumentEmpty(id=8), ttl_seconds=5),
    ],
)
def test_clone_sync_skips_view_once_media(media, config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2, media=media)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 0
    assert sync["skipped_unsupported"] == [
        {
            "id": 2,
            "kind": f"{type(media).__name__}TTL",
        }
    ]
    assert client.requests == []
    assert state.load(clone_state.clone_id).cursor == 2


@pytest.mark.parametrize(
    "multiple_choice",
    [False, True],
)
def test_clone_sync_replaces_poll_with_result_snapshot(
    multiple_choice, config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneReuploadClient(
        [message(2, media=poll_media(multiple_choice=multiple_choice))]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["skipped_unsupported"] == []
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message.startswith("📊 Результаты опроса\n\nChoose\n\n")
    assert "Choose" in request.message
    assert "First\n████░░░░░░ 40% · 4 голоса" in request.message
    assert "Second\n██████░░░░ 60% · 6 голосов" in request.message
    assert request.message.endswith("Проголосовало: 10")
    assert "snapshot" not in request.message.casefold()
    assert "clone" not in request.message.casefold()
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_poll_without_breakdown_casts_and_retracts_vote(
    config_env, monkeypatch, capsys
):
    seed_clone()
    media = poll_media(results=[], total_voters=10, public_voters=False, quiz=False)
    client = CloneReuploadClient([message(2, media=media)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    votes = [
        req
        for req in client.requests
        if isinstance(req, functions.messages.SendVoteRequest)
    ]
    assert len(votes) == 2
    assert votes[0].options == [b"a"]
    assert votes[1].options == []
    [send] = [
        req
        for req in client.requests
        if isinstance(req, functions.messages.SendMessageRequest)
    ]
    assert "40% · 4 голоса" in send.message
    assert "Проголосовало: 10" in send.message
    assert out["sync"]["poll_votes"] == [{"message_id": 2, "status": "captured"}]


def test_clone_sync_no_send_blocks_before_reaching_a_poll(monkeypatch):
    """TGCLI_NO_SEND cannot let a poll vote slip through, because it stops
    the whole sync in preflight (ADR-0048 §4). The renderer's own gate is a
    second line of defence, unit-tested in test_clone_snapshot.py."""
    from tgcli import cli

    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["clone", "sync", "@source", "--json"]) == 2
    assert not safety.audit_path().exists()


def test_clone_sync_replaces_unavailable_story_with_named_placeholder(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    story = types.MessageMediaStory(peer=types.PeerUser(88), id=5558)

    class StoryClient(CloneReuploadClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerUser):
                return SimpleNamespace(
                    id=88,
                    first_name="Andrey",
                    last_name="Kozlov",
                    username="targetdaddy",
                )
            return await super().get_entity(ref)

    client = StoryClient([message(2, media=story)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["skipped_unsupported"] == []
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.message == "Stories недоступна\nАвтор: Andrey Kozlov"
    [entity] = request.entities
    prefix = "Stories недоступна\nАвтор: "
    assert isinstance(entity, types.MessageEntityTextUrl)
    assert entity.offset == len(prefix.encode("utf-16-le")) // 2
    assert entity.length == len("Andrey Kozlov".encode("utf-16-le")) // 2
    assert entity.url == "https://t.me/targetdaddy"
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_maps_reply_to_story_placeholder(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    story = types.MessageMediaStory(peer=types.PeerChannel(77), id=346)

    class StoryReplyClient(CloneReuploadClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerChannel) and ref.channel_id == 77:
                return channel(77, "Story source", username="storysource")
            return await super().get_entity(ref)

    client = StoryReplyClient(
        [
            message(2, media=story),
            message(
                3, message="Reply", reply_to=types.MessageReplyHeader(reply_to_msg_id=2)
            ),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    assert isinstance(client.requests[0], functions.messages.SendMessageRequest)
    assert client.requests[0].message == "Stories недоступна\nАвтор: Story source"
    assert client.requests[0].entities[0].url == "https://t.me/storysource"
    assert isinstance(client.requests[1], functions.messages.SendMessageRequest)
    assert client.requests[1].reply_to.reply_to_msg_id == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [2, 3]


@pytest.mark.parametrize(
    "media",
    [
        types.MessageMediaWebPage(webpage=types.WebPageEmpty(id=6)),
        types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7)),
        types.MessageMediaDocument(document=types.DocumentEmpty(id=8)),
    ],
)
def test_clone_sync_forwards_native_media_without_flattening_caption(
    media, config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2, media=media, message="photo caption")])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"]["copied"] == 1
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 2
    assert saved.dest_for(2) == 2
    assert [request.id for request in client.requests] == [[2]]
    assert client.requests[0].drop_media_captions is None


def test_clone_sync_keeps_grouped_id_zero_album_atomic_and_in_position(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    document = types.MessageMediaDocument(document=types.DocumentEmpty(id=8))
    client = CloneSyncClient(
        [
            message(2),
            message(3, media=photo, grouped_id=0),
            message(4, media=document, grouped_id=0),
            message(5),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    result["sync"].pop("participants")
    assert result["sync"] == {
        "copied": 4,
        "forwarded": 4,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "quote_flattened": [],
        "poll_votes": [],
        "skipped_service": 0,
        "skipped_unsupported": [],
        "skipped_autoforward": 0,
        "topics_created": 0,
        "cursor": 5,
        "discussion_cursor": 0,
        "more": False,
    }
    assert [request.id for request in client.requests] == [[2], [3, 4], [5]]
    assert len(client.requests[1].random_id) == 2
    assert len(set(client.requests[1].random_id)) == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(source_id) for source_id in (2, 3, 4, 5)] == [2, 3, 4, 5]


def test_clone_sync_rejects_duplicate_album_confirmation_atomically(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class DuplicateConfirmationClient(CloneSyncClient):
        async def __call__(self, request):
            response = await super().__call__(request)
            response.updates.append(response.updates[0])
            return response

    client = DuplicateConfirmationClient(
        [message(2, grouped_id=44), message(3, grouped_id=44)]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "complete cloned batch" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.id_map == {}


def test_clone_sync_reuploads_reply_to_mapped_parent_with_quote(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    quote_entities = [types.MessageEntityBold(offset=0, length=5)]
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1,
        quote_text="quote",
        quote_entities=quote_entities,
        quote_offset=2,
    )
    client = CloneReuploadClient([message(2, message="child", reply_to=reply)])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.reply_to.reply_to_msg_id == 1001
    assert request.reply_to.quote_text == "quote"
    assert request.reply_to.quote_entities == quote_entities
    assert request.reply_to.quote_offset == 2
    assert state.load(clone_state.clone_id).dest_for(2) == 1002


def test_clone_sync_reuploads_nested_reply_with_mapped_root(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.record_mapping(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    reply = types.MessageReplyHeader(reply_to_msg_id=2, reply_to_top_id=1)
    client = CloneReuploadClient([message(3, message="nested", reply_to=reply)])
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    [request] = client.requests
    assert request.reply_to.reply_to_msg_id == 1002
    assert request.reply_to.top_msg_id == 1001


def test_clone_sync_preserves_mapped_parent_when_nested_root_is_unmapped(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(2, 1002)
    clone_state.cursor = 2
    state.save(clone_state)
    reply = types.MessageReplyHeader(reply_to_msg_id=2, reply_to_top_id=1)
    client = CloneReuploadClient([message(3, message="nested", reply_to=reply)])
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMessageRequest)
    assert request.reply_to.reply_to_msg_id == 1002
    assert request.reply_to.top_msg_id is None
    assert sync["reply_flattened"] == 0


def test_clone_sync_blocks_reply_quote_entities_without_quote_text(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    reply = types.MessageReplyHeader(
        reply_to_msg_id=1,
        quote_entities=[types.MessageEntityBold(offset=0, length=1)],
    )
    client = CloneReuploadClient([message(2, reply_to=reply)])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "reply quote" in capsys.readouterr().err
    assert state.load(clone_state.clone_id).dest_for(2) is None
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_clone_sync_flattens_reply_without_mapped_parent(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneReuploadClient(
        [message(2, reply_to=types.MessageReplyHeader(reply_to_msg_id=1))]
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 1
    assert sync["reply_flattened"] == 1
    [request] = client.requests
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.reply_to is None
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_reuploads_protected_photo_with_caption_and_cleans_tempfile(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient(
        [message(2, message="caption", media=photo)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    [request] = client.requests
    assert isinstance(request, functions.messages.SendMediaRequest)
    assert isinstance(request.media, types.InputMediaUploadedPhoto)
    assert request.message == "caption"
    assert len(client.uploads) == 1
    assert all(
        not path.exists() and not path.parent.exists() for path in client.downloads
    )
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_download_failure_keeps_protected_batch_retryable(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class DownloadFailureClient(CloneReuploadClient):
        async def download_media(self, message, file=None):
            return None

    media = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = DownloadFailureClient([message(2, media=media)], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "media download failed" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.id_map == {}
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_clone_sync_accepts_short_sent_confirmation_for_protected_text(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class ShortSentClient(CloneReuploadClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.SendMessageRequest):
                self.requests.append(request)
                self.destination_last_id += 1
                return types.UpdateShortSentMessage(
                    id=self.destination_last_id,
                    out=True,
                    pts=1,
                    pts_count=1,
                    date=None,
                )
            return await super().__call__(request)

    client = ShortSentClient([message(2, message="protected")], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 1
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_reuploads_protected_document_preserving_metadata(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    attributes = [types.DocumentAttributeFilename("report.txt")]
    document = types.MessageMediaDocument(
        document=SimpleNamespace(
            mime_type="text/plain",
            attributes=attributes,
        )
    )
    client = CloneReuploadClient(
        [message(2, message="document", media=document)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = client.requests
    assert isinstance(request.media, types.InputMediaUploadedDocument)
    assert request.media.mime_type == "text/plain"
    assert request.media.attributes == attributes
    assert request.message == "document"
    assert state.load(clone_state.clone_id).dest_for(2) == 2


def test_clone_sync_reuploads_protected_album_as_one_ordered_batch(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient(
        [
            message(2, message="first", media=photo, grouped_id=5),
            message(3, message="second", media=photo, grouped_id=5),
        ],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    uploads = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.UploadMediaRequest)
    ]
    [multi] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMultiMediaRequest)
    ]
    assert len(uploads) == 2
    assert [item.message for item in multi.multi_media] == ["first", "second"]
    assert len({item.random_id for item in multi.multi_media}) == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [2, 3]
    assert all(
        not path.exists() and not path.parent.exists() for path in client.downloads
    )


def test_clone_sync_reuploads_open_reply_album_to_mapped_parent(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    reply = types.MessageReplyHeader(reply_to_msg_id=1, quote_text="album")
    client = CloneReuploadClient(
        [
            message(2, media=photo, grouped_id=44, reply_to=reply),
            message(3, media=photo, grouped_id=44),
        ]
    )
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMultiMediaRequest)
    ]
    assert request.reply_to.reply_to_msg_id == 1001
    assert request.reply_to.quote_text == "album"
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [1002, 1003]


def test_clone_sync_prefixes_attributed_reply_album_caption_once(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="megagroup", title="Team chat")
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    reply = types.MessageReplyHeader(reply_to_msg_id=1)
    client = CloneReuploadClient(
        [
            message(
                2,
                message="caption",
                media=photo,
                grouped_id=44,
                reply_to=reply,
                from_id=types.PeerUser(77),
                sender_id=77,
            ),
            message(
                3,
                message="",
                media=photo,
                grouped_id=44,
                from_id=types.PeerUser(77),
                sender_id=77,
            ),
        ]
    )
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    client.destination_last_id = 1001

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    capsys.readouterr()
    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.SendMultiMediaRequest)
    ]
    assert [item.message for item in request.multi_media] == [
        "Alex: \n\ncaption",
        "",
    ]


def test_clone_sync_limit_reports_more_and_next_run_resumes(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(4), message(2), message(3)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--limit", "2", "--json"]) == 0

    first = json.loads(capsys.readouterr().out)["sync"]
    assert first["copied"] == 2
    assert first["cursor"] == 3
    assert first["more"] is True
    assert [request.id for request in client.requests] == [[2], [3]]

    assert main(["clone", "sync", "@source", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)["sync"]
    assert second["copied"] == 1
    assert second["cursor"] == 4
    assert second["more"] is False
    assert [request.id for request in client.requests] == [[2], [3], [4]]
    assert state.load(clone_state.clone_id).dest_for(4) == 4


def test_clone_sync_flood_wait_persists_cooldown_without_advancing(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class FloodClient(CloneSyncClient):
        async def __call__(self, request):
            self.requests.append(request)
            raise telethon_errors.FloodWaitError(request=request, capture=600)

    client = FloodClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5

    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.dest_for(2) is None
    assert saved.cooldown_deadline() is not None
    assert len(client.requests) == 1
    assert client.session_mutation_safe is True


def test_clone_sync_flood_wait_arms_account_cooldown_for_other_clones(
    config_env, monkeypatch, capsys
):
    from tgcli.clone import flood

    seed_clone()
    other = state.CloneState.new(
        account_user_id=42,
        source_peer_id=456,
        source_title="Other channel",
        source_kind="broadcast",
    )
    other.destination_peer_id = 888
    state.save(other)

    class FloodClient(CloneSyncClient):
        async def __call__(self, request):
            self.requests.append(request)
            raise telethon_errors.FloodWaitError(request=request, capture=600)

    client = FloodClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    assert flood.cooldown_deadline(42) is not None
    first_requests = len(client.requests)
    capsys.readouterr()

    class OtherClient(CloneSyncClient):
        def __init__(self):
            super().__init__([])
            self.source = channel(456, "Other channel")
            self.destination = channel(888, "Other channel", creator=True)

        async def get_entity(self, ref):
            if isinstance(ref, types.PeerChannel):
                if ref.channel_id == 888:
                    return self.destination
                raise ValueError("peer not found")
            assert ref == "@other"
            return self.source

    other_client = OtherClient()
    make_session_fake(monkeypatch, other_client)

    assert main(["clone", "sync", "@other", "--json"]) == 5
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] > 0
    assert other_client.requests == []
    assert first_requests == 1


def test_clone_sync_account_cooldown_blocks_before_network(
    config_env, monkeypatch, capsys
):
    from datetime import UTC, datetime, timedelta

    from tgcli.clone import flood

    seed_clone()
    flood.arm_cooldown(42, datetime.now(UTC) + timedelta(minutes=10))

    client = CloneSyncClient([message(2)])
    entity_calls = []

    original_get_entity = client.get_entity

    async def tracking_get_entity(ref):
        entity_calls.append(ref)
        return await original_get_entity(ref)

    client.get_entity = tracking_get_entity  # type: ignore[method-assign]
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] > 0
    assert client.requests == []
    assert entity_calls == []


def test_clone_sync_readonly_blocks_before_config_or_session(monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["--readonly", "clone", "sync", "@source"]) == 2
    assert not safety.audit_path().exists()


def test_clone_sync_unmatched_confirmation_does_not_advance_state(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()

    class UnmatchedClient(CloneSyncClient):
        async def __call__(self, request):
            response = await super().__call__(request)
            response.updates[0].random_id += 1
            return response

    client = UnmatchedClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "did not confirm" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.id_map == {}
    assert client.destination_last_id == 2


def test_clone_sync_plain_output_has_contract_columns(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--plain"]) == 0

    assert capsys.readouterr().out.rstrip().split("\t") == [
        "1",
        "1",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "0",
        "2",
        clone_state.clone_id,
        "123",
        "999",
        "False",
        "0",
        "0",
    ]


def test_clone_sync_reupload_upload_issues_save_file_part_requests(
    config_env, monkeypatch, capsys
):
    seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))

    class MultiPartClient(CloneReuploadClient):
        async def download_media(self, message, file=None):
            path = Path(f"{file}.bin")
            path.write_bytes(b"x" * (128 * 1024 + 10))
            self.downloads.append(path)
            return str(path)

    client = MultiPartClient(
        [message(2, message="caption", media=photo)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    parts = [
        req
        for req in client.part_requests
        if isinstance(req, functions.upload.SaveFilePartRequest)
    ]
    assert [req.file_part for req in sorted(parts, key=lambda r: r.file_part)] == [0, 1]
    assert len({req.file_id for req in parts}) == 1
    [send] = [
        req
        for req in client.requests
        if isinstance(req, functions.messages.SendMediaRequest)
    ]
    assert isinstance(send.media.file, types.InputFile)
    assert send.media.file.parts == 2


def test_clone_sync_reupload_striped_download_for_large_media(
    config_env, monkeypatch, capsys
):
    from tgcli.transfer import CHUNK_SIZE

    seed_clone()
    document = types.MessageMediaDocument(
        document=SimpleNamespace(
            mime_type="application/octet-stream",
            attributes=[],
            size=2 * CHUNK_SIZE,
        )
    )
    msg = message(2, message="big", media=document)
    msg.file = SimpleNamespace(size=2 * CHUNK_SIZE)

    class StripedClient(CloneReuploadClient):
        def __init__(self, messages, *, protected=False):
            super().__init__(messages, protected=protected)
            self.iter_download_calls = []

        async def iter_download(
            self, media, *, offset=0, request_size=None, stride=None
        ):
            self.iter_download_calls.append(
                {
                    "offset": offset,
                    "request_size": request_size,
                    "stride": stride,
                }
            )
            yield bytes([65 + offset // CHUNK_SIZE]) * CHUNK_SIZE

        async def download_media(self, message, file=None):
            raise AssertionError("large media must not use sequential download_media")

    client = StripedClient([msg], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    assert {call["offset"] for call in client.iter_download_calls} == {0, CHUNK_SIZE}
    assert {call["stride"] for call in client.iter_download_calls} == {2 * CHUNK_SIZE}
    assert client.part_requests


def test_clone_sync_reupload_striped_download_flood_wait_exits_5(
    config_env, monkeypatch, capsys
):
    from tgcli.transfer import CHUNK_SIZE

    clone_state = seed_clone()
    document = types.MessageMediaDocument(
        document=SimpleNamespace(
            mime_type="application/octet-stream",
            attributes=[],
            size=2 * CHUNK_SIZE,
        )
    )
    msg = message(2, message="big", media=document)
    msg.file = SimpleNamespace(size=2 * CHUNK_SIZE)

    class FloodDownloadClient(CloneReuploadClient):
        async def iter_download(
            self, media, *, offset=0, request_size=None, stride=None
        ):
            raise telethon_errors.FloodWaitError(request=None, capture=45)
            yield  # pragma: no cover

        async def download_media(self, message, file=None):
            raise AssertionError("large media must not use sequential download_media")

    client = FloodDownloadClient([msg], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] == 45
    assert client.part_requests == []
    assert not any(
        isinstance(
            req,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMessageRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        )
        for req in client.requests
    )
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.cooldown_deadline() is not None


def test_clone_sync_reupload_part_flood_wait_exits_5_without_send(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))

    class FloodPartClient(CloneReuploadClient):
        async def download_media(self, message, file=None):
            path = Path(f"{file}.bin")
            path.write_bytes(b"x" * (128 * 1024 + 10))
            self.downloads.append(path)
            return str(path)

        async def __call__(self, request):
            if isinstance(request, functions.upload.SaveFilePartRequest):
                self.part_requests.append(request)
                raise telethon_errors.FloodWaitError(request=request, capture=90)
            return await super().__call__(request)

    client = FloodPartClient([message(2, media=photo)], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] == 90
    assert not any(
        isinstance(
            req,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMessageRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        )
        for req in client.requests
    )
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0
    assert saved.cooldown_deadline() is not None
