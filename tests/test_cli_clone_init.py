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


def user(user_id=123, *, first_name="Alex", last_name="Smith", bot=False):
    return types.User(
        id=user_id, first_name=first_name, last_name=last_name, bot=bot,
    )


class CloneInitClient:
    def __init__(self):
        self.source = channel(123, "Source channel", noforwards=True)
        self.source_about = ""
        self.requests = []
        self.destination = None
        self.downloads = []
        self.uploads = []

    async def get_entity(self, ref):
        if isinstance(ref, types.PeerChannel) and self.destination is not None:
            return self.destination
        assert ref == "@source"
        return self.source

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_messages(self, entity, limit=None):
        assert entity is self.source
        assert limit == 0
        return SimpleNamespace(total=321)

    async def iter_dialogs(self):
        if self.destination is not None:
            yield SimpleNamespace(entity=self.destination)

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
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return SimpleNamespace(
                full_chat=SimpleNamespace(about=self.source_about)
            )
        if isinstance(request, functions.users.GetFullUserRequest):
            return SimpleNamespace(full_user=SimpleNamespace(about=self.source_about))
        if isinstance(request, functions.channels.CreateChannelRequest):
            clone_id = state.clone_id(42, 123)
            pending = state.load(clone_id)
            assert pending.destination_peer_id is None
            assert pending.creation_marker == request.title
            self.destination = channel(999, request.title, creator=True)
            return SimpleNamespace(chats=[self.destination])
        if isinstance(request, functions.channels.EditTitleRequest):
            self.destination.title = request.title
            return SimpleNamespace()
        if isinstance(request, functions.messages.EditChatAboutRequest):
            self.destination.about = request.about
            return True
        if isinstance(request, functions.channels.EditPhotoRequest):
            self.destination.photo = request.photo
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
        "id": 123, "title": "Source channel", "kind": "broadcast",
    }
    assert result["clone"]["status"] == "planned"
    assert result["clone"]["commit_required"] is True
    assert result["approximate_message_count"] == 321
    assert result["protected"] is True
    assert result["preview_id"].startswith("p_")
    assert client.requests == []
    assert client.session_mutation_safe is False


def test_clone_init_preview_accepts_nonforum_megagroup(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source = channel(
        123, "Team chat", broadcast=False, megagroup=True, forum=False
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Team chat", "kind": "megagroup"}


def test_clone_init_preview_accepts_private_dialog(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source = user()
    make_session_fake(monkeypatch, client)

    assert main(["clone", "init", "@source", "--json"]) == 0

    source = json.loads(capsys.readouterr().out)["clone"]["source"]
    assert source == {"id": 123, "title": "Alex Smith", "kind": "dialog"}


@pytest.mark.parametrize(
    ("source", "error"),
    [
        (channel(123, "Forum", broadcast=False, megagroup=True, forum=True),
         "forum topics are not supported"),
        (types.Chat(id=123, title="Legacy group", photo=types.ChatPhotoEmpty(),
                    participants_count=2, date=None, version=1),
         "basic groups are not supported"),
        (user(bot=True), "bots are not supported"),
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

    assert main(["clone", "init", "@source", "--commit", preview_id, "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["status"] == "ready"
    assert result["clone"]["commit_required"] is False
    assert result["clone"]["destination"] == {"id": 999, "title": "Source channel"}
    saved = state.load(result["clone"]["id"])
    assert saved.destination_peer_id == 999
    assert client.session_mutation_safe is True
    assert [type(request) for request in client.requests] == [
        functions.channels.CreateChannelRequest,
        functions.channels.EditTitleRequest,
        functions.channels.GetFullChannelRequest,
    ]
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert [record["action"] for record in audits] == [
        "clone-init-create",
        "clone-init-title",
    ]


def test_clone_init_commit_copies_nonempty_channel_description(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source_about = "Source description"
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert main(
        ["clone", "init", "@source", "--commit", preview["preview_id"], "--json"]
    ) == 0

    capsys.readouterr()
    [request] = [item for item in client.requests
                 if isinstance(item, functions.messages.EditChatAboutRequest)]
    assert request.peer is client.destination
    assert request.about == "Source description"
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert audits[-1]["action"] == "clone-init-about"


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

    assert main([
        "clone", "init", "@source", "--commit", preview_id, "--json"
    ]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["source"] == {
        "id": 123, "title": "Alex Smith", "kind": "dialog",
    }
    assert client.destination.title == "Alex Smith"
    assert client.destination.about == "Dialog bio"
    assert any(isinstance(item, functions.users.GetFullUserRequest)
               for item in client.requests)
    assert any(isinstance(item, functions.channels.EditPhotoRequest)
               for item in client.requests)
    assert state.load(result["clone"]["id"]).source_kind == "dialog"


def test_clone_init_commit_copies_channel_avatar_and_cleans_tempfile(
    config_env, monkeypatch, capsys
):
    client = CloneInitClient()
    client.source.photo = object()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert main(
        ["clone", "init", "@source", "--commit", preview["preview_id"], "--json"]
    ) == 0

    capsys.readouterr()
    [request] = [item for item in client.requests
                 if isinstance(item, functions.channels.EditPhotoRequest)]
    assert request.channel is client.destination
    assert isinstance(request.photo, types.InputChatUploadedPhoto)
    assert isinstance(request.photo.file, types.InputFile)
    assert len(client.uploads) == 1
    assert all(not path.exists() and not path.parent.exists()
               for path in client.downloads)
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

    assert main(
        ["clone", "init", "@source", "--commit", preview["preview_id"], "--json"]
    ) == 2

    assert "avatar download failed" in capsys.readouterr().err
    saved = state.load(state.clone_id(42, 123))
    assert saved.destination_peer_id == 999
    assert not any(isinstance(item, functions.channels.EditPhotoRequest)
                   for item in client.requests)
    audits = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    assert "clone-init-avatar" not in [record["action"] for record in audits]


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
    assert main(
        ["clone", "init", "@source", "--commit", preview["preview_id"], "--json"]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["destination"]["id"] == 999
    assert [type(request) for request in client.requests] == [
        functions.channels.EditTitleRequest,
        functions.channels.GetFullChannelRequest,
    ]
    assert state.load(clone_state.clone_id).destination_peer_id == 999


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
    client.destination = channel(999, "Source channel", creator=True)
    make_session_fake(monkeypatch, client)

    preview = stored_preview()
    assert main(
        ["clone", "init", "@source", "--commit", preview["preview_id"], "--json"]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["clone"]["destination"] == {"id": 999, "title": "Source channel"}
    assert [type(request) for request in client.requests] == [
        functions.channels.GetFullChannelRequest,
    ]


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

    assert main(
        ["clone", "init", "@source", "--commit", preview["preview_id"], "--json"]
    ) == 5

    saved = state.load(state.clone_id(42, 123))
    assert saved.cooldown_deadline() is not None
    assert len(client.requests) == 1


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
                    entity=channel(channel_id, clone_state.creation_marker, creator=True)
                )

    client = MultipleMarkersClient()
    make_session_fake(monkeypatch, client)
    preview = stored_preview()

    assert main(
        ["clone", "init", "@source", "--commit", preview["preview_id"], "--json"]
    ) == 2

    assert "multiple channels" in capsys.readouterr().err
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_clone_init_commit_readonly_blocks_before_config_session_and_preview_use(
    monkeypatch,
):
    from tgcli import cli

    preview = stored_preview()
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(
        [
            "--readonly",
            "clone",
            "init",
            "@source",
            "--commit",
            preview["preview_id"],
        ]
    ) == 2

    assert safety.consume_preview(preview["preview_id"])["kind"] == "clone-init"
