"""Named session roles (ADR-0062)."""

from __future__ import annotations

import fcntl
from pathlib import Path

import pytest

from tgcli import session
from tgcli.config import Account, validate_role_name
from tgcli.errors import ConfigError

ACCOUNT = Account(alias="work", api_id=1, api_hash="h", session="Work")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    "role",
    ["", "a.b", "a/b", "../x", "a b", "١", "role!", "PRIMARY", "Primary", "primary"],
)
def test_validate_role_name_rejects_invalid_and_reserved(role):
    with pytest.raises(ConfigError):
        validate_role_name(role)


@pytest.mark.parametrize("role", ["job", "clone", "a", "A_1-z", "x" * 64])
def test_validate_role_name_accepts_alias_grade_names(role):
    assert validate_role_name(role) == role


def test_session_path_primary_unchanged(state):
    assert session.session_path(ACCOUNT) == state / "sessions" / "Work.session"


def test_session_path_with_role(state):
    assert (
        session.session_path(ACCOUNT, role="job")
        == state / "sessions" / "Work@job.session"
    )


def test_session_path_role_casefold_reuses_existing_file(state):
    sessions = state / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "Work@Job.session").write_bytes(b"x")
    assert session.session_path(ACCOUNT, role="job").name == "Work@Job.session"
    assert session.session_path(ACCOUNT, role="JOB").name == "Work@Job.session"


class FakeTelethonClient:
    def __init__(self, authorized=True):
        self.authorized = authorized
        self.connected = False

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def disconnect(self):
        self.connected = False

    async def _call(self, sender, request, *args, **kwargs):
        """Present because the real client has it — the governor wraps it."""
        return None


async def test_client_role_missing_is_config_error_with_remediation(state, monkeypatch):
    monkeypatch.setattr(
        session,
        "_make_client",
        lambda path, account, *, mutation_safe=False: FakeTelethonClient(),
    )
    with pytest.raises(ConfigError, match=r"run: tg accounts login work --role job"):
        async with session.client(ACCOUNT, role="job"):
            pass


async def test_client_role_unauthorized_file_is_config_error(state, monkeypatch):
    path = session.session_path(ACCOUNT, role="job")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    monkeypatch.setattr(
        session,
        "_make_client",
        lambda path, account, *, mutation_safe=False: FakeTelethonClient(
            authorized=False
        ),
    )
    with pytest.raises(ConfigError, match=r"--role job"):
        async with session.client(ACCOUNT, role="job"):
            pass


async def test_client_role_busy_names_alias_at_role(state, monkeypatch):
    path = session.session_path(ACCOUNT, role="job")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    lock = path.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ConfigError, match=r"session 'Work@job' is busy"):
            async with session.client(ACCOUNT, role="job"):
                pass
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


async def test_client_primary_path_byte_identical_when_role_none(state, monkeypatch):
    """Regression pin: role=None must keep today's path and busy wording."""
    seen: list[Path] = []

    def capture(path, account, *, mutation_safe=False):
        seen.append(path)
        return FakeTelethonClient()

    monkeypatch.setattr(session, "_make_client", capture)
    async with session.client(ACCOUNT):
        pass
    assert seen == [state / "sessions" / "Work.session"]


async def test_client_primary_still_busy_with_classic_message(state, monkeypatch):
    path = session.session_path(ACCOUNT)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    lock = path.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ConfigError, match=r"session 'Work' is busy"):
            async with session.client(ACCOUNT):
                pass
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def test_list_role_sessions(state):
    sessions = state / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "Work.session").write_bytes(b"p")
    (sessions / "Work@job.session").write_bytes(b"j")
    (sessions / "Work@clone.session").write_bytes(b"c")
    (sessions / "Other@job.session").write_bytes(b"o")
    names = session.list_roles(ACCOUNT)
    assert names == ["clone", "job"]


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "hash-main"
session = "main"

[accounts.work]
api_id = 67890
api_hash = "hash-work"
session = "Work"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(SAMPLE)
    state = tmp_path / "state"
    (state / "sessions").mkdir(parents=True)
    monkeypatch.setenv("TGCLI_CONFIG", str(config_path))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    return {"config": config_path, "state": state}


def test_show_lists_roles(config_env):
    from tgcli.cli import main
    from tgcli.commands import accounts as accounts_cmd
    from tgcli.config import load_config

    sessions = config_env["state"] / "sessions"
    (sessions / "Work.session").write_bytes(b"p")
    (sessions / "Work@job.session").write_bytes(b"j")
    data = accounts_cmd.show_account(load_config(), "work")
    assert data["roles"] == [
        {
            "name": "job",
            "session": str(sessions / "Work@job.session"),
            "exists": True,
            "locked": False,
            "authorized": None,
        }
    ]
    assert main(["accounts", "show", "work", "--json"]) == 0


def test_remove_role_deletes_only_role_files(config_env, capsys):
    import json

    from tgcli.cli import main
    from tgcli.config import load_config

    sessions = config_env["state"] / "sessions"
    primary = sessions / "Work.session"
    role = sessions / "Work@job.session"
    bak = Path(str(role) + ".bak")
    primary.write_bytes(b"p")
    role.write_bytes(b"j")
    bak.write_bytes(b"b")

    assert main(["accounts", "remove", "work", "--role", "job", "--json"]) == 2
    assert role.exists()
    capsys.readouterr()

    assert (
        main(["accounts", "remove", "work", "--role", "job", "--confirm", "--json"])
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "alias": "work",
        "role": "job",
        "config": "unchanged",
        "session": "deleted",
        "backup": "deleted",
    }
    assert not role.exists()
    assert not bak.exists()
    assert primary.exists()
    assert "work" in load_config().accounts


def test_remove_role_unknown_exits_4(config_env, capsys):
    import json

    from tgcli.cli import main

    (config_env["state"] / "sessions" / "Work.session").write_bytes(b"p")
    code = main(["accounts", "remove", "work", "--role", "job", "--confirm", "--json"])
    assert code == 4
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "NOT_FOUND"


def test_remove_role_rejects_keep_session(config_env, capsys):
    import json

    from tgcli.cli import main

    (config_env["state"] / "sessions" / "Work@job.session").write_bytes(b"j")
    code = main(
        [
            "accounts",
            "remove",
            "work",
            "--role",
            "job",
            "--confirm",
            "--keep-session",
            "--json",
        ]
    )
    assert code == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "BLOCKED"


def test_doctor_reports_roles_offline(config_env, capsys):
    import json

    from tgcli.cli import main

    sessions = config_env["state"] / "sessions"
    (sessions / "Work.session").write_bytes(b"p")
    (sessions / "Work@job.session").write_bytes(b"j")
    assert main(["doctor", "--account", "work", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["accounts"][0]["roles"][0]["name"] == "job"
    assert data["accounts"][0]["roles"][0]["checks"]["session_file"] is True
    assert data["accounts"][0]["roles"][0]["checks"]["authorized"] is None


def test_session_role_unknown_exits_3_before_network(config_env, monkeypatch, capsys):
    import json

    from tgcli.cli import main

    (config_env["state"] / "sessions" / "Work.session").write_bytes(b"p")

    def boom(*_a, **_k):
        raise AssertionError("must not open a Telegram client for a missing role")

    monkeypatch.setattr("tgcli.session._make_client", boom)
    code = main(["--session-role", "job", "--account", "work", "dialogs", "--json"])
    assert code == 3
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "CONFIG"
    assert "accounts login work --role job" in err["error"]["message"]


def test_session_role_accepted_on_api_and_mutation_flags(config_env):
    from tgcli.parser import build_parser

    parser = build_parser()
    read_args = parser.parse_args(["--session-role", "job", "dialogs", "--limit", "1"])
    assert read_args.session_role == "job"
    mutate_args = parser.parse_args(
        ["--session-role", "job", "send", "--preview", "@chat", "hi"]
    )
    assert mutate_args.session_role == "job"
    api_args = parser.parse_args(
        [
            "--session-role",
            "job",
            "api",
            "users.getFullUser",
            "--params",
            "{}",
        ]
    )
    assert api_args.session_role == "job"


def test_audit_records_role_on_mutation(config_env, monkeypatch):
    import json

    from tgcli import safety
    from tgcli.safety import audit_path

    token = safety.set_audit_role("job")
    try:
        safety.append_audit("send", "work", {"preview_id": "p_x"})
    finally:
        safety.reset_audit_role(token)
    record = json.loads(audit_path().read_text().splitlines()[-1])
    assert record["role"] == "job"
    assert record["action"] == "send"


def test_journal_records_role(config_env):
    import json

    from tgcli import invocations
    from tgcli.session import state_dir

    invocations.log_invocation(
        command="dialogs",
        account="work",
        role="job",
        exit_code=0,
        duration_ms=1,
    )
    line = json.loads((state_dir() / "invocations.jsonl").read_text().splitlines()[-1])
    assert line["role"] == "job"


def test_login_continue_rejects_role_flag(config_env, capsys):
    import json

    from tgcli.cli import main

    code = main(
        [
            "accounts",
            "login",
            "--continue",
            "l_deadbeefdeadbeefdeadbeefdead",
            "--role",
            "job",
            "--json",
        ]
    )
    assert code == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "BLOCKED"


def test_audit_records_role_on_send_commit_through_real_cli_path(
    config_env, monkeypatch
):
    """Regression for the confirmed PR #93 review defect: `set_audit_role` was
    only active inside `dispatch.run_network`'s client window, but the
    cli-level `_audit_before`/`_audit_after` mutation rows (cli.py) run
    outside that window, so `send --commit --session-role ROLE` audit rows
    never carried `"role"` (CONTRACT.md §9 / ADR-0062). This exercises the
    real `cli.main()` path, not the context var set by hand."""
    import json
    from types import SimpleNamespace

    from telethon.tl import types as tl_types

    from tests.conftest import make_session_fake
    from tgcli import safety
    from tgcli.cli import main

    class SendClient:
        async def get_input_entity(self, chat):
            return f"input:{chat}"

        async def __call__(self, request):
            return SimpleNamespace(
                updates=[tl_types.UpdateMessageID(id=42, random_id=request.random_id)]
            )

    preview = safety.create_preview(
        {
            "kind": "send",
            "chat": "@alice",
            "text": "hello",
            "file": None,
            "file_size": None,
            "file_sha256": None,
            "reply_to": None,
            "topic": None,
            "silent": False,
            "random_id": 900,
            "to": {"id": 7, "name": "Alice"},
        }
    )
    make_session_fake(monkeypatch, SendClient())

    assert (
        main(
            [
                "--session-role",
                "job",
                "send",
                "--commit",
                preview["preview_id"],
                "--json",
            ]
        )
        == 0
    )

    lines = [json.loads(line) for line in safety.audit_path().read_text().splitlines()]
    mutation_rows = [row for row in lines if row["action"] in ("send", "send-result")]
    assert len(mutation_rows) == 2
    assert all(row.get("role") == "job" for row in mutation_rows)


def test_audit_records_role_on_api_write_through_real_cli_path(config_env, monkeypatch):
    """Mirror-fix check (AGENTS.md): the review explicitly named `tg api`
    writes as also affected. `dispatch.run_network` is stubbed out entirely
    here so the assertion cannot pass by relying on dispatch's own
    (too-narrow) role window — it must come from the shared cli-level seam."""
    import json

    from tgcli import cli, safety

    async def fake_run_network(args, account):
        return {"method": args.method, "result": {}}, []

    monkeypatch.setattr(cli, "_run_network", fake_run_network)

    assert (
        cli.main(
            [
                "--session-role",
                "job",
                "api",
                "channels.editAdmin",
                "--params",
                '{"channel": "@team", "user_id": "@alice", "rank": "mod"}',
                "--write",
                "--confirm",
                "channels.editAdmin",
                "--json",
            ]
        )
        == 0
    )

    [row] = [
        json.loads(line)
        for line in safety.audit_path().read_text().splitlines()
        if json.loads(line)["action"] == "api"
    ]
    assert row.get("role") == "job"
