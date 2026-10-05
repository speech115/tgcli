"""Public CLI boundaries for ADR-0087's local jobs slice."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from tgcli import desktop
from tgcli.archive import store as archive_store
from tgcli.cli import main
from tgcli.commands import archive as archive_cmd
from tgcli.errors import PolicyError
from tgcli.jobs import store as jobs_store

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
session = "main"
"""


@pytest.fixture
def jobs_env(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    state = tmp_path / "state"
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    return state


def _init_archive() -> None:
    directory = archive_store.ensure_account_dir(
        archive_store.default_archive_root(), "main"
    )
    conn = archive_store.connect(archive_store.db_path_for(directory))
    try:
        archive_store.ensure_meta(conn, account_user_id=42, account_alias="main")
    finally:
        conn.close()


def _add_args(*extra: str) -> list[str]:
    return [
        "jobs",
        "add",
        "archive-transcribe",
        "--key",
        "nightly",
        *extra,
        "--json",
    ]


def test_jobs_requires_subcommand_and_add_kind(jobs_env, capsys):
    assert main(["jobs", "--json"]) == 1
    assert "required" in capsys.readouterr().err.lower()
    assert main(["jobs", "add", "--json"]) == 1
    assert "required" in capsys.readouterr().err.lower()


def test_add_list_show_idempotency_and_replace(jobs_env, capsys):
    assert main(_add_args("--max-attempts", "4", "--priority", "high")) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["created"] is True
    assert first["noop"] is False
    expected = {
        "key": "nightly",
        "generation": 1,
        "kind": "archive-transcribe",
        "lane": "local",
        "spec": {"max_attempts": 4},
        "priority": "high",
        "state": "queued",
    }
    assert {key: first["job"][key] for key in expected} == expected

    assert main(_add_args("--max-attempts", "4", "--priority", "high")) == 0
    duplicate = json.loads(capsys.readouterr().out)
    assert duplicate["created"] is False
    assert duplicate["noop"] is True
    assert duplicate["job"]["generation"] == 1

    assert main(["jobs", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [(row["key"], row["generation"]) for row in listed["jobs"]] == [
        ("nightly", 1)
    ]

    assert main(["jobs", "show", "nightly", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["job"]["spec"] == {"max_attempts": 4}
    assert shown["events"][-1]["type"] == "created"

    assert main(_add_args("--max-attempts", "5")) == 2
    assert "--replace" in capsys.readouterr().err
    assert main(_add_args("--max-attempts", "5", "--replace")) == 0
    replaced = json.loads(capsys.readouterr().out)
    assert replaced["job"]["generation"] == 2
    assert replaced["job"]["spec"] == {"max_attempts": 5}


def test_add_cancel_and_run_obey_local_mutation_gates(jobs_env, monkeypatch, capsys):
    assert main(["--readonly", *_add_args()]) == 2
    assert "readonly" in capsys.readouterr().err.lower()

    assert main(_add_args()) == 0
    capsys.readouterr()
    assert main(["--readonly", "jobs", "cancel", "nightly", "--json"]) == 2
    assert "readonly" in capsys.readouterr().err.lower()

    assert main(["jobs", "cancel", "nightly", "--json"]) == 0
    cancelled = json.loads(capsys.readouterr().out)
    assert cancelled["job"]["state"] == "cancelled"
    assert cancelled["cancel_requested"] is False

    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    assert main(["--max-runtime", "1", "jobs", "run", "--lane", "local", "--json"]) == 0
    run = json.loads(capsys.readouterr().out)
    assert run["selected"] == 0
    assert run["stop_reason"] == "idle"


def test_local_runner_requires_bounded_runtime_and_rejects_session_role(
    jobs_env, capsys
):
    assert main(["jobs", "run", "--lane", "local", "--json"]) == 2
    assert "--max-runtime" in capsys.readouterr().err
    assert (
        main(
            [
                "--max-runtime",
                "3001",
                "jobs",
                "run",
                "--lane",
                "local",
                "--json",
            ]
        )
        == 2
    )
    assert "3000" in capsys.readouterr().err
    assert (
        main(
            [
                "--session-role",
                "job",
                "--max-runtime",
                "1",
                "jobs",
                "run",
                "--lane",
                "local",
                "--json",
            ]
        )
        == 2
    )
    assert "session role" in capsys.readouterr().err.lower()


def test_local_runner_rejects_non_finite_runtime(jobs_env, capsys):
    assert (
        main(
            [
                "jobs",
                "run",
                "--lane",
                "local",
                "--max-runtime",
                "nan",
                "--json",
            ]
        )
        == 2
    )
    assert "finite" in capsys.readouterr().err.lower()


def test_local_transcription_job_runs_offline_to_completion(
    jobs_env, monkeypatch, capsys
):
    from tgcli import session

    _init_archive()
    assert main(_add_args()) == 0
    capsys.readouterr()

    @asynccontextmanager
    async def boom(*_args, **_kwargs):
        raise AssertionError("local jobs lane must not open Telegram")
        yield  # pragma: no cover

    monkeypatch.setattr(session, "client", boom)
    assert main(["--max-runtime", "1", "jobs", "run", "--lane", "local", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    expected = {
        "lane": "local",
        "selected": 1,
        "completed": 1,
        "queued": 0,
        "failed": 0,
        "cancelled": 0,
        "stop_reason": "idle",
    }
    assert {key: data[key] for key in expected} == expected
    assert data["outcomes"][0]["key"] == "nightly"
    assert data["outcomes"][0]["result"]["remaining"] is False

    assert main(_add_args()) == 0
    next_generation = json.loads(capsys.readouterr().out)
    assert next_generation["created"] is True
    assert next_generation["job"]["generation"] == 2

    journal = [
        json.loads(line)
        for line in (jobs_env / "invocations.jsonl").read_text().splitlines()
    ]
    assert {
        key: journal[-2][key]
        for key in (
            "command",
            "lane",
            "selected",
            "completed",
            "deferred",
            "failed",
            "cancelled",
        )
    } == {
        "command": "jobs",
        "lane": "local",
        "selected": 1,
        "completed": 1,
        "deferred": 0,
        "failed": 0,
        "cancelled": 0,
    }
    assert "key" not in journal[-2]
    assert "outcomes" not in journal[-2]


def test_rearm_completed_local_job_and_allow_no_send(jobs_env, monkeypatch, capsys):
    _init_archive()
    assert main(_add_args()) == 0
    capsys.readouterr()
    assert main(["--max-runtime", "1", "jobs", "run", "--lane", "local"]) == 0
    capsys.readouterr()

    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    assert (
        main(
            [
                "--max-runtime",
                "1",
                "jobs",
                "run",
                "--rearm",
                "nightly",
                "--json",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert data["lane"] == "local"
    assert data["completed"] == 1
    assert data["outcomes"][0]["generation"] == 2


def test_rearm_recovers_a_stale_running_row_and_runs_it(jobs_env, capsys):
    """A process killed mid-quantum leaves `running` behind; the next timer
    wake must finish the work instead of refusing every hour."""
    _init_archive()
    assert main(_add_args()) == 0
    capsys.readouterr()
    conn = jobs_store.connect("main")
    try:
        assert jobs_store.claim_next(conn, "local")["state"] == "running"
    finally:
        conn.close()

    argv = ["--max-runtime", "1", "jobs", "run", "--rearm", "nightly", "--json"]
    assert main(argv) == 0
    data = json.loads(capsys.readouterr().out)
    assert (data["completed"], data["outcomes"][0]["generation"]) == (1, 1)


def test_rearm_does_not_mutate_when_the_lane_is_already_running(jobs_env, capsys):
    _init_archive()
    assert main(_add_args()) == 0
    capsys.readouterr()
    assert main(["--max-runtime", "1", "jobs", "run", "--lane", "local"]) == 0
    capsys.readouterr()

    with jobs_store.lane_lock("main", "local"):
        assert (
            main(
                [
                    "--max-runtime",
                    "1",
                    "jobs",
                    "run",
                    "--rearm",
                    "nightly",
                    "--json",
                ]
            )
            == 2
        )
    assert "already running" in capsys.readouterr().err

    assert main(["jobs", "show", "nightly", "--json"]) == 0
    job = json.loads(capsys.readouterr().out)["job"]
    assert (job["generation"], job["state"]) == (1, "completed")


def test_readonly_jobs_list_does_not_repair_registry_permissions(jobs_env, capsys):
    assert main(_add_args()) == 0
    capsys.readouterr()
    directory = jobs_store.path_for("main").parent
    database = jobs_store.path_for("main")
    directory.chmod(0o755)
    database.chmod(0o644)

    assert main(["--readonly", "jobs", "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["jobs"][0]["key"] == "nightly"
    assert directory.stat().st_mode & 0o777 == 0o755
    assert database.stat().st_mode & 0o777 == 0o644


def test_rearm_cancelled_job_is_not_resurrected(jobs_env, capsys):
    assert main(_add_args()) == 0
    capsys.readouterr()
    assert main(["jobs", "cancel", "nightly"]) == 0
    capsys.readouterr()
    argv = ["--max-runtime", "1", "jobs", "run", "--rearm", "nightly", "--json"]
    assert main(argv) == 0
    capsys.readouterr()

    assert main(["jobs", "show", "nightly", "--json"]) == 0
    job = json.loads(capsys.readouterr().out)["job"]
    assert (job["generation"], job["state"]) == (1, "cancelled")


def test_telegram_rearm_obeys_no_send_before_session(jobs_env, monkeypatch, capsys):
    from tgcli import session

    assert main(["jobs", "add", "archive-sync", "--key", "sync"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("TGCLI_CONFIG", str(jobs_env / "missing.toml"))
    monkeypatch.setenv("TGCLI_NO_SEND", "1")

    @asynccontextmanager
    async def boom(*_args, **_kwargs):
        raise AssertionError("rearm gate must run before session open")
        yield  # pragma: no cover

    monkeypatch.setattr(session, "client", boom)
    assert (
        main(
            [
                "--session-role",
                "job",
                "--max-runtime",
                "1",
                "jobs",
                "run",
                "--rearm",
                "sync",
                "--json",
            ]
        )
        == 2
    )
    assert "no_send" in capsys.readouterr().err.lower()


def test_failed_job_notifies_once_and_notification_failure_is_best_effort(
    jobs_env, monkeypatch, capsys
):
    _init_archive()
    assert main(_add_args()) == 0
    capsys.readouterr()
    notifications = []

    def blocked(*_args, **_kwargs):
        raise PolicyError("sensitive target detail")

    def notify(title, message):
        notifications.append((title, message))
        raise RuntimeError("notification unavailable")

    monkeypatch.setattr(archive_cmd, "transcribe", blocked)
    monkeypatch.setattr(desktop, "notify", notify)
    argv = ["--max-runtime", "1", "jobs", "run", "--lane", "local", "--json"]
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["failed"] == 1
    assert notifications == [("tgcli job failed", "nightly; run: tg jobs show nightly")]
    assert "sensitive" not in notifications[0][1]

    assert main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["selected"] == 0
    assert len(notifications) == 1


def test_archive_refresh_command_is_removed(jobs_env, capsys):
    assert main(["archive", "refresh", "--json"]) == 1
    assert "invalid choice" in capsys.readouterr().err.lower()


def test_local_runner_uses_one_item_quanta_until_remaining_is_false(
    jobs_env, monkeypatch, capsys
):
    _init_archive()
    assert main(_add_args("--max-attempts", "4")) == 0
    capsys.readouterr()
    calls: list[tuple[int, int]] = []

    def fake_transcribe(alias, *, limit, max_attempts, config):
        assert alias == "main"
        calls.append((limit, max_attempts))
        return {
            "account": {"alias": alias},
            "remaining": len(calls) == 1,
            "attempted": 1,
            "transcribed": 1,
        }

    monkeypatch.setattr(archive_cmd, "transcribe", fake_transcribe)
    assert main(["--max-runtime", "1", "jobs", "run", "--lane", "local", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert calls == [(1, 4), (1, 4)]
    assert data["selected"] == 2
    assert data["completed"] == 1
    assert data["outcomes"][-1]["state"] == "completed"


def test_store_stats_reports_jobs_registry(jobs_env, capsys):
    assert main(_add_args()) == 0
    capsys.readouterr()
    assert main(["store", "stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["jobs"]["bytes"] > 0
    assert data["jobs"]["db"]["count"] == 1
    assert data["jobs"]["states"] == {"queued": 1}


@pytest.mark.parametrize(
    ("argv", "kind", "lane", "spec"),
    [
        (
            [
                "archive-backfill",
                "--key",
                "backfill",
                "@one",
                "@two",
                "--limit",
                "25",
            ],
            "archive-backfill",
            "telegram",
            {"chats": ["@one", "@two"], "limit": 25, "private": False},
        ),
        (
            ["archive-backfill", "--key", "private", "--private"],
            "archive-backfill",
            "telegram",
            {"chats": [], "limit": 100, "private": True},
        ),
        (
            [
                "archive-sync",
                "--key",
                "sync",
                "--max-events",
                "40",
                "--max-dialogs",
                "3",
                "--max-media",
                "2",
            ],
            "archive-sync",
            "telegram",
            {"max_dialogs": 3, "max_events": 40, "max_media": 2},
        ),
        (
            ["clone-sync", "--key", "clone", "-100123"],
            "clone-sync",
            "telegram",
            {"source": "-100123"},
        ),
    ],
)
def test_add_typed_telegram_jobs(jobs_env, capsys, argv, kind, lane, spec):
    assert main(["jobs", "add", *argv, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["job"]["kind"] == kind
    assert data["job"]["lane"] == lane
    assert data["job"]["spec"] == spec


def test_archive_backfill_job_validates_target_mode(jobs_env, capsys):
    assert main(["jobs", "add", "archive-backfill", "--key", "x", "--json"]) == 2
    assert "chat" in capsys.readouterr().err.lower()
    assert (
        main(
            [
                "jobs",
                "add",
                "archive-backfill",
                "--key",
                "x",
                "@one",
                "--private",
                "--json",
            ]
        )
        == 2
    )
    assert "private" in capsys.readouterr().err.lower()


def test_telegram_runner_requires_role_and_all_mutation_gates(
    tmp_path, monkeypatch, capsys
):
    missing = tmp_path / "missing.toml"
    monkeypatch.setenv("TGCLI_CONFIG", str(missing))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    base = ["jobs", "run", "--lane", "telegram", "--max-runtime", "1", "--json"]

    assert main(base) == 2
    assert "session role" in capsys.readouterr().err.lower()
    assert main(["--readonly", "--session-role", "job", *base]) == 2
    assert "readonly" in capsys.readouterr().err.lower()
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    assert main(["--session-role", "job", *base]) == 2
    assert "no_send" in capsys.readouterr().err.lower()
    assert not (tmp_path / "state" / "jobs").exists()


def test_telegram_runner_opens_exact_named_mutation_safe_session(
    jobs_env, monkeypatch, capsys
):
    from tgcli import session
    from tgcli.commands import jobs as jobs_cmd

    assert main(["jobs", "add", "archive-sync", "--key", "sync", "--json"]) == 0
    capsys.readouterr()
    opened = {}
    client = object()

    @asynccontextmanager
    async def fake_session(account, *, mutation_safe=False, role=None, govern=True):
        opened.update(
            alias=account.alias,
            mutation_safe=mutation_safe,
            role=role,
            govern=govern,
        )
        yield client

    async def fake_run(tg, alias, *, max_runtime, config):
        assert tg is client
        assert alias == "main"
        assert max_runtime == 1
        return {
            "account": {"alias": alias, "user_id": 42},
            "lane": "telegram",
            "selected": 0,
            "completed": 0,
            "queued": 0,
            "failed": 0,
            "cancelled": 0,
            "recovered": {"queued": 0, "cancelled": 0},
            "outcomes": [],
            "stop_reason": "idle",
        }

    monkeypatch.setattr(session, "client", fake_session)
    monkeypatch.setattr(jobs_cmd, "run_telegram", fake_run)
    assert (
        main(
            [
                "jobs",
                "run",
                "--lane",
                "telegram",
                "--session-role",
                "job",
                "--max-runtime",
                "1",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["lane"] == "telegram"
    assert opened == {
        "alias": "main",
        "mutation_safe": True,
        "role": "job",
        "govern": True,
    }
