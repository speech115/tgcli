import contextlib
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from hypothesis import settings, strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes.clone_init import CloneInitClient, channel
from tgcli import safety
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


def user(user_id=123, *, first_name="Alex", last_name="Smith", bot=False):
    return types.User(
        id=user_id,
        first_name=first_name,
        last_name=last_name,
        bot=bot,
    )


def legacy_group(**overrides):
    values = {
        "id": 123,
        "title": "Legacy group",
        "photo": types.ChatPhotoEmpty(),
        "participants_count": 2,
        "date": None,
        "version": 1,
    }
    values.update(overrides)
    return types.Chat(**values)


def stored_preview():
    return safety.create_preview(
        {
            "kind": "clone-init",
            "source": "@source",
            "account_user_id": 42,
            "source_peer_id": 123,
            "source_title": "Source channel",
            "protected": True,
            "approximate_message_count": 321,
        }
    )


def test_clone_init_preview_reports_plan_without_mutation(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["source"] == {
        "id": 123,
        "title": "Source channel",
        "kind": "broadcast",
    }
    assert result["clone"]["status"] == "planned"
    assert result["clone"]["commit_required"] is True
    assert result["approximate_message_count"] == 321
    assert result["protected"] is True
    assert result["preview_id"].startswith("p_")
    assert result["peers_to_create"] == 1
    assert [type(request) for request in client.requests] == [
        functions.channels.GetFullChannelRequest
    ]
    assert client.session_mutation_safe is False


def test_clone_init_preview_peers_to_create_two_with_linked_discussion(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["peers_to_create"] == 2


def test_clone_init_preview_peers_to_create_one_with_no_comments(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--no-comments", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["peers_to_create"] == 1


def test_clone_init_preview_peers_to_create_zero_when_destination_recorded(
    config_env, monkeypatch, capsys
):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    client = CloneInitClient()
    client.linked = linked_group()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["peers_to_create"] == 0


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (
            legacy_group(migrated_to=types.InputChannel(channel_id=555, access_hash=0)),
            "migrated to a supergroup; clone channel 555 instead",
        ),
        (
            legacy_group(migrated_to=types.InputChannelEmpty()),
            "migrated to a supergroup; clone the supergroup instead",
        ),
        (legacy_group(deactivated=True), "deactivated"),
    ],
)
def test_clone_init_preview_rejects_unsupported_source_kinds(
    source, error, config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source = source
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 2

    assert error in capsys.readouterr().err


def test_clone_init_commit_creates_and_records_destination(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    client.requests.clear()

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["status"] == "ready"
    assert result["clone"]["commit_required"] is False
    assert result["clone"]["destination"] == {
        "id": 999,
        "title": "[Clone] Source channel",
    }
    saved = state.load(result["clone"]["id"])
    assert saved.destination_peer_id == 999
    assert saved.source_title == "Source channel"
    assert client.session_mutation_safe is True
    assert any(
        isinstance(item, functions.channels.CreateChannelRequest)
        for item in client.requests
    )
    assert any(
        isinstance(item, functions.channels.EditTitleRequest)
        for item in client.requests
    )
    assert any(
        isinstance(item, functions.account.UpdateNotifySettingsRequest)
        for item in client.requests
    )
    [title_edit] = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.EditTitleRequest)
    ]
    assert title_edit.title == "[Clone] Source channel"
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert [record["action"] for record in audits] == [
        "clone-init-create",
        "clone-init-title",
        "clone-init-ergonomics",
    ]


def test_clone_init_commit_creates_forum_destination(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = channel(
        123, "Forum chat", broadcast=False, megagroup=True, forum=True
    )
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    [created] = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.CreateChannelRequest)
    ]
    assert created.megagroup is True and created.broadcast is False
    assert any(
        isinstance(item, functions.channels.ToggleForumRequest)
        for item in client.requests
    )
    assert client.destination.forum is True
    saved = state.load(result["clone"]["id"])
    assert saved.source_kind == "forum"
    assert saved.destination_kind == "forum"


@pytest.mark.parametrize(
    ("stored_kind", "live_forum"),
    [("megagroup", True), ("forum", False)],
)
def test_clone_init_commit_blocks_source_kind_drift_without_state_or_mutation(
    stored_kind, live_forum, config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=live_forum
    )
    clone_state = state.CloneState.new(
        account_user_id=42,
        source_peer_id=123,
        source_title="Team chat",
        source_kind=stored_kind,
    )
    state.save(clone_state)
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]
    state_path = state.path_for(clone_state.clone_id)
    before = state_path.read_bytes()

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 2

    assert "source kind no longer matches" in capsys.readouterr().err
    assert state_path.read_bytes() == before
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_clone_init_commit_copies_nonempty_channel_description(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source_about = "Source description"
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    capsys.readouterr()
    [request] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.EditChatAboutRequest)
    ]
    assert request.peer is client.destination
    assert request.about == "Source description"
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert [record["action"] for record in audits][-2:] == [
        "clone-init-about",
        "clone-init-ergonomics",
    ]


def test_clone_init_commit_tolerates_an_unchanged_description(
    config_env, monkeypatch, capsys
):
    """Re-running init is the documented recovery path, and by then the
    description already matches — Telegram answers ChatAboutNotModified
    (live-proven). That is a no-op, not a failure."""

    class UnchangedAboutClient(CloneInitClient):
        async def __call__(self, request):
            if isinstance(request, functions.messages.EditChatAboutRequest):
                raise telethon_errors.ChatAboutNotModifiedError(request)
            return await super().__call__(request)

    client = UnchangedAboutClient()
    client.source_about = "Source description"
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )


def test_clone_init_commit_copies_private_dialog_profile(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source = user()
    client.source.photo = object()
    client.source_about = "Dialog bio"
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["source"] == {
        "id": 123,
        "title": "Alex Smith",
        "kind": "dialog",
    }
    assert client.destination.title == "[Clone] Alex Smith"
    assert client.destination.about == "Dialog bio"
    assert any(
        isinstance(item, functions.users.GetFullUserRequest) for item in client.requests
    )
    assert any(
        isinstance(item, functions.channels.EditPhotoRequest)
        for item in client.requests
    )
    assert state.load(result["clone"]["id"]).source_kind == "dialog"


def test_clone_init_commit_copies_basic_group_profile(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = legacy_group()
    client.source_about = "Group description"
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert client.destination.title == "[Clone] Legacy group"
    assert client.destination.about == "Group description"
    assert any(
        isinstance(item, functions.messages.GetFullChatRequest)
        for item in client.requests
    )
    assert state.load(result["clone"]["id"]).source_kind == "basic"


def test_clone_init_avatar_download_failure_keeps_destination_retryable(
    config_env, monkeypatch, capsys
):
    class MissingAvatarClient(CloneInitClient):
        async def download_profile_photo(self, entity, file=None):
            return None

    client = MissingAvatarClient()
    client.source.photo = object()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 2
    )

    assert "avatar download failed" in capsys.readouterr().err
    saved = state.load(state.clone_id(42, 123, "broadcast"))
    assert saved.destination_peer_id == 999
    assert not any(
        isinstance(item, functions.channels.EditPhotoRequest)
        for item in client.requests
    )
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert "clone-init-avatar" not in [record["action"] for record in audits]


def test_clone_init_rerun_skips_unchanged_avatar(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source.photo = SimpleNamespace(photo_id=555)
    make_session_fake(monkeypatch, client)

    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    capsys.readouterr()
    assert state.load(state.clone_id(42, 123, "broadcast")).avatar_for(123) == 555

    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    capsys.readouterr()

    edits = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.EditPhotoRequest)
    ]
    assert len(edits) == 1
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert [record["action"] for record in audits].count("clone-init-avatar") == 1


def test_clone_init_commit_adopts_half_created_marker_channel(
    config_env, monkeypatch, capsys
):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, clone_state.creation_marker, creator=True)
    make_session_fake(monkeypatch, client)

    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["destination"]["id"] == 999
    assert not any(
        isinstance(item, functions.channels.CreateChannelRequest)
        for item in client.requests
    )
    assert any(
        isinstance(item, functions.channels.EditTitleRequest)
        for item in client.requests
    )
    assert state.load(clone_state.clone_id).destination_peer_id == 999


def test_clone_init_commit_blocks_multiple_marker_matches_without_mutation(
    config_env, monkeypatch, capsys
):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    state.save(clone_state)

    class MultipleMarkersClient(CloneInitClient):
        async def iter_dialogs(self):
            for channel_id in (998, 999):
                yield SimpleNamespace(
                    entity=channel(
                        channel_id, clone_state.creation_marker, creator=True
                    )
                )

    client = MultipleMarkersClient()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 2
    )

    assert "multiple channels" in capsys.readouterr().err
    assert client.requests == []
    assert not safety.audit_path().exists()


def linked_group(group_id=777, title="Source chat"):
    return channel(group_id, title, broadcast=False, megagroup=True, forum=False)


def test_init_audits_the_discussion_title_edit_before_sending_it(
    config_env, monkeypatch, capsys
):
    """The discussion group's rename is a mutation like the channel's: its
    audit row is on disk before the EditTitleRequest leaves."""

    class AuditProbeClient(CloneInitClient):
        audited_at_edit: list[str] | None = None

        async def __call__(self, request):
            if (
                isinstance(request, functions.channels.EditTitleRequest)
                and request.channel is self.discussion
            ):
                path = safety.audit_path()
                lines = path.read_text().splitlines() if path.exists() else []
                self.audited_at_edit = [json.loads(line)["action"] for line in lines]
            return await super().__call__(request)

    client = AuditProbeClient()
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    assert client.audited_at_edit is not None
    assert client.audited_at_edit[-1] == "clone-init-discussion-title"


@pytest.mark.parametrize("marker", ["unavailable", "none"])
def test_plain_reinit_keeps_posts_only_clone_with_copied_posts(
    marker, config_env, monkeypatch, capsys
):
    """posts copied before a link have no anchors, so a posts-only
    clone with copied posts is never upgraded in place. A plain re-init once
    the linked group reads fine keeps the marker, links nothing, and points
    to --replace."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    clone_state.comments = marker
    if marker == "unavailable":
        clone_state.discussion_source_peer_id = 777
    for source_id in range(1, 51):
        clone_state.record_mapping(source_id, source_id + 1000)
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["clone"]["comments"] == marker
    assert "--replace" in captured.err
    saved = state.load(result["clone"]["id"])
    assert saved.comments == marker
    assert saved.discussion_destination_peer_id is None
    assert saved.discussion_linked is False
    assert len(saved.id_map) == 50
    assert client.discussion is None
    assert not any(
        isinstance(item, functions.channels.SetDiscussionGroupRequest)
        for item in client.requests
    )


def test_reinit_while_source_unlinked_keeps_copied_comments_loadable(
    config_env, monkeypatch, capsys
):
    """A clone that already copied comments, re-inited while its source has
    no linked group, records "none" and must still load: `clone status` reads
    it, and relinking the same group resumes the comments where they stopped
    instead of copying them again onto the destination group."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 777
    clone_state.discussion_destination_peer_id = 1001
    clone_state.discussion_linked = True
    clone_state.record_mapping(1, 1001)
    clone_state.record_discussion_mapping(12, 5)
    clone_state.discussion_cursor = 12
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    client.discussion = channel(
        1001,
        "[Clone] Source chat",
        creator=True,
        broadcast=False,
        megagroup=True,
        forum=False,
    )
    make_session_fake(monkeypatch, client)
    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["clone"]["comments"] == "none"

    assert main(["clone", "status", "@source", "--json"]) == 0
    capsys.readouterr()

    client.linked = linked_group()
    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["clone"]["comments"] == "enabled"
    saved = state.load(clone_state.clone_id)
    assert saved.discussion_cursor == 12
    assert saved.discussion_id_map == {12: 5}


def test_reinit_with_unreadable_group_after_copied_comments_stays_loadable(
    config_env, monkeypatch, capsys
):
    """The init-time twin of sync's degrade: a clone with copied comments whose
    linked group turns unreadable records "unavailable" and still loads."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 777
    clone_state.discussion_destination_peer_id = 1001
    clone_state.discussion_linked = True
    clone_state.record_mapping(1, 1001)
    clone_state.record_discussion_mapping(12, 5)
    clone_state.discussion_cursor = 12
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    client.linked = linked_group()
    client.linked_history_error = telethon_errors.ChannelPrivateError(request=None)
    make_session_fake(monkeypatch, client)
    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["clone"]["comments"] == "unavailable"

    saved = state.load(clone_state.clone_id)
    assert saved.comments == "unavailable"
    assert saved.discussion_cursor == 0
    assert saved.discussion_id_map == {}


def test_plain_reinit_upgrades_unavailable_clone_with_no_copied_posts(
    config_env, monkeypatch, capsys
):
    """Nothing copied yet means no anchor is missing: a re-init once the
    linked group reads fine links it and reports comments == "enabled"."""
    client = CloneInitClient()
    client.linked = linked_group()
    client.linked_history_error = telethon_errors.ChannelPrivateError(request=None)
    make_session_fake(monkeypatch, client)
    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    assert json.loads(capsys.readouterr().out)["clone"]["comments"] == "unavailable"

    client.linked_history_error = None
    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["comments"] == "enabled"
    assert state.load(result["clone"]["id"]).discussion_linked is True


def test_clone_init_mutes_created_peers_forever(config_env, monkeypatch, capsys):
    from tgcli.clone.ergonomics import MUTE_FOREVER_UNTIL

    client = CloneInitClient()
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ergonomics"] == {"muted": True, "folder": "added"}
    mutes = [
        item
        for item in client.requests
        if isinstance(item, functions.account.UpdateNotifySettingsRequest)
    ]
    assert len(mutes) == 2
    assert {item.settings.mute_until for item in mutes} == {MUTE_FOREVER_UNTIL}
    muted_ids = {item.peer.peer.channel_id for item in mutes}
    assert muted_ids == {999, 1001}
    [folder_update] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.UpdateDialogFilterRequest)
    ]
    included = {peer.channel_id for peer in folder_update.filter.include_peers}
    assert included == {999, 1001}


def test_clone_init_skips_mute_when_already_forever(config_env, monkeypatch, capsys):
    from tgcli.clone.ergonomics import MUTE_FOREVER_UNTIL

    client = CloneInitClient()
    client.notify_mute_until = {999: MUTE_FOREVER_UNTIL}
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    mutes = [
        item
        for item in client.requests
        if isinstance(item, functions.account.UpdateNotifySettingsRequest)
    ]
    assert mutes == []
    assert json.loads(capsys.readouterr().out)["ergonomics"]["muted"] is True


def test_clone_init_mute_failure_warns_and_keeps_exit_0(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.notify_error = RuntimeError("notify denied")
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ergonomics"]["muted"] is False
    assert "warning: clone mute failed" in captured.err


def test_clone_init_reuses_existing_clone_folder(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.dialog_filters = [
        types.DialogFilter(
            id=7,
            title=types.TextWithEntities(text="Clone", entities=[]),
            pinned_peers=[],
            include_peers=[types.InputPeerChannel(channel_id=50, access_hash=0)],
            exclude_peers=[],
        )
    ]
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ergonomics"]["folder"] == "added"
    [update] = [
        item
        for item in client.requests
        if isinstance(item, functions.messages.UpdateDialogFilterRequest)
    ]
    assert update.id == 7
    included = {peer.channel_id for peer in update.filter.include_peers}
    assert included == {50, 999}


def test_clone_init_folder_present_when_peers_already_included(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    client.dialog_filters = [
        types.DialogFilter(
            id=7,
            title=types.TextWithEntities(text="Clone", entities=[]),
            pinned_peers=[],
            include_peers=[types.InputPeerChannel(channel_id=999, access_hash=0)],
            exclude_peers=[],
        )
    ]
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ergonomics"]["folder"] == "present"
    assert not any(
        isinstance(item, functions.messages.UpdateDialogFilterRequest)
        for item in client.requests
    )


def test_clone_init_folder_unavailable_on_rpc_failure(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.folder_error = RuntimeError("filters denied")
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ergonomics"]["folder"] == "unavailable"
    assert "warning: clone folder unavailable" in captured.err


def test_clone_init_commit_no_comments_creates_one_peer_and_disables(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--no-comments", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["comments"] == "disabled"
    creates = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.CreateChannelRequest)
    ]
    assert len(creates) == 1
    assert not creates[0].title.endswith("-discussion")
    assert not any(
        isinstance(item, functions.channels.SetDiscussionGroupRequest)
        for item in client.requests
    )
    saved = state.load(result["clone"]["id"])
    assert saved.comments == "disabled"
    assert saved.discussion_linked is False
    assert saved.discussion_destination_peer_id is None


def test_clone_init_no_comments_over_enabled_is_policy_error(
    config_env, monkeypatch, capsys
):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.destination_peer_id = 999
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 777
    clone_state.discussion_destination_peer_id = 1001
    clone_state.discussion_linked = True
    state.save(clone_state)

    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--no-comments", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 2
    err = capsys.readouterr().err.casefold()
    assert "no-comments" in err or "disabled" in err or "comments" in err


def test_clone_init_no_comments_over_paused_comments_is_policy_error(
    config_env, monkeypatch, capsys
):
    """A clone whose source unlinked its group ("none", still linked, with
    comment progress) resumes on relink, so --no-comments refuses it like an
    enabled one instead of saving a state that no longer loads."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    clone_state.comments = "none"
    clone_state.discussion_source_peer_id = 777
    clone_state.discussion_destination_peer_id = 1001
    clone_state.discussion_linked = True
    clone_state.record_mapping(1, 1001)
    clone_state.record_discussion_mapping(12, 5)
    clone_state.discussion_cursor = 12
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--no-comments", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 2
    assert "--no-comments" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.comments == "none"
    assert saved.discussion_id_map == {12: 5}


def test_init_rejects_a_discussion_marker_matching_multiple_groups(
    config_env, monkeypatch, capsys
):
    """Two dialogs titled with the discussion marker: PolicyError, exit 4."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    marker = f"{clone_state.creation_marker}-discussion"

    class MultipleDiscussionMarkersClient(CloneInitClient):
        async def iter_dialogs(self):
            yield SimpleNamespace(entity=self.destination)
            for group_id in (1001, 1002):
                yield SimpleNamespace(
                    entity=channel(
                        group_id,
                        marker,
                        creator=True,
                        broadcast=False,
                        megagroup=True,
                        forum=False,
                    )
                )

    client = MultipleDiscussionMarkersClient()
    client.destination = channel(999, "Source channel", creator=True)
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 2
    )

    assert "multiple" in capsys.readouterr().err
    saved = state.load(clone_state.clone_id)
    assert saved.discussion_destination_peer_id is None
    assert saved.discussion_linked is False


CORRUPT_DB = b"not a sqlite database"


def _write_unreadable_state():
    cid = state.clone_id(42, 123, "broadcast")
    path = state.path_for(cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CORRUPT_DB)
    return cid, path


def test_clone_init_commit_without_replace_hints_replace_on_unreadable_state(
    config_env, monkeypatch, capsys
):
    _, path = _write_unreadable_state()
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 2
    )

    err = capsys.readouterr().err
    assert "--replace" in err
    assert path.read_bytes() == CORRUPT_DB
    assert client.requests == []


def test_clone_init_preview_reports_supersede_for_unreadable_state(
    config_env, monkeypatch, capsys
):
    _write_unreadable_state()
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--replace", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["supersede"] == {"existing": True, "readable": False, "replace": True}
    assert [type(request) for request in client.requests] == [
        functions.channels.GetFullChannelRequest
    ]


class _TitleFailsOnce(CloneInitClient):
    """Telegram fails the destination's title edit once, after creating it."""

    def __init__(self):
        super().__init__()
        self.title_failures = 1

    async def __call__(self, request):
        if (
            isinstance(request, functions.channels.EditTitleRequest)
            and self.title_failures
        ):
            self.title_failures -= 1
            self.requests.append(request)
            raise telethon_errors.ServerError(
                request=None, message="INTERNAL", code=500
            )
        return await super().__call__(request)


def _creations(client):
    return [
        request
        for request in client.requests
        if isinstance(request, functions.channels.CreateChannelRequest)
    ]


@pytest.mark.parametrize("previous", ["readable", "unreadable", "none"])
def test_clone_init_replace_commit_retry_resumes_its_own_destination(
    config_env, monkeypatch, capsys, previous
):
    """A `--replace` commit that failed after creating the new destination is
    re-committed with the same preview (H3): it must resume that destination,
    not archive it and create a second channel."""
    cid = state.clone_id(42, 123, "broadcast")
    if previous == "readable":
        old = state.CloneState.new(
            account_user_id=42, source_peer_id=123, source_title="Source channel"
        )
        old.creation_marker = f"tgcli-clone-{cid[:12]}-old"
        old.destination_peer_id = 555
        state.save(old)
    elif previous == "unreadable":
        _write_unreadable_state()
    client = _TitleFailsOnce()
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--replace", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 1
    capsys.readouterr()
    assert len(_creations(client)) == 1
    marker = state.load(cid).creation_marker

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["clone"]["destination"]["id"] == 999
    assert len(_creations(client)) == 1
    saved = state.load(cid)
    assert saved.destination_peer_id == 999
    assert saved.creation_marker == marker
    archived = list(state.clones_dir().glob(f"{cid}.db.superseded-*"))
    assert len(archived) == (0 if previous == "none" else 1)


def test_clone_init_commit_refuses_while_another_run_holds_the_clone(
    config_env, monkeypatch, capsys
):
    """A sync on another session role owns the slot: the commit leg must not
    create, rename, or re-shape the destination underneath it."""
    import fcntl

    client = CloneInitClient()
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    client.requests.clear()
    clone_id = preview["clone"]["id"]
    state.clones_dir().mkdir(parents=True, exist_ok=True)
    holder = (state.clones_dir() / f"{clone_id}.lock").open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        argv = ["clone", "init", "@source", "--commit", preview["preview_id"]]
        assert main([*argv, "--json"]) == 2
    finally:
        holder.close()

    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "BLOCKED"
    assert f"clone {clone_id} is busy" in error["message"]
    assert client.requests == []
    assert state.load(clone_id) is None

    assert main([*argv, "--json"]) == 0
    assert state.load(clone_id).destination_peer_id == 999


class _FlakyTitles(CloneInitClient):
    """Fails the next destination title edit when `fail_title` is set."""

    fail_title = False

    async def __call__(self, request):
        if isinstance(request, functions.channels.EditTitleRequest) and (
            self.fail_title
        ):
            self.fail_title = False
            raise telethon_errors.ServerError(
                request=None, message="INTERNAL", code=500
            )
        return await super().__call__(request)


class CloneInitReplaceMachine(RuleBasedStateMachine):
    """Previews with or without
    `--replace`, commits Telegram may cut short after the destination
    exists, and retries of any of them. Each preview creates at most one
    destination however often it is re-committed, and the slot always
    loads."""

    previews = Bundle("previews")

    def __init__(self):
        super().__init__()
        self.patch = pytest.MonkeyPatch()
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / "config.toml").write_text(SAMPLE)
        self.patch.setenv("TGCLI_CONFIG", str(root / "config.toml"))
        self.patch.setenv("TGCLI_STATE_DIR", str(root / "state"))
        self.patch.delenv("TGCLI_READONLY", raising=False)
        self.client = _FlakyTitles()
        make_session_fake(self.patch, self.client)
        self.created_by: dict[str, int] = {}

    def _quiet(self, argv) -> int:
        with (
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = main(argv)
        self.last_out = out.getvalue()
        return code

    @rule(target=previews, replace=st.booleans())
    def preview(self, replace):
        argv = ["clone", "init", "@source", "--json"]
        assert self._quiet(argv + (["--replace"] if replace else [])) == 0
        return json.loads(self.last_out)["preview_id"]

    @rule(preview_id=previews, fail_title=st.booleans())
    def commit(self, preview_id, fail_title):
        self.client.fail_title = fail_title
        before = len(_creations(self.client))
        self._quiet(["clone", "init", "@source", "--commit", preview_id, "--json"])
        self.client.fail_title = False
        made = len(_creations(self.client)) - before
        self.created_by[preview_id] = self.created_by.get(preview_id, 0) + made

    @invariant()
    def each_preview_creates_at_most_one_destination(self):
        assert all(count <= 1 for count in self.created_by.values())

    @invariant()
    def the_slot_always_loads(self):
        state.load(state.clone_id(42, 123, "broadcast"))

    def teardown(self):
        self.patch.undo()
        self.directory.cleanup()


CloneInitReplaceMachine.TestCase.settings = settings(
    max_examples=40, stateful_step_count=8, derandomize=True, deadline=None
)
