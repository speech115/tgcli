"""CLI tests for `tg clone refresh` (ADR-0054)."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli import safety, session
from tgcli.cli import main
from tgcli.clone import state

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
        "creator": True,
        "username": None,
        "usernames": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _fwd(from_name="Имя"):
    return types.MessageFwdHeader(date=None, from_name=from_name, imported=False)


def message(message_id, text="тело", **overrides):
    values = {
        "id": message_id,
        "message": text,
        "entities": None,
        "fwd_from": None,
        "media": None,
        "grouped_id": None,
        "action": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def seed_clone(source_kind="broadcast"):
    clone_state = state.CloneState.new(
        account_user_id=42,
        source_peer_id=123,
        source_title="Source channel",
        source_kind=source_kind,
    )
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    return clone_state


class RefreshClient:
    # Telethon restores the logged-in account id from the session on connect,
    # so a connected client knows it without a get_me RPC.
    _self_id = 42

    def __init__(self, source_msgs, dest_msgs):
        self.source = channel(123, "Source channel")
        self.destination = channel(999, "Source channel", creator=True)
        self.source_msgs = {m.id: m for m in source_msgs}
        self.dest_msgs = {m.id: m for m in dest_msgs}
        self.requests = []
        self.input_peer = types.InputPeerChannel(channel_id=999, access_hash=0)
        self.edit_error = None
        self.edit_errors_by_id: dict[int, Exception] = {}
        self.flood_on_edit_after = None
        self._edits = 0

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_entity(self, ref):
        if isinstance(ref, types.PeerChannel):
            if ref.channel_id == 999:
                return self.destination
            raise ValueError("peer not found")
        assert ref == "@source"
        return self.source

    async def get_input_entity(self, ref):
        if ref is self.destination or (
            isinstance(ref, types.PeerChannel) and ref.channel_id == 999
        ):
            return self.input_peer
        if hasattr(ref, "id") and ref.id == 999:
            return self.input_peer
        raise ValueError("no input peer")

    async def get_messages(self, entity, ids=None, limit=None):
        if getattr(self, "flood_on_get_messages", False):
            raise telethon_errors.FloodWaitError(request=None, capture=90)
        store = self.source_msgs if entity is self.source else self.dest_msgs
        return [store.get(item) for item in ids]

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.messages.EditMessageRequest):
            if (
                self.flood_on_edit_after is not None
                and self._edits >= self.flood_on_edit_after
            ):
                raise telethon_errors.FloodWaitError(request=request, capture=90)
            err = self.edit_errors_by_id.get(request.id, self.edit_error)
            if err is not None:
                raise err
            self._edits += 1
            # Reflect the edit so a later fresh preview sees the new body.
            dest = self.dest_msgs.get(request.id)
            if dest is not None:
                dest.message = request.message
                dest.entities = request.entities
            return SimpleNamespace(updates=[])
        raise AssertionError(f"unexpected request: {type(request).__name__}")


def _eligible_pair():
    """One posts-leg forward whose dest body is still the raw source text."""
    src = message(54, "тело", fwd_from=_fwd())
    dst = message(154, "тело")
    return src, dst


def test_clone_refresh_requires_initialized_clone(config_env, monkeypatch, capsys):
    client = RefreshClient([], [])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "refresh", "@source", "--json"]) == 2
    assert "clone is not initialized" in capsys.readouterr().err


def test_clone_refresh_preview_exits_5_when_cooldown_active(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.set_cooldown(datetime.now(UTC) + timedelta(minutes=10))
    state.save(clone_state)
    src, dst = _eligible_pair()
    client = RefreshClient([src], [dst])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "refresh", "@source", "--json"]) == 5
    assert client.requests == []
    assert "rate limited" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error",
    [
        telethon_errors.ChannelPrivateError,
        telethon_errors.ChannelInvalidError,
        telethon_errors.ChatForbiddenError,
    ],
)
def test_clone_refresh_unreachable_destination_exits_2(
    config_env, monkeypatch, capsys, error
):
    """A destination the account can no longer open — deleted, left, banned —
    is the documented policy failure, not a raw Telethon traceback."""
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src, dst = _eligible_pair()

    class GoneDestinationClient(RefreshClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerChannel):
                raise error(request=None)
            return await super().get_entity(ref)

    client = GoneDestinationClient([src], [dst])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "refresh", "@source", "--json"]) == 2
    assert "clone destination is unavailable" in capsys.readouterr().err
    assert client.requests == []


def test_clone_refresh_preview_floodwait_arms_cooldown_exit_5(
    config_env, monkeypatch, capsys
):
    """A FloodWait on the preview scan exits 5 locally (ADR-0072: the seam
    arms the per-type cooldown and the run refuses)."""
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src, dst = _eligible_pair()
    client = RefreshClient([src], [dst])
    client.flood_on_get_messages = True
    make_session_fake(monkeypatch, client)

    assert main(["clone", "refresh", "@source", "--json"]) == 5
    assert "rate limited" in capsys.readouterr().err


def test_clone_refresh_preview_unaffected_by_readonly(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src, dst = _eligible_pair()
    client = RefreshClient([src], [dst])
    make_session_fake(monkeypatch, client)

    assert main(["--readonly", "clone", "refresh", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["preview_id"].startswith("p_")
    assert result["refresh"]["eligible"] == [{"source_id": 54, "destination_id": 154}]


def test_clone_refresh_preview_json_and_persisted_payload(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    poll = types.MessageMediaPoll(
        poll=types.Poll(
            id=1,
            question=types.TextWithEntities(text="Q", entities=[]),
            answers=[
                types.PollAnswer(
                    text=types.TextWithEntities(text="A", entities=[]), option=b"a"
                )
            ],
            hash=0,
        ),
        results=types.PollResults(results=[], total_voters=0),
    )
    clone_state.record_mapping(60, 160)
    clone_state.record_mapping(70, 170)
    # Discussion-leg mapping must never appear in either preview list.
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 456
    clone_state.discussion_destination_peer_id = 888
    clone_state.discussion_linked = True
    clone_state.record_discussion_mapping(50, 500)
    state.save(clone_state)
    src = message(54, "тело", fwd_from=_fwd())
    poll_src = message(60, "poll", fwd_from=_fwd(), media=poll)
    native_src = message(70, "тело", fwd_from=_fwd())
    disc_src = message(50, "тело", fwd_from=_fwd())
    dest = message(154, "тело")
    poll_dest = message(160, "poll")
    native_dest = message(170, "тело", fwd_from=_fwd("Other"))
    disc_dest = message(500, "тело")
    client = RefreshClient(
        [src, poll_src, native_src, disc_src],
        [dest, poll_dest, native_dest, disc_dest],
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "refresh", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["preview_id"].startswith("p_")
    assert "expires_at" in result
    assert result["clone"]["id"] == clone_state.clone_id
    assert result["clone"]["source"]["id"] == 123
    assert result["refresh"]["eligible"] == [{"source_id": 54, "destination_id": 154}]
    assert result["refresh"]["excluded"] == [
        {"source_id": 60, "reason": "poll-snapshot"},
        {"source_id": 70, "reason": "native-reforward"},
    ]
    assert all(
        item["source_id"] != 50
        for item in result["refresh"]["eligible"] + result["refresh"]["excluded"]
    )

    stored = safety.consume_preview(result["preview_id"])
    assert stored["kind"] == "clone-refresh"
    assert stored["source"] == "@source"
    assert stored["account_user_id"] == 42
    assert stored["source_peer_id"] == 123
    assert stored["eligible"] == [{"source_id": 54, "destination_id": 154}]
    assert "text" not in stored
    assert "excluded" not in stored


def test_clone_refresh_commit_readonly_blocks_before_preview_use(monkeypatch):
    from tgcli import cli

    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account, **kw: pytest.fail("session opened")
    )

    assert (
        main(
            [
                "--readonly",
                "clone",
                "refresh",
                "@source",
                "--commit",
                preview["preview_id"],
            ]
        )
        == 2
    )
    assert safety.consume_preview(preview["preview_id"])["kind"] == "clone-refresh"


@pytest.mark.parametrize("env_name", ["TGCLI_READONLY", "TGCLI_NO_SEND"])
def test_clone_refresh_commit_blocked_by_env(monkeypatch, env_name):
    from tgcli import cli

    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )
    monkeypatch.setenv(env_name, "1")
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))

    assert main(["clone", "refresh", "@source", "--commit", preview["preview_id"]]) == 2
    assert safety.consume_preview(preview["preview_id"])["kind"] == "clone-refresh"


def test_clone_refresh_commit_rejects_wrong_kind(config_env, monkeypatch, capsys):
    preview = safety.create_preview(
        {
            "kind": "clone-init",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
        }
    )
    make_session_fake(monkeypatch, RefreshClient([], []))
    assert main(["clone", "refresh", "@source", "--commit", preview["preview_id"]]) == 2
    assert "clone refresh preview" in capsys.readouterr().err


def test_clone_refresh_commit_rejects_source_mismatch(config_env, monkeypatch, capsys):
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@other",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )
    make_session_fake(monkeypatch, RefreshClient([], []))
    assert main(["clone", "refresh", "@source", "--commit", preview["preview_id"]]) == 2
    assert "clone refresh preview" in capsys.readouterr().err


def test_clone_refresh_commit_rejects_wrong_account(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src, dst = _eligible_pair()
    client = RefreshClient([src], [dst])
    make_session_fake(monkeypatch, client)
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 99,
            "source_peer_id": 123,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )

    assert main(["clone", "refresh", "@source", "--commit", preview["preview_id"]]) == 2
    err = capsys.readouterr().err
    assert "clone refresh preview" in err
    assert "account" in err
    assert client.requests == []


def test_clone_refresh_commit_rejects_wrong_source_peer(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src, dst = _eligible_pair()
    client = RefreshClient([src], [dst])
    make_session_fake(monkeypatch, client)
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 999,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )

    assert main(["clone", "refresh", "@source", "--commit", preview["preview_id"]]) == 2
    err = capsys.readouterr().err
    assert "clone refresh preview" in err
    assert "source" in err or "account" in err
    assert client.requests == []


def test_clone_refresh_commit_rejects_stale_id_map_pair(
    config_env, monkeypatch, capsys
):
    """After replace/remap, a preview pair no longer matching id_map fails closed."""
    clone_state = seed_clone()
    clone_state.record_mapping(54, 254)  # remapped; preview still names 154
    state.save(clone_state)
    src = message(54, "тело", fwd_from=_fwd())
    dst = message(254, "тело")
    client = RefreshClient([src], [dst])
    make_session_fake(monkeypatch, client)
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )

    assert main(["clone", "refresh", "@source", "--commit", preview["preview_id"]]) == 2
    err = capsys.readouterr().err
    assert "clone refresh preview" in err
    assert "id_map" in err
    assert client.requests == []


def test_clone_refresh_commit_skips_stale_candidate(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src = message(54, "тело", fwd_from=_fwd())
    # Destination already carries a prefix — became stale between preview and commit.
    dst = message(154, "Переслано от Имя\n\nтело")
    client = RefreshClient([src], [dst])
    make_session_fake(monkeypatch, client)
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )

    assert (
        main(
            ["clone", "refresh", "@source", "--commit", preview["preview_id"], "--json"]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["refresh"]["edited"] == []
    assert result["refresh"]["skipped"] == [{"source_id": 54, "reason": "not-eligible"}]
    assert result["refresh"]["count"] == 0
    assert client.requests == []


def test_clone_refresh_commit_audits_before_edit_and_boundary_shape(
    config_env, monkeypatch, capsys, tmp_path
):
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src = message(54, "тело", fwd_from=_fwd())
    dst = message(154, "тело", media=types.MessageMediaPhoto(photo=None))
    order: list[str] = []

    class OrderedClient(RefreshClient):
        async def __call__(self, request):
            order.append(f"rpc:{type(request).__name__}")
            return await super().__call__(request)

    client = OrderedClient([src], [dst])
    make_session_fake(monkeypatch, client)
    original_audit = safety.append_audit

    def tracking_audit(action, account, details):
        order.append(f"audit:{action}")
        original_audit(action, account, details)

    monkeypatch.setattr(safety, "append_audit", tracking_audit)
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )

    assert (
        main(
            ["clone", "refresh", "@source", "--commit", preview["preview_id"], "--json"]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["refresh"]["edited"] == [{"source_id": 54, "destination_id": 154}]
    assert result["refresh"]["count"] == 1
    assert order == ["audit:clone-refresh-prefix", "rpc:EditMessageRequest"]

    assert len(client.requests) == 1
    req = client.requests[0]
    assert isinstance(req, functions.messages.EditMessageRequest)
    assert req.peer == client.input_peer
    assert req.id == 154
    assert req.message == "Переслано от Имя\n\nтело"
    assert req.entities is None or req.entities == []
    assert req.media is None
    assert not any(
        isinstance(
            r,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMessageRequest,
            ),
        )
        for r in client.requests
    )

    audit_lines = (tmp_path / "state" / "audit.jsonl").read_text().strip().splitlines()
    record = json.loads(audit_lines[-1])
    assert record["action"] == "clone-refresh-prefix"
    assert record["clone_id"] == clone_state.clone_id
    assert record["source_message_id"] == 54
    assert record["destination_message_id"] == 154


def test_clone_refresh_commit_swallows_message_not_modified(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    clone_state.record_mapping(69, 169)
    state.save(clone_state)
    src_a = message(54, "тело", fwd_from=_fwd())
    src_b = message(69, "другое", fwd_from=_fwd("Канал"))
    dst_a = message(154, "тело")
    dst_b = message(169, "другое")
    client = RefreshClient([src_a, src_b], [dst_a, dst_b])
    client.edit_errors_by_id[154] = telethon_errors.MessageNotModifiedError(
        request=None
    )
    make_session_fake(monkeypatch, client)
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [
                {"source_id": 54, "destination_id": 154},
                {"source_id": 69, "destination_id": 169},
            ],
        }
    )

    assert (
        main(
            ["clone", "refresh", "@source", "--commit", preview["preview_id"], "--json"]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["refresh"]["edited"] == [
        {"source_id": 54, "destination_id": 154},
        {"source_id": 69, "destination_id": 169},
    ]
    assert result["refresh"]["count"] == 2
    assert len(client.requests) == 2


def test_clone_refresh_commit_flood_arms_cooldown_exit_5(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    clone_state.record_mapping(69, 169)
    state.save(clone_state)
    src_a = message(54, "тело", fwd_from=_fwd())
    src_b = message(69, "другое", fwd_from=_fwd("Канал"))
    dst_a = message(154, "тело")
    dst_b = message(169, "другое")
    client = RefreshClient([src_a, src_b], [dst_a, dst_b])
    client.flood_on_edit_after = 1  # first edit ok, second floods
    make_session_fake(monkeypatch, client)
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [
                {"source_id": 54, "destination_id": 154},
                {"source_id": 69, "destination_id": 169},
            ],
        }
    )

    assert (
        main(
            ["clone", "refresh", "@source", "--commit", preview["preview_id"], "--json"]
        )
        == 5
    )
    assert "rate limited" in capsys.readouterr().err
    # First edit stayed applied — no rollback.
    assert client.dest_msgs[154].message == "Переслано от Имя\n\nтело"
    assert client.dest_msgs[169].message == "другое"


def test_clone_refresh_commit_megagroup_keeps_sync_author_attribution(
    config_env, monkeypatch, capsys
):
    """A non-broadcast clone renders author_of exactly like sync; the ADR-0050
    forward lead is broadcast-only and must never reach a megagroup post."""
    clone_state = seed_clone(source_kind="megagroup")
    clone_state.record_mapping(54, 154)
    state.save(clone_state)
    src = message(54, "тело", fwd_from=_fwd(), post_author="Админ")
    dst = message(154, "тело")
    client = RefreshClient([src], [dst])
    client.source = channel(123, "Source group", broadcast=False, megagroup=True)
    make_session_fake(monkeypatch, client)
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "eligible": [{"source_id": 54, "destination_id": 154}],
        }
    )

    assert (
        main(
            ["clone", "refresh", "@source", "--commit", preview["preview_id"], "--json"]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["refresh"]["edited"] == [{"source_id": 54, "destination_id": 154}]
    assert len(client.requests) == 1
    req = client.requests[0]
    assert isinstance(req, functions.messages.EditMessageRequest)
    assert "Переслано от" not in req.message
    assert req.message == "Админ: \n\nтело"


def test_clone_refresh_preview_album_deleted_lead_fails_closed(
    config_env, monkeypatch, capsys
):
    """A deleted album lead must not promote the survivor onto the wrong live
    post: the follower is excluded, not prefixed."""
    clone_state = seed_clone()
    clone_state.record_mapping(20, 200)
    clone_state.record_mapping(21, 201)
    state.save(clone_state)
    follower = message(21, "тело", fwd_from=_fwd(), grouped_id=77)
    dst = message(201, "тело")
    client = RefreshClient([follower], [dst])  # lead 20 deleted at the source
    make_session_fake(monkeypatch, client)

    assert main(["clone", "refresh", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["refresh"]["eligible"] == []
    assert result["refresh"]["excluded"] == [
        {"source_id": 21, "reason": "album-lead-unknown"}
    ]


def test_clone_refresh_after_flood_fresh_preview_lists_remaining(
    config_env, monkeypatch, capsys
):
    """Edits already applied stay; a fresh preview only lists still-missing."""
    clone_state = seed_clone()
    clone_state.record_mapping(54, 154)
    clone_state.record_mapping(69, 169)
    state.save(clone_state)
    src_a = message(54, "тело", fwd_from=_fwd())
    src_b = message(69, "другое", fwd_from=_fwd("Канал"))
    # 54 already fixed (as if a prior partial commit succeeded); 69 still raw.
    dst_a = message(154, "Переслано от Имя\n\nтело")
    dst_b = message(169, "другое")
    client = RefreshClient([src_a, src_b], [dst_a, dst_b])
    make_session_fake(monkeypatch, client)

    assert main(["clone", "refresh", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["refresh"]["eligible"] == [{"source_id": 69, "destination_id": 169}]


def test_clone_refresh_resolves_a_bare_title_through_clone_state(
    config_env, monkeypatch, capsys
):
    """ADR-0082 documents the title reference for refresh as well as sync."""
    seed_clone()

    class TitleClient(RefreshClient):
        async def get_entity(self, ref):
            if isinstance(ref, types.PeerChannel) and ref.channel_id == 123:
                return self.source
            if isinstance(ref, str):
                raise AssertionError(f"title must not reach Telegram: {ref!r}")
            return await super().get_entity(ref)

    make_session_fake(monkeypatch, TitleClient([], []))

    assert main(["clone", "refresh", "Source channel", "--json"]) == 0
    capsys.readouterr()


def test_clone_refresh_unreachable_source_is_not_found(config_env, monkeypatch, capsys):
    seed_clone()

    class PrivateClient(RefreshClient):
        async def get_entity(self, ref):
            raise telethon_errors.ChannelPrivateError(request=None)

    make_session_fake(monkeypatch, PrivateClient([], []))

    assert main(["clone", "refresh", "@source", "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "NOT_FOUND"
