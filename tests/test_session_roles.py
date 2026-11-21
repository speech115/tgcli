"""Named session roles."""

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
        """Present because the real client has it: `tg run` wraps it."""
        return None


async def test_client_role_missing_is_config_error_with_remediation(state, monkeypatch):
    monkeypatch.setattr(
        session,
        "_make_client",
        lambda path, account, *, mutation_safe=False: FakeTelethonClient(),
    )
    with pytest.raises(
        ConfigError, match=r"run: tg accounts login work --role job --phone PHONE"
    ):
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
    monkeypatch.setattr(session, "BUSY_WAIT_SECONDS", 0.0)
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


def test_remove_role_deletes_a_role_left_with_only_its_backup(config_env, capsys):
    """An interrupted promote leaves only `<account>@<role>.session.bak`, a
    live key; `--role` removal exited 4 and could not reach it."""
    import json

    from tgcli.cli import main

    bak = config_env["state"] / "sessions" / "Work@job.session.bak"
    bak.write_bytes(b"live-key")

    assert (
        main(["accounts", "remove", "work", "--role", "job", "--confirm", "--json"])
        == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "alias": "work",
        "role": "job",
        "config": "unchanged",
        "session": "absent",
        "backup": "deleted",
    }
    assert not bak.exists()


def test_remove_role_takes_a_shared_lock_file_once(config_env, capsys):
    """On a case-insensitive filesystem (macOS) `Work@job.lock` and
    `Work@JOB.lock` are one file; locking it a second time from the same
    process reported the role busy and removed nothing."""
    import json

    from tgcli.cli import main

    sessions = config_env["state"] / "sessions"
    (sessions / "Work@job.session.bak").write_bytes(b"old-key")
    (sessions / "Work@JOB.session").write_bytes(b"new-key")
    (sessions / "Work@job.lock").touch()
    if not (sessions / "Work@JOB.lock").exists():
        (sessions / "Work@JOB.lock").symlink_to(sessions / "Work@job.lock")

    assert (
        main(["accounts", "remove", "work", "--role", "job", "--confirm", "--json"])
        == 0
    )

    assert json.loads(capsys.readouterr().out)["session"] == "deleted"
    assert not (sessions / "Work@JOB.session").exists()
    assert not (sessions / "Work@job.session.bak").exists()


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


def test_session_role_accepted_on_script_and_mutation_flags(config_env):
    from tgcli.parser import build_parser

    parser = build_parser()
    read_args = parser.parse_args(["--session-role", "job", "dialogs", "--limit", "1"])
    assert read_args.session_role == "job"
    mutate_args = parser.parse_args(
        ["--session-role", "job", "send", "--preview", "@chat", "hi"]
    )
    assert mutate_args.session_role == "job"
    run_args = parser.parse_args(["--session-role", "job", "run", "-"])
    assert run_args.session_role == "job"


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
