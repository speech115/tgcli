import json
from types import SimpleNamespace

import pytest
from telethon.tl import functions

from tests.conftest import make_session_fake
from tgcli import safety
from tgcli.cli import main
from tgcli.errors import PolicyError
from tgcli.mirror.store import MirrorStore, mirror_id


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


def channel(channel_id, title, *, creator=True):
    return SimpleNamespace(
        id=channel_id,
        title=title,
        creator=creator,
        broadcast=True,
        megagroup=False,
        username=None,
    )


class MirrorInitClient:
    def __init__(self, *, dialogs=(), fail_create=False):
        self.source = channel(123, "Source channel", creator=False)
        self.destination = None
        self.dialogs = list(dialogs)
        self.fail_create = fail_create
        self.requests = []

    async def get_me(self):
        return SimpleNamespace(id=42)

    async def get_entity(self, ref):
        if ref == "@source":
            return self.source
        if getattr(ref, "channel_id", None) and self.destination:
            return self.destination
        raise ValueError(f"unknown entity: {ref!r}")

    async def iter_dialogs(self):
        for entity in self.dialogs:
            yield SimpleNamespace(entity=entity)

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.channels.CreateChannelRequest):
            self.destination = channel(999, request.title)
            self.dialogs.append(self.destination)
            if self.fail_create:
                raise ConnectionError("accepted then disconnected")
            return SimpleNamespace(chats=[self.destination])
        if isinstance(request, functions.channels.EditTitleRequest):
            self.destination.title = request.title
            return SimpleNamespace()
        raise AssertionError(f"unexpected request: {request!r}")


def test_mirror_init_preview_makes_no_telegram_mutation(
    config_env, monkeypatch, capsys
):
    client = MirrorInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "init", "@source", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "mirror": {
            "id": mirror_id(42, 123),
            "source": {"id": 123, "title": "Source channel"},
            "destination": None,
            "status": "planned",
            "commit_required": True,
        }
    }
    assert client.requests == []
    assert not safety.audit_path().exists()


def test_mirror_init_commit_creates_once_and_restores_source_title(
    config_env, monkeypatch, capsys
):
    client = MirrorInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "init", "@source", "--commit", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["mirror"]["destination"] == {"id": 999, "title": "Source channel"}
    assert first["mirror"]["status"] == "authorized"

    creates = [r for r in client.requests if isinstance(r, functions.channels.CreateChannelRequest)]
    titles = [r for r in client.requests if isinstance(r, functions.channels.EditTitleRequest)]
    assert len(creates) == 1
    assert creates[0].broadcast is True
    assert creates[0].megagroup is False
    assert creates[0].title == f"[tgcli:{mirror_id(42, 123)[:12]}]"
    assert [request.title for request in titles] == ["Source channel"]

    assert main(["mirror", "init", "@source", "--commit", "--json"]) == 0
    json.loads(capsys.readouterr().out)
    assert len([r for r in client.requests if isinstance(r, functions.channels.CreateChannelRequest)]) == 1
    assert len([r for r in client.requests if isinstance(r, functions.channels.EditTitleRequest)]) == 1

    reopened = MirrorStore().create(42, 123, "Source channel")
    assert reopened.destination_peer_id == 999
    assert reopened.authorized is True
    assert len(safety.audit_path().read_text().splitlines()) == 2


def test_mirror_init_commit_after_preview_restores_current_source_title(
    config_env, monkeypatch, capsys
):
    client = MirrorInitClient()
    make_session_fake(monkeypatch, client)

    assert main(["mirror", "init", "@source", "--json"]) == 0
    capsys.readouterr()
    client.source.title = "Renamed source"

    assert main(["mirror", "init", "@source", "--commit", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["mirror"]["source"]["title"] == "Renamed source"
    assert result["mirror"]["destination"] == {
        "id": 999,
        "title": "Renamed source",
    }
    creates = [
        request
        for request in client.requests
        if isinstance(request, functions.channels.CreateChannelRequest)
    ]
    titles = [
        request
        for request in client.requests
        if isinstance(request, functions.channels.EditTitleRequest)
    ]
    assert [request.title for request in creates] == [
        f"[tgcli:{mirror_id(42, 123)[:12]}]"
    ]
    assert [request.title for request in titles] == ["Renamed source"]


@pytest.mark.asyncio
async def test_mirror_init_reconciles_accepted_ambiguous_create_without_duplicate(
    config_env,
):
    from tgcli.commands.mirror import commit_init

    first = MirrorInitClient(fail_create=True)
    with pytest.raises(ConnectionError, match="accepted then disconnected"):
        await commit_init(first, "@source", "main")
    marker_channel = first.destination
    assert marker_channel is not None

    retry = MirrorInitClient(dialogs=[marker_channel])
    retry.destination = marker_channel
    result = await commit_init(retry, "@source", "main")

    assert result["mirror"]["destination"] == {"id": 999, "title": "Source channel"}
    assert not any(
        isinstance(request, functions.channels.CreateChannelRequest)
        for request in retry.requests
    )


@pytest.mark.asyncio
async def test_mirror_init_refuses_multiple_marker_matches(config_env):
    from tgcli.commands.mirror import commit_init

    marker = f"[tgcli:{mirror_id(42, 123)[:12]}]"
    client = MirrorInitClient(dialogs=[channel(998, marker), channel(999, marker)])

    with pytest.raises(PolicyError, match="multiple"):
        await commit_init(client, "@source", "main")
    assert client.requests == []


@pytest.mark.parametrize(
    "flag,value",
    [("--readonly", None), ("TGCLI_READONLY", "1"), ("TGCLI_NO_SEND", "1")],
)
def test_mirror_init_commit_is_blocked_before_config_or_session(
    monkeypatch, flag, value
):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, value)
        argv = ["mirror", "init", "@source", "--commit"]
    else:
        argv = [flag, "mirror", "init", "@source", "--commit"]

    assert main(argv) == 2
    assert not safety.audit_path().exists()
