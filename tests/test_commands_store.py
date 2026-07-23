"""Local-state inventory and cleanup (ADR-0040)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.cli import main
from tgcli.commands import store as store_cmd
from tgcli.safety import PREVIEW_TTL


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _write_preview(
    root: Path,
    name: str,
    *,
    suffix: str = ".json",
    expires_at: datetime,
    body: str = "hello",
) -> Path:
    directory = root / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}{suffix}"
    path.write_text(
        json.dumps({"payload": {"text": body}, "expires_at": expires_at.isoformat()})
    )
    return path


def _seed_inventory(root: Path) -> None:
    future = NOW + timedelta(minutes=4)
    past = NOW - timedelta(minutes=1)
    _write_preview(root, "p_live1", expires_at=future)
    _write_preview(root, "p_live2", expires_at=future)
    _write_preview(root, "p_expired", expires_at=past)
    for index in range(3):
        _write_preview(root, f"p_spent{index}", suffix=".used", expires_at=past)
    _write_preview(root, "p_pending", suffix=".pending", expires_at=future)
    (root / "audit.jsonl").write_text('{"action":"send"}\n')
    sessions = root / "sessions"
    sessions.mkdir()
    (sessions / "main.session").write_bytes(b"session-bytes")
    labs = root / "labs"
    labs.mkdir()
    (labs / "old.bin").write_bytes(b"relic-bytes")


def test_scan_classifies_previews_and_reports_relics(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["previews"]["live"]["count"] == 2
    assert data["previews"]["expired"]["count"] == 1
    assert data["previews"]["spent"]["count"] == 3
    assert data["previews"]["pending"]["count"] == 1
    assert data["audit_log"]["bytes"] > 0
    assert data["relics"] == [{"name": "labs", "bytes": len(b"relic-bytes")}]


def test_store_stats_cli_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)

    assert main(["store", "stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["previews"]) == {"live", "expired", "spent", "pending"}
    assert "audit_log" in data
    assert "relics" in data


def test_cleanup_dry_run_reports_without_deleting(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)

    result = store_cmd.cleanup(tmp_path, confirm=False, now=NOW)

    assert result["confirmed"] is False
    assert result["removed"] == []
    assert set(result["would_remove"]) == {
        "p_expired.json",
        "p_spent0.used",
        "p_spent1.used",
        "p_spent2.used",
    }
    assert result["bytes"] > 0
    assert result["kept"]["audit_log"] is True
    assert result["kept"]["sessions"] is True
    assert "labs" in result["kept"]["relics"]
    assert (tmp_path / "previews" / "p_expired.json").exists()
    assert (tmp_path / "previews" / "p_spent0.used").exists()
    assert (tmp_path / "previews" / "p_live1.json").exists()
    assert (tmp_path / "previews" / "p_pending.pending").exists()
    assert (tmp_path / "audit.jsonl").exists()
    assert (tmp_path / "sessions" / "main.session").exists()
    assert (tmp_path / "labs" / "old.bin").exists()


def test_cleanup_confirm_deletes_spent_and_expired_only(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert result["confirmed"] is True
    assert set(result["removed"]) == {
        "p_expired.json",
        "p_spent0.used",
        "p_spent1.used",
        "p_spent2.used",
    }
    assert result["would_remove"] == []
    assert result["bytes"] > 0
    assert not (tmp_path / "previews" / "p_expired.json").exists()
    assert not (tmp_path / "previews" / "p_spent0.used").exists()
    assert (tmp_path / "previews" / "p_live1.json").exists()
    assert (tmp_path / "previews" / "p_pending.pending").exists()
    assert (tmp_path / "audit.jsonl").exists()
    assert (tmp_path / "sessions" / "main.session").exists()
    assert (tmp_path / "labs" / "old.bin").exists()


def test_cleanup_include_pending_only_when_far_past_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    fresh = NOW + timedelta(minutes=4)
    old = NOW - PREVIEW_TTL - timedelta(minutes=1)
    _write_preview(tmp_path, "p_fresh", suffix=".pending", expires_at=fresh)
    _write_preview(tmp_path, "p_old", suffix=".pending", expires_at=old)

    result = store_cmd.cleanup(tmp_path, confirm=True, include_pending=True, now=NOW)

    assert "p_old.pending" in result["removed"]
    assert "p_fresh.pending" not in result["removed"]
    assert (tmp_path / "previews" / "p_fresh.pending").exists()
    assert not (tmp_path / "previews" / "p_old.pending").exists()


def test_cleanup_older_than_keeps_recent_spent(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    recent = NOW - timedelta(minutes=1)
    ancient = NOW - timedelta(days=3)
    _write_preview(tmp_path, "p_recent", suffix=".used", expires_at=recent)
    _write_preview(tmp_path, "p_ancient", suffix=".used", expires_at=ancient)

    result = store_cmd.cleanup(
        tmp_path, confirm=True, older_than=timedelta(days=1), now=NOW
    )

    assert result["removed"] == ["p_ancient.used"]
    assert (tmp_path / "previews" / "p_recent.used").exists()


def test_cleanup_cli_dry_run_hint_on_stderr(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)

    assert main(["store", "cleanup", "--json"]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["confirmed"] is False
    assert data["removed"] == []
    assert "would be removed" in captured.err
    assert "--confirm" in captured.err


def test_cleanup_confirm_blocked_under_readonly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)

    assert main(["store", "cleanup", "--confirm", "--readonly", "--json"]) == 2
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "BLOCKED"
    assert (tmp_path / "previews" / "p_spent0.used").exists()


def test_cleanup_dry_run_does_not_chmod_survivors(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    future = NOW + timedelta(minutes=4)
    live = _write_preview(tmp_path, "p_live", expires_at=future)
    live.chmod(0o644)

    store_cmd.cleanup(tmp_path, confirm=False, now=NOW)

    assert live.stat().st_mode & 0o777 == 0o644


def test_cleanup_confirm_blocked_by_tgcli_readonly_env(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TGCLI_READONLY", "1")
    _seed_inventory(tmp_path)

    assert main(["store", "cleanup", "--confirm", "--json"]) == 2
    assert (tmp_path / "previews" / "p_spent0.used").exists()


def test_cleanup_rejects_invalid_older_than(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    assert main(["store", "cleanup", "--older-than", "nope", "--json"]) == 1
    assert "invalid --older-than" in capsys.readouterr().err

    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    future = NOW + timedelta(minutes=4)
    live = _write_preview(tmp_path, "p_live", expires_at=future)
    live.chmod(0o644)
    spent = _write_preview(
        tmp_path, "p_spent", suffix=".used", expires_at=NOW - timedelta(minutes=1)
    )
    spent.chmod(0o644)

    store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert not spent.exists()
    assert live.exists()
    assert live.stat().st_mode & 0o777 == 0o600


def test_scan_reports_world_readable_previews(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    future = NOW + timedelta(minutes=4)
    open_path = _write_preview(tmp_path, "p_open", expires_at=future)
    open_path.chmod(0o644)
    tight = _write_preview(tmp_path, "p_tight", expires_at=future)
    tight.chmod(0o600)

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["previews_world_readable"] == 1
