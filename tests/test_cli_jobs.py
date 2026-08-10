"""Public CLI boundaries for ADR-0087's local jobs slice."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest

from tgcli.archive import store as archive_store
from tgcli.cli import main
from tgcli.commands import archive as archive_cmd

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
