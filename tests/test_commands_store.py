"""Local-state inventory and cleanup (ADR-0040)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli.cli import main
from tgcli.commands import store as store_cmd


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
