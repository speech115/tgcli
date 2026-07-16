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


SAMPLE = '''
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
'''


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


def poll_media(*, multiple_choice=False):
    answers = [
        types.PollAnswer(
            text=types.TextWithEntities(text="First", entities=[]), option=b"a"
        ),
        types.PollAnswer(
            text=types.TextWithEntities(text="Second", entities=[]), option=b"b"
        ),
    ]
    return types.MessageMediaPoll(
        poll=types.Poll(
            id=77,
            question=types.TextWithEntities(text="Choose", entities=[]),
            answers=answers,
            hash=0,
            multiple_choice=multiple_choice,
        ),
        results=types.PollResults(
            results=[
                types.PollAnswerVoters(option=b"a", voters=4),
                types.PollAnswerVoters(option=b"b", voters=6),
            ],
            total_voters=10,
        ),
    )


def seed_clone(*, kind="broadcast", title="Source channel"):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title=title,
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
            assert ref.channel_id == 999
            return self.destination
        assert ref == "@source"
        return self.source

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_messages(self, entity, limit=None):
        assert entity is self.destination
        return [SimpleNamespace(id=item_id, action=self.destination_actions.get(item_id))
                for item_id in range(self.destination_last_id,
                                     max(self.destination_last_id - limit, 0), -1)]

    async def iter_messages(self, entity, *, min_id=0, reverse=False):
        assert entity is self.source
        self.iter_messages_calls.append((min_id, reverse))
        for item in sorted(self.messages, key=lambda value: value.id):
            if item.id > min_id:
                yield item

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

    async def download_media(self, message, file=None):
        path = Path(f"{file}.bin")
        path.write_bytes(b"payload")
        self.downloads.append(path)
        return str(path)

    async def upload_file(self, path):
        self.uploads.append(path)
        return types.InputFile(id=len(self.uploads), parts=1,
                               name=Path(path).name, md5_checksum="")

    async def __call__(self, request):
        if isinstance(request, (functions.messages.SendMessageRequest,
                                functions.messages.SendMediaRequest)):
            self.requests.append(request)
            self.destination_last_id += 1
            return SimpleNamespace(updates=[types.UpdateMessageID(
                id=self.destination_last_id, random_id=request.random_id
            )])
        if isinstance(request, functions.messages.UploadMediaRequest):
            self.requests.append(request)
            index = len([item for item in self.requests
                         if isinstance(item, functions.messages.UploadMediaRequest)])
            return types.MessageMediaPhoto(photo=types.Photo(
                id=index, access_hash=index * 11, file_reference=b"ref", date=None,
                sizes=[], dc_id=2,
            ))
        if isinstance(request, functions.messages.SendMultiMediaRequest):
            self.requests.append(request)
            updates = []
            for item in request.multi_media:
                self.destination_last_id += 1
                updates.append(types.UpdateMessageID(
                    id=self.destination_last_id, random_id=item.random_id
                ))
            return SimpleNamespace(updates=updates)
        return await super().__call__(request)


def forum_channel(channel_id, title, **overrides):
    return channel(channel_id, title, broadcast=False, megagroup=True,
                   forum=True, **overrides)


def topic_create(message_id, title):
    return message(message_id, message=None,
                   action=types.MessageActionTopicCreate(title=title, icon_color=0))


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
                types.MessageActionTopicCreate(title=request.title, icon_color=0))
            return SimpleNamespace(updates=[types.UpdateMessageID(
                id=self.destination_last_id, random_id=request.random_id)])
        if isinstance(request, functions.messages.GetForumTopicsByIDRequest):
            self.requests.append(request)
            return SimpleNamespace(topics=[
                SimpleNamespace(id=topic_id, title=self.source_topic_titles[topic_id])
                for topic_id in request.topics
                if topic_id in self.source_topic_titles])
        return await super().__call__(request)


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
        assert (action, account, details) == ("clone-sync-topic", "main", {
            "clone_id": clone_state.clone_id, "source_topic_id": 2,
        })
        raise PolicyError("audit write failed")

    monkeypatch.setattr(safety, "append_audit", fail_audit)

    with pytest.raises(PolicyError, match="audit write failed"):
        await topics.create_topic(
            mutate, forum_channel(999, "Forum chat", creator=True), clone_state, 2,
            account_alias="main", title="News", icon_color=0)

    assert mutate_calls == []
    assert clone_state.topic_map == {}
    assert state.load(clone_state.clone_id).topic_map == {}


def test_clone_sync_copies_plain_text_oldest_first_and_reruns_idempotently(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(3), message(2)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["sync"] == {
        "copied": 2,
        "forwarded": 2,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "skipped_service": 0,
        "skipped_unsupported": [],
        "topics_created": 0,
        "cursor": 3,
        "more": False,
    }
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
    assert audit_calls == [("clone-sync-topic", "main", {
        "clone_id": clone_state.clone_id, "source_topic_id": 2,
    })]
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
    client = CloneForumClient([
        message(3, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, forum_topic=True)),
    ])
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
    client = CloneForumClient([
        message(4, message="pong", from_id=types.PeerUser(77), sender_id=77,
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=3, reply_to_top_id=2, forum_topic=True)),
    ])
    client.destination_last_id = 1003

    async def get_entity(ref):
        if isinstance(ref, types.PeerUser):
            return types.User(id=77, first_name="Alex")
        return await CloneSyncClient.get_entity(client, ref)

    client.get_entity = get_entity
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    [request] = [item for item in client.requests
                 if isinstance(item, functions.messages.SendMessageRequest)]
    assert request.message == "Alex: pong"
    assert request.reply_to.reply_to_msg_id == 1003
    assert request.reply_to.top_msg_id == 1002
    assert sync["reuploaded"] == 1


def test_clone_sync_recovers_unmapped_topic_from_source_lookup(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([
        message(5, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, forum_topic=True)),
    ])
    client.source_topic_titles = {2: "Old news"}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    lookups = [item for item in client.requests
               if isinstance(item, functions.messages.GetForumTopicsByIDRequest)]
    created = [item for item in client.requests
               if isinstance(item, functions.messages.CreateForumTopicRequest)]
    assert len(lookups) == 1 and len(created) == 1
    assert created[0].title == "Old news"
    assert sync["topics_created"] == 1
    forward = [item for item in client.requests
               if isinstance(item, functions.messages.ForwardMessagesRequest)]
    assert forward[0].top_msg_id == 2


def test_clone_sync_topic_recovery_audit_failure_blocks_copy_and_state(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([
        message(5, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, forum_topic=True)),
    ])
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
            if (isinstance(request, functions.messages.ForwardMessagesRequest)
                    and not self.failed):
                self.failed = True
                self.requests.append(request)
                raise PolicyError("copy failed")
            return await super().__call__(request)

    client = FailOnceForumClient([
        message(5, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, forum_topic=True)),
    ])
    client.source_topic_titles = {2: "Old news"}
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2
    assert "copy failed" in capsys.readouterr().err
    failed = state.load(clone_state.clone_id)
    assert failed.topic_dest_for(2) == 2
    assert failed.cursor == 0 and failed.id_map == {}

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["topics_created"] == 0
    assert len([item for item in client.requests
                if isinstance(item, functions.messages.GetForumTopicsByIDRequest)]) == 1
    assert len([item for item in client.requests
                if isinstance(item, functions.messages.CreateForumTopicRequest)]) == 1
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 5 and saved.dest_for(5) == 3


def test_clone_sync_validates_forum_header_before_topic_recovery(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([
        message(5, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, forum_topic=True,
            reply_to_peer_id=types.PeerChannel(321))),
    ])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "cross-peer clone replies are not supported" in capsys.readouterr().err
    assert client.requests == []
    assert not safety.audit_path().exists()
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.topic_map == {} and saved.id_map == {}


@pytest.mark.parametrize("topic_id", [True, "2", -2, 2_147_483_648])
def test_clone_sync_rejects_malformed_forum_placement_before_recovery(
    topic_id, config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([
        message(5, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=topic_id, forum_topic=True)),
    ])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "reply parent is invalid" in capsys.readouterr().err
    assert client.requests == []
    assert not safety.audit_path().exists()
    saved = state.load(clone_state.clone_id)
    assert saved.cursor == 0 and saved.topic_map == {} and saved.id_map == {}


@pytest.mark.parametrize("topic_id", [True, "2", -2, 2_147_483_648])
def test_topic_id_of_rejects_malformed_forum_placement(topic_id):
    header = types.MessageReplyHeader(
        reply_to_msg_id=topic_id, forum_topic=True)

    with pytest.raises(PolicyError, match="topic id is invalid"):
        topics.topic_id_of(message(5, reply_to=header))


@pytest.mark.parametrize("topic_id", [True, "2", -2, 2_147_483_648])
def test_topic_id_of_rejects_malformed_forum_reply_topic(topic_id):
    header = types.MessageReplyHeader(
        reply_to_msg_id=3, reply_to_top_id=topic_id, forum_topic=True)

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
    client = CloneForumClient([
        message(5, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=3, forum_topic=True, **overrides)),
    ])
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
    client = CloneForumClient([
        message(5, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2_147_483_648, reply_to_top_id=2,
            forum_topic=True)),
    ])
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
        reply_to_msg_id=2_147_483_647, reply_to_top_id=top_id,
        forum_topic=True)

    assert topics.topic_id_of(message(5, reply_to=header)) == 2_147_483_647


def test_clone_sync_rejects_mixed_topic_placement_album_before_recovery(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone(kind="forum", title="Forum chat")
    client = CloneForumClient([
        message(5, grouped_id=44, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2, forum_topic=True)),
        message(6, grouped_id=44, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=3, forum_topic=True)),
    ])
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
    client = CloneForumClient([
        message(3, grouped_id=44, reply_to=header),
        message(4, grouped_id=44),
    ])
    client.destination_last_id = 1002
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["forwarded"] == 2
    [request] = client.requests
    assert request.id == [3, 4] and request.top_msg_id == 1002


def test_clone_sync_rejects_forum_reply_header_for_nonforum_clone(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneSyncClient([
        message(2, reply_to=types.MessageReplyHeader(
            reply_to_msg_id=1, forum_topic=True)),
    ])
    client.source = channel(123, "Team chat", broadcast=False, megagroup=True,
                            forum=False)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 2

    assert "reply shape is not supported" in capsys.readouterr().err


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
    client = CloneReuploadClient([
        message(2, message="😀bold", entities=[original],
                from_id=types.PeerUser(77), sender_id=77,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=1))
    ])
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
    assert request.message == "Zoë 🚀: 😀bold"
    assert request.reply_to.reply_to_msg_id == 1001
    prefix_units = len("Zoë 🚀: ".encode("utf-16-le")) // 2
    assert request.entities[0].offset == prefix_units + 2
    assert original.offset == 2
    assert sync["reuploaded"] == 1
    assert sync["reply_flattened"] == 0


def test_clone_sync_reuploads_protected_megagroup_with_cached_author_prefix(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneReuploadClient([
        message(2, message="first", from_id=types.PeerUser(77), sender_id=77),
        message(3, message="second", from_id=types.PeerUser(77), sender_id=77),
    ], protected=True)
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False,
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
    sends = [item for item in client.requests
             if isinstance(item, functions.messages.SendMessageRequest)]
    assert [item.message for item in sends] == ["Alex: first", "Alex: second"]
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
    client = CloneReuploadClient([
        message(2, message="answer", from_id=types.PeerUser(123),
                sender_id=123, reply_to=reply)
    ])
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
    assert request.message == "Alex Smith: answer"
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
    client = CloneReuploadClient([
        message(2, message="answer", reply_to=reply)
    ])
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
    client = CloneReuploadClient([
        message(2, message="pong", from_id=types.PeerUser(77), sender_id=77,
                reply_to=types.MessageReplyHeader(reply_to_msg_id=1)),
    ])
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
    [request] = [item for item in client.requests
                 if isinstance(item, functions.messages.SendMessageRequest)]
    assert request.message == "Alex: pong"
    assert request.reply_to.reply_to_msg_id == 1001
    assert sync["reuploaded"] == 1


def test_clone_sync_blocks_basic_group_reply_with_different_source_peer(
    config_env, monkeypatch, capsys
):
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

    assert "cross-peer clone replies" in capsys.readouterr().err
    assert client.requests == []
    assert state.load(clone_state.clone_id).dest_for(2) is None


def test_clone_sync_reports_attributed_unmapped_reply_forward_fallback(
    config_env, monkeypatch, capsys
):
    seed_clone(kind="megagroup", title="Team chat")
    client = CloneSyncClient([
        message(2, reply_to=types.MessageReplyHeader(reply_to_msg_id=1))
    ])
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
        peer=types.PeerUser(42), story_id=261,
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
        999, "Forum chat", creator=True, broadcast=False, megagroup=True,
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
    assert sync == {
        "copied": 1,
        "forwarded": 1,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "skipped_service": 1,
        "skipped_unsupported": [
            {"id": 2, "kind": "MessageMediaPoll"},
            {"id": 3, "kind": "MessageMediaDice"},
        ],
        "topics_created": 0,
        "cursor": 4,
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
def test_clone_sync_skips_view_once_media(
    media, config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    client = CloneSyncClient([message(2, media=media)])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    sync = json.loads(capsys.readouterr().out)["sync"]
    assert sync["copied"] == 0
    assert sync["skipped_unsupported"] == [{
        "id": 2, "kind": f"{type(media).__name__}TTL",
    }]
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
    client = CloneReuploadClient([
        message(2, media=poll_media(multiple_choice=multiple_choice))
    ])
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


def test_clone_sync_maps_reply_to_story_placeholder(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    story = types.MessageMediaStory(peer=types.PeerChannel(77), id=346)

    class StoryReplyClient(CloneReuploadClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerChannel) and ref.channel_id == 77:
                return channel(77, "Story source", username="storysource")
            return await super().get_entity(ref)

    client = StoryReplyClient([
        message(2, media=story),
        message(3, message="Reply", reply_to=types.MessageReplyHeader(
            reply_to_msg_id=2
        )),
    ])
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
    assert result["sync"] == {
        "copied": 4,
        "forwarded": 4,
        "reuploaded": 0,
        "snapshots": 0,
        "reply_flattened": 0,
        "skipped_service": 0,
        "skipped_unsupported": [],
        "topics_created": 0,
        "cursor": 5,
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
        reply_to_msg_id=1, quote_text="quote", quote_entities=quote_entities,
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
    client = CloneReuploadClient([
        message(2, reply_to=types.MessageReplyHeader(reply_to_msg_id=1))
    ])
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
    assert all(not path.exists() and not path.parent.exists() for path in client.downloads)
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
                    id=self.destination_last_id, out=True, pts=1, pts_count=1,
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
    document = types.MessageMediaDocument(document=SimpleNamespace(
        mime_type="text/plain", attributes=attributes,
    ))
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
        [message(2, message="first", media=photo, grouped_id=5),
         message(3, message="second", media=photo, grouped_id=5)],
        protected=True,
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    uploads = [item for item in client.requests
               if isinstance(item, functions.messages.UploadMediaRequest)]
    [multi] = [item for item in client.requests
               if isinstance(item, functions.messages.SendMultiMediaRequest)]
    assert len(uploads) == 2
    assert [item.message for item in multi.multi_media] == ["first", "second"]
    assert len({item.random_id for item in multi.multi_media}) == 2
    saved = state.load(clone_state.clone_id)
    assert [saved.dest_for(2), saved.dest_for(3)] == [2, 3]
    assert all(not path.exists() and not path.parent.exists() for path in client.downloads)


def test_clone_sync_reuploads_open_reply_album_to_mapped_parent(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(1, 1001)
    clone_state.cursor = 1
    state.save(clone_state)
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    reply = types.MessageReplyHeader(reply_to_msg_id=1, quote_text="album")
    client = CloneReuploadClient([
        message(2, media=photo, grouped_id=44, reply_to=reply),
        message(3, media=photo, grouped_id=44),
    ])
    client.destination_last_id = 1001
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["sync"]["copied"] == 2
    [request] = [item for item in client.requests
                 if isinstance(item, functions.messages.SendMultiMediaRequest)]
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
    client = CloneReuploadClient([
        message(2, message="caption", media=photo, grouped_id=44,
                reply_to=reply, from_id=types.PeerUser(77), sender_id=77),
        message(3, message="", media=photo, grouped_id=44,
                from_id=types.PeerUser(77), sender_id=77),
    ])
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
    [request] = [item for item in client.requests
                 if isinstance(item, functions.messages.SendMultiMediaRequest)]
    assert [item.message for item in request.multi_media] == [
        "Alex: caption", "",
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


def test_clone_sync_readonly_blocks_before_config_or_session(monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
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
        "1", "1", "0", "0", "0", "0", "0", "0", "2",
        clone_state.clone_id, "123", "999", "False",
    ]
