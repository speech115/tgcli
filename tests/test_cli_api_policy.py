import pytest

from telethon.tl.tlobject import TLRequest

from tgcli.cli import main
from tgcli.commands import api as api_cmd


REVIEWED_READ_METHODS = [
    # channels (7)
    "channels.getAdminLog",
    "channels.getAdminedPublicChannels",
    "channels.getChannels",
    "channels.getFullChannel",
    "channels.getMessages",
    "channels.getParticipant",
    "channels.getParticipants",
    # contacts (4)
    "contacts.getContacts",
    "contacts.resolvePhone",
    "contacts.resolveUsername",
    "contacts.search",
    # messages (18)
    "messages.getCommonChats",
    "messages.getDialogs",
    "messages.getDiscussionMessage",
    "messages.getForumTopics",
    "messages.getFullChat",
    "messages.getHistory",
    "messages.getMessageReactionsList",
    "messages.getMessages",
    "messages.getMessagesReactions",
    "messages.getPeerDialogs",
    "messages.getReplies",
    "messages.getSavedDialogs",
    "messages.getSavedHistory",
    "messages.getSearchCounters",
    "messages.getUnreadMentions",
    "messages.getUnreadReactions",
    "messages.search",
    "messages.searchGlobal",
    # photos (1)
    "photos.getUserPhotos",
    # stats (4)
    "stats.getBroadcastStats",
    "stats.getMegagroupStats",
    "stats.getMessagePublicForwards",
    "stats.getMessageStats",
    # stories (4)
    "stories.getStoriesArchive",
    "stories.getStoriesByID",
    "stories.getPeerStories",
    "stories.getStoryViewsList",
    # users (2)
    "users.getFullUser",
    "users.getUsers",
]


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


def test_read_api_method_reaches_network_dispatcher(config_env, monkeypatch):
    from tgcli import cli

    called = []

    async def fake_run_network(args, account):
        called.append((args.method, args.params, account.alias))
        return {"method": args.method, "result": {}}, []

    monkeypatch.setattr(cli, "_run_network", fake_run_network)

    assert (
        main(["api", "users.getFullUser", "--params", '{"id": "@self"}', "--json"]) == 0
    )
    assert called == [("users.getFullUser", '{"id": "@self"}', "main")]


def test_api_write_readonly_flag_is_blocked_before_network(
    config_env, monkeypatch, capsys
):
    from tgcli import cli

    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert (
        main(["--readonly", "api", "messages.sendMessage", "--params", "{}", "--write"])
        == 2
    )
    assert "mutation blocked" in capsys.readouterr().err


def test_allowed_api_write_reaches_dispatcher_and_is_audited(config_env, monkeypatch):
    from tgcli import cli, safety

    calls = []

    async def fake_run_network(args, account):
        calls.append((args.method, args.params, account.alias))
        return {"method": args.method, "result": {}}, []

    monkeypatch.setattr(cli, "_run_network", fake_run_network)

    assert (
        main(["api", "messages.sendMessage", "--params", "{}", "--write", "--json"])
        == 0
    )
    assert calls == [("messages.sendMessage", "{}", "main")]
    assert "messages.sendMessage" in safety.audit_path().read_text()


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (
            [
                "api",
                "auth.logOut",
                "--params",
                "{}",
                "--write",
                "--confirm",
                "auth.logOut",
            ],
            "permanently denied",
        ),
        (
            ["api", "messages.deleteMessages", "--params", "{}", "--write"],
            "requires exact --confirm",
        ),
        (
            [
                "api",
                "messages.deleteMessages",
                "--params",
                "{}",
                "--write",
                "--confirm",
                "messages.deleteHistory",
            ],
            "requires exact --confirm",
        ),
    ],
)
def test_denied_or_unconfirmed_api_write_stops_before_config_or_session(
    argv, message, monkeypatch, capsys
):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(argv) == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("method", ["auth.LogOut", "account.DeleteAccount"])
def test_case_variant_denylisted_api_write_is_blocked_before_network(
    method, monkeypatch, capsys
):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )
    monkeypatch.setattr(
        cli, "_run_network", lambda args, account: pytest.fail("network dispatched")
    )

    assert main(["api", method, "--params", "{}", "--write"]) == 2
    assert "permanently denied" in capsys.readouterr().err


def test_case_variant_confirm_method_still_requires_confirm(monkeypatch, capsys):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )
    monkeypatch.setattr(
        cli, "_run_network", lambda args, account: pytest.fail("network dispatched")
    )

    assert main(["api", "messages.DeleteMessages", "--params", "{}", "--write"]) == 2
    assert "requires exact --confirm" in capsys.readouterr().err


def test_case_variant_confirm_matches_canonical_and_dispatches(config_env, monkeypatch):
    from tgcli import cli

    calls = []

    async def fake_run_network(args, account):
        calls.append((args.method, args.params, account.alias))
        return {"method": args.method, "result": {}}, []

    monkeypatch.setattr(cli, "_run_network", fake_run_network)

    assert (
        main(
            [
                "api",
                "messages.DeleteMessages",
                "--params",
                "{}",
                "--write",
                "--confirm",
                "messages.deleteMessages",
                "--json",
            ]
        )
        == 0
    )
    assert calls == [("messages.deleteMessages", "{}", "main")]


@pytest.mark.parametrize(
    "environment", [{"TGCLI_READONLY": "1"}, {"TGCLI_NO_SEND": "1"}]
)
def test_api_write_kill_switch_stops_before_config_or_session(
    environment, monkeypatch, capsys
):
    from tgcli import cli

    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["api", "messages.sendMessage", "--params", "{}", "--write"]) == 2
    assert "mutation blocked" in capsys.readouterr().err


def test_unknown_api_write_stops_before_config_or_session(monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["api", "messages.noSuchMethod", "--params", "{}", "--write"]) == 4


def test_allowed_write_api_method_without_params_remains_parser_error(
    config_env, capsys
):
    assert main(["api", "users.getFullUser", "--write"]) == 1
    assert "the following arguments are required: --params" in capsys.readouterr().err


def test_read_api_method_without_params_remains_parser_error(config_env, capsys):
    assert main(["api", "users.getFullUser"]) == 1
    assert "the following arguments are required: --params" in capsys.readouterr().err


def test_non_read_api_method_is_blocked_before_network(config_env, monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )

    assert main(["api", "messages.sendMessage", "--params", "{}"]) == 2


@pytest.mark.parametrize("method", ["auth.checkPassword", "account.getTmpPassword"])
def test_sensitive_verb_prefixed_api_method_is_blocked_before_config_or_session(
    method, monkeypatch, capsys
):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )
    monkeypatch.setattr(
        cli, "_run_network", lambda args, account: pytest.fail("network dispatched")
    )

    assert main(["api", method, "--params", "{}"]) == 2
    assert (
        "raw API method is not allowlisted for read-only use" in capsys.readouterr().err
    )


@pytest.mark.parametrize("method", REVIEWED_READ_METHODS)
def test_reviewed_read_method_is_allowlisted_and_resolves(method):
    assert api_cmd.is_read_method(method)
    request_type = api_cmd._resolve_method(method)
    assert issubclass(request_type, TLRequest)


def test_allowlist_contains_exactly_the_reviewed_methods():
    assert api_cmd.READ_METHOD_ALLOWLIST == frozenset(REVIEWED_READ_METHODS)


@pytest.mark.parametrize(
    "method",
    [
        "messages.getMessagesViews",
        "contacts.getLocated",
        "messages.getExportedChatInvites",
        "messages.getBotCallbackAnswer",
    ],
)
def test_rejected_read_looking_api_method_is_blocked_before_config_or_session(
    method, monkeypatch, capsys
):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        cli.session, "client", lambda account: pytest.fail("session opened")
    )
    monkeypatch.setattr(
        cli, "_run_network", lambda args, account: pytest.fail("network dispatched")
    )

    assert main(["api", method, "--params", "{}"]) == 2
    assert (
        "raw API method is not allowlisted for read-only use" in capsys.readouterr().err
    )
