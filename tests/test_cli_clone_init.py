import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tgcli import safety
from tgcli.cli import main
from tgcli.clone import state
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
            pending = state.load(state.clone_id(42, 123))
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
    assert result["account_flood"] == {
        "cooldown_until": None,
        "last_peer_created_at": None,
    }
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


def test_clone_init_preview_peers_to_create_nonzero_on_replace_with_recorded_destination(
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

    assert main(["clone", "init", "@source", "--replace", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["peers_to_create"] == 2
    assert result["supersede"]["replace"] is True


def test_clone_init_preview_includes_account_flood_record(
    config_env, monkeypatch, capsys
):
    from datetime import UTC, datetime, timedelta

    from tgcli.clone import flood

    at = datetime.now(UTC) - timedelta(hours=2)
    until = datetime.now(UTC) + timedelta(minutes=15)
    flood.record_peer_created(42, at)
    flood.arm_cooldown(42, until)
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["account_flood"] == {
        "cooldown_until": until.isoformat(),
        "last_peer_created_at": at.isoformat(),
    }


def test_clone_init_preview_accepts_nonforum_megagroup(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Team chat", "kind": "megagroup"}


def test_clone_init_preview_accepts_forum_megagroup(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = channel(
        123, "Forum chat", broadcast=False, megagroup=True, forum=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Forum chat", "kind": "forum"}


def test_clone_init_preview_accepts_private_dialog(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = user()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Alex Smith", "kind": "dialog"}


def test_clone_init_preview_accepts_bot_dialog(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = user(bot=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Alex Smith", "kind": "dialog"}


def test_clone_init_preview_accepts_basic_group(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source = legacy_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Legacy group", "kind": "basic"}


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


def test_clone_init_forum_audit_failure_blocks_toggle_on_retry(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
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
    clone_state = state.CloneState.new(
        account_user_id=42,
        source_peer_id=123,
        source_title="Forum chat",
        source_kind="forum",
    )
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    def fail_audit(action, account, details):
        assert (action, account, details) == (
            "clone-init-forum",
            "main",
            {"clone_id": clone_state.clone_id},
        )
        raise PolicyError("audit write failed")

    monkeypatch.setattr(safety, "append_audit", fail_audit)
    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 2

    assert "audit write failed" in capsys.readouterr().err
    assert client.requests == []
    assert client.destination.forum is False


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
    assert audits[-1]["action"] == "clone-init-about"


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


def test_clone_init_commit_copies_channel_avatar_and_cleans_tempfile(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source.photo = object()
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
        if isinstance(item, functions.channels.EditPhotoRequest)
    ]
    assert request.channel is client.destination
    assert isinstance(request.photo, types.InputChatUploadedPhoto)
    assert isinstance(request.photo.file, types.InputFile)
    assert len(client.uploads) == 1
    assert all(
        not path.exists() and not path.parent.exists() for path in client.downloads
    )
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert audits[-1]["action"] == "clone-init-avatar"


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
    saved = state.load(state.clone_id(42, 123))
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
    assert state.load(state.clone_id(42, 123)).avatar_for(123) == 555

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


def test_clone_init_rerun_recopies_changed_avatar(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.source.photo = SimpleNamespace(photo_id=555)
    make_session_fake(monkeypatch, client)

    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    capsys.readouterr()

    client.source.photo = SimpleNamespace(photo_id=556)
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
    assert len(edits) == 2
    assert state.load(state.clone_id(42, 123)).avatar_for(123) == 556


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


def test_clone_init_commit_adopts_plain_forum_marker_then_enables_forum(
    config_env, monkeypatch, capsys
):
    clone_state = state.CloneState.new(
        account_user_id=42,
        source_peer_id=123,
        source_title="Forum chat",
        source_kind="forum",
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    state.save(clone_state)
    client = CloneInitClient()
    client.source = channel(
        123, "Forum chat", broadcast=False, megagroup=True, forum=True
    )
    client.destination = channel(
        999,
        clone_state.creation_marker,
        creator=True,
        broadcast=False,
        megagroup=True,
        forum=False,
    )
    make_session_fake(monkeypatch, client)
    assert main(["clone", "init", "@source", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["status"] == "ready"
    assert state.load(clone_state.clone_id).destination_peer_id == 999
    assert not any(
        isinstance(item, functions.channels.CreateChannelRequest)
        for item in client.requests
    )
    assert any(
        isinstance(item, functions.channels.ToggleForumRequest)
        for item in client.requests
    )
    assert client.destination.forum is True
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert [record["action"] for record in audits] == [
        "clone-init-forum",
        "clone-init-title",
    ]


def test_clone_init_commit_reuses_recorded_destination_without_mutation(
    config_env, monkeypatch, capsys
):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    make_session_fake(monkeypatch, client)

    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["destination"] == {
        "id": 999,
        "title": "[Clone] Source channel",
    }
    assert not any(
        isinstance(item, functions.channels.CreateChannelRequest)
        for item in client.requests
    )
    assert not any(
        isinstance(item, functions.channels.EditTitleRequest)
        for item in client.requests
    )
    assert any(
        isinstance(item, functions.channels.GetFullChannelRequest)
        for item in client.requests
    )


def test_clone_init_commit_retitles_legacy_unprefixed_destination(
    config_env, monkeypatch, capsys
):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, "Source channel", creator=True)
    make_session_fake(monkeypatch, client)

    preview = stored_preview()
    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["destination"] == {
        "id": 999,
        "title": "[Clone] Source channel",
    }
    [title_edit] = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.EditTitleRequest)
    ]
    assert title_edit.title == "[Clone] Source channel"
    assert client.destination.title == "[Clone] Source channel"


def test_clone_init_create_flood_wait_persists_cooldown(
    config_env, monkeypatch, capsys
):
    class FloodClient(CloneInitClient):
        async def __call__(self, request):
            self.requests.append(request)
            raise telethon_errors.FloodWaitError(request=None, capture=600)

    client = FloodClient()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 5
    )

    saved = state.load(state.clone_id(42, 123))
    assert saved.cooldown_deadline() is not None
    assert len(client.requests) == 1


def test_clone_init_commit_blocks_under_account_cooldown_before_network(
    config_env, monkeypatch, capsys
):
    from datetime import UTC, datetime, timedelta

    from tgcli.clone import flood

    flood.arm_cooldown(42, datetime.now(UTC) + timedelta(minutes=10))
    client = CloneInitClient()

    async def forbid_get_entity(*_args, **_kwargs):
        raise AssertionError("get_entity must not run under account cooldown")

    async def forbid_get_me():
        raise AssertionError("get_me must not run under account cooldown")

    client.get_entity = forbid_get_entity  # type: ignore[method-assign]
    client.get_me = forbid_get_me  # type: ignore[method-assign]
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 5
    )
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["retry_after"] > 0
    assert client.requests == []


def test_clone_init_preview_not_blocked_by_account_cooldown(
    config_env, monkeypatch, capsys
):
    from datetime import UTC, datetime, timedelta

    from tgcli.clone import flood

    flood.arm_cooldown(42, datetime.now(UTC) + timedelta(minutes=10))
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["status"] == "planned"
    assert [type(request) for request in client.requests] == [
        functions.channels.GetFullChannelRequest
    ]


def test_clone_init_create_records_peer_created_timestamp(
    config_env, monkeypatch, capsys
):
    from tgcli.clone import flood

    client = CloneInitClient()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    record = flood.load(42)
    assert record["last_peer_created_at"] is not None


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


def test_init_creates_and_links_discussion_group(config_env, monkeypatch, capsys):
    """Source with a readable linked group: init creates a private owned
    megagroup, links it via SetDiscussionGroupRequest, and records
    comments == "enabled" with discussion_linked True."""
    client = CloneInitClient()
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["comments"] == "enabled"
    [created] = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.CreateChannelRequest)
        and item.title.endswith("-discussion")
    ]
    assert created.megagroup is True and created.broadcast is False
    [link] = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.SetDiscussionGroupRequest)
    ]
    assert link.broadcast is client.destination and link.group is client.discussion
    assert any(
        isinstance(item, functions.channels.TogglePreHistoryHiddenRequest)
        and item.enabled is False
        for item in client.requests
    )
    assert client.discussion.title == "[Clone] Source chat"
    saved = state.load(result["clone"]["id"])
    assert saved.comments == "enabled"
    assert saved.discussion_source_peer_id == 777
    assert saved.discussion_destination_peer_id == 1001
    assert saved.discussion_linked is True
    [discussion_title] = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.EditTitleRequest)
        and item.channel is client.discussion
    ]
    assert discussion_title.title == "[Clone] Source chat"
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert "clone-init-discussion-create" in [record["action"] for record in audits]
    assert "clone-init-discussion-link" in [record["action"] for record in audits]


def test_init_discussion_title_is_idempotent_on_rerun(config_env, monkeypatch, capsys):
    """Re-init against an already-prefixed discussion group issues no title edit."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    clone_state.discussion_destination_peer_id = 1001
    clone_state.discussion_linked = True
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 777
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    client.linked = linked_group()
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

    capsys.readouterr()
    assert not any(
        isinstance(item, functions.channels.EditTitleRequest)
        for item in client.requests
    )
    assert client.discussion.title == "[Clone] Source chat"


@pytest.mark.parametrize(
    "error",
    [
        telethon_errors.ChannelPrivateError(request=None),
        telethon_errors.ChatAdminRequiredError(request=None),
        ValueError("no history"),
    ],
)
def test_init_marks_unreadable_discussion_group_unavailable(
    error, config_env, monkeypatch, capsys
):
    """Linked group whose history raises ChannelPrivateError: init succeeds
    posts-only, state records comments == "unavailable" and the source
    discussion peer id, and no group is created."""
    client = CloneInitClient()
    client.linked = linked_group()
    client.linked_history_error = error
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["comments"] == "unavailable"
    assert result["clone"]["status"] == "ready"
    saved = state.load(result["clone"]["id"])
    assert saved.comments == "unavailable"
    assert saved.discussion_source_peer_id == 777
    assert saved.discussion_destination_peer_id is None
    assert saved.discussion_linked is False
    assert client.discussion is None
    assert not any(
        isinstance(item, functions.channels.SetDiscussionGroupRequest)
        for item in client.requests
    )


def test_clone_init_mutes_created_peers_forever(config_env, monkeypatch, capsys):
    from tgcli.commands.dialog import MUTE_FOREVER_UNTIL

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


def test_clone_init_unresolved_discussion_marks_muted_false(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    original_get_entity = client.get_entity

    async def flaky_get_entity(ref):
        if isinstance(ref, types.PeerChannel) and ref.channel_id == 1001:
            raise ValueError("discussion gone")
        return await original_get_entity(ref)

    client.get_entity = flaky_get_entity  # type: ignore[method-assign]

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["ergonomics"]["muted"] is False
    assert "warning: clone mute skipped for discussion peer" in captured.err


def test_clone_init_skips_mute_when_already_forever(config_env, monkeypatch, capsys):
    from tgcli.commands.dialog import MUTE_FOREVER_UNTIL

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


def test_init_without_linked_chat_records_no_comments(config_env, monkeypatch, capsys):
    """linked_chat_id None: comments == "none", no extra requests."""
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["comments"] == "none"
    saved = state.load(result["clone"]["id"])
    assert saved.comments == "none"
    assert saved.discussion_source_peer_id is None
    core = [
        type(request)
        for request in client.requests
        if type(request)
        in {
            functions.channels.CreateChannelRequest,
            functions.channels.EditTitleRequest,
            functions.channels.GetFullChannelRequest,
        }
    ]
    assert core == [
        functions.channels.CreateChannelRequest,
        functions.channels.EditTitleRequest,
        functions.channels.GetFullChannelRequest,
    ]
    assert result["ergonomics"]["muted"] is True
    assert result["ergonomics"]["folder"] in {"added", "present"}


def test_clone_init_preview_records_no_comments_choice(config_env, monkeypatch, capsys):
    client = CloneInitClient()
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--no-comments", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    stored = json.loads(
        (safety.previews_dir() / f"{result['preview_id']}.json").read_text()
    )
    assert stored["payload"]["no_comments"] is True


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


def test_clone_init_recommit_keeps_disabled_comments_without_flag(
    config_env, monkeypatch, capsys
):
    """CONTRACT: comments \"disabled\" is posts-only forever for the slot."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.destination_peer_id = 999
    clone_state.comments = "disabled"
    clone_state.creation_marker = "tgcli-clone-marker"
    state.save(clone_state)

    client = CloneInitClient()
    client.destination = channel(999, "[Clone] Source channel", creator=True)
    client.linked = linked_group()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    preview_id = preview["preview_id"]
    stored = json.loads((safety.previews_dir() / f"{preview_id}.json").read_text())
    assert stored["payload"].get("no_comments") is not True

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["comments"] == "disabled"
    assert not any(
        isinstance(item, functions.channels.CreateChannelRequest)
        and str(getattr(item, "title", "")).endswith("-discussion")
        for item in client.requests
    )
    assert not any(
        isinstance(item, functions.channels.SetDiscussionGroupRequest)
        for item in client.requests
    )
    saved = state.load(result["clone"]["id"])
    assert saved.comments == "disabled"
    assert saved.discussion_linked is False


def test_init_ignores_monoforum_links(config_env, monkeypatch, capsys):
    """linked_chat_id None + linked_monoforum_id set: comments == "none"."""
    client = CloneInitClient()
    client.linked_monoforum_id = 888
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 0
    )

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["comments"] == "none"
    saved = state.load(result["clone"]["id"])
    assert saved.comments == "none"
    assert saved.discussion_source_peer_id is None
    assert not any(
        isinstance(item, functions.channels.SetDiscussionGroupRequest)
        for item in client.requests
    )


def test_init_relinks_after_a_crash_between_create_and_link(
    config_env, monkeypatch, capsys
):
    """Seeded state with discussion_destination_peer_id set and
    discussion_linked False: init adopts the peer (no CreateChannelRequest)
    and issues SetDiscussionGroupRequest, leaving discussion_linked True."""
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.creation_marker = f"tgcli-clone-{clone_state.clone_id[:12]}"
    clone_state.destination_peer_id = 999
    clone_state.discussion_destination_peer_id = 1001
    state.save(clone_state)
    client = CloneInitClient()
    client.destination = channel(999, "Source channel", creator=True)
    client.linked = linked_group()
    client.discussion = channel(
        1001,
        f"{clone_state.creation_marker}-discussion",
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

    capsys.readouterr()
    assert not any(
        isinstance(item, functions.channels.CreateChannelRequest)
        for item in client.requests
    )
    [link] = [
        item
        for item in client.requests
        if isinstance(item, functions.channels.SetDiscussionGroupRequest)
    ]
    assert link.group is client.discussion
    saved = state.load(clone_state.clone_id)
    assert saved.discussion_linked is True
    assert saved.discussion_destination_peer_id == 1001
    assert saved.comments == "enabled"


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


def test_clone_init_commit_readonly_blocks_before_config_session_and_preview_use(
    monkeypatch,
):
    from tgcli import cli

    preview = stored_preview()
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )

    assert (
        main(
            [
                "--readonly",
                "clone",
                "init",
                "@source",
                "--commit",
                preview["preview_id"],
            ]
        )
        == 2
    )

    assert safety.consume_preview(preview["preview_id"])["kind"] == "clone-init"


def _write_v1_state(source_title="Old", extra=None):
    cid = state.clone_id(42, 123)
    path = state.path_for(cid)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "account_user_id": 42,
        "source_peer_id": 123,
        "source_title": source_title,
    }
    if extra:
        data.update(extra)
    path.write_text(json.dumps(data))
    return cid, path


def test_clone_init_commit_without_replace_hints_replace_on_unreadable_state(
    config_env, monkeypatch, capsys
):
    _, path = _write_v1_state()
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert (
        main(["clone", "init", "@source", "--commit", preview["preview_id"], "--json"])
        == 2
    )

    err = capsys.readouterr().err
    assert "--replace" in err
    assert json.loads(path.read_text())["version"] == 1
    assert client.requests == []


def test_clone_init_preview_reports_supersede_for_unreadable_state(
    config_env, monkeypatch, capsys
):
    _write_v1_state()
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--replace", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["supersede"] == {"existing": True, "readable": False, "replace": True}
    assert [type(request) for request in client.requests] == [
        functions.channels.GetFullChannelRequest
    ]


def test_clone_init_replace_supersedes_unreadable_state_and_creates_fresh(
    config_env, monkeypatch, capsys
):
    cid, _ = _write_v1_state()
    sidecar = state.clones_dir() / f"{cid}-participants.jsonl"
    sidecar.write_text('{"peer":"source"}\n')
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--replace", "--json"]) == 0
    preview_id = json.loads(capsys.readouterr().out)["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["status"] == "ready"
    assert result["clone"]["destination"]["id"] == 999

    saved = state.load(cid)
    assert saved.version == state.VERSION
    assert saved.destination_peer_id == 999

    state_archive = list(state.clones_dir().glob(f"{cid}.json.superseded-*"))
    sidecar_archive = list(
        state.clones_dir().glob(f"{cid}-participants.jsonl.superseded-*")
    )
    assert len(state_archive) == 1
    assert json.loads(state_archive[0].read_text())["version"] == 1
    assert len(sidecar_archive) == 1
    assert not sidecar.exists()

    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert "clone-init-replace" in actions


def test_clone_init_replace_does_not_reuse_recorded_destination(
    config_env, monkeypatch, capsys
):
    old = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    old.creation_marker = f"tgcli-clone-{old.clone_id[:12]}"
    old.destination_peer_id = 555
    state.save(old)
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--replace", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["supersede"] == {"existing": True, "readable": True, "replace": True}
    preview_id = preview["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["destination"]["id"] == 999

    saved = state.load(old.clone_id)
    assert saved.destination_peer_id == 999
    assert saved.creation_marker.startswith(f"tgcli-clone-{old.clone_id[:12]}-")
    assert saved.creation_marker != f"tgcli-clone-{old.clone_id[:12]}"
    assert any(
        isinstance(r, functions.channels.CreateChannelRequest) for r in client.requests
    )


def test_clone_init_replace_without_existing_state_is_plain_init(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--replace", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["supersede"] == {
        "existing": False,
        "readable": None,
        "replace": True,
    }
    preview_id = preview["preview_id"]

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["destination"]["id"] == 999

    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert "clone-init-replace" not in actions
    saved = state.load(state.clone_id(42, 123))
    assert saved.creation_marker.startswith(
        f"tgcli-clone-{state.clone_id(42, 123)[:12]}-"
    )
