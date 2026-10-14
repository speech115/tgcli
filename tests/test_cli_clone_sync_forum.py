"""clone sync: forum topics and topic placement."""

import json
from types import SimpleNamespace

import pytest
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone import (
    CloneForumClient,
    CloneSyncClient,
    channel,
    forum_channel,
    message,
    seed_clone,
    topic_create,
)

# A fixture: the redundant alias marks an explicit re-export for ruff.
from tests.fakes.clone_env import config_env as config_env
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import state, topics
from tgcli.errors import PolicyError


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
