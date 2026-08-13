"""Local-state inventory and cleanup (ADR-0040)."""

import errno
import fcntl
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tgcli import login_state
from tgcli.cli import main
from tgcli.commands import store as store_cmd
from tgcli.errors import NotFoundError
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


def test_scan_file_inventory_is_driven_by_the_registry(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "one.bin").write_bytes(b"one")
    registry = getattr(store_cmd, "_FILE_SCAN_REGISTRY", ())
    monkeypatch.setattr(
        store_cmd,
        "_FILE_SCAN_REGISTRY",
        (*registry, (("fixtures",), "fixtures", "*.bin")),
        raising=False,
    )

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data.get("fixtures") == {"count": 1, "bytes": 3}


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
    assert result["kept"]["archive"] is True
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


def _write_unparsable_preview(root: Path, name: str, *, mtime: datetime) -> Path:
    """A preview truncated mid-write: valid name, unreadable expires_at."""
    directory = root / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text('{"payload": {"text": "half-writ')
    stamp = mtime.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_unparsable_preview_falls_back_to_mtime(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    fresh = _write_unparsable_preview(
        tmp_path, "p_torn", mtime=NOW - timedelta(minutes=1)
    )
    stale = _write_unparsable_preview(
        tmp_path, "p_stale", mtime=NOW - PREVIEW_TTL - timedelta(minutes=1)
    )

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["previews"]["live"]["count"] == 1
    assert data["previews"]["expired"]["count"] == 1

    store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert fresh.exists(), "a preview written moments ago must survive cleanup"
    assert not stale.exists()


def _write_unparsable_login(root: Path, login_id: str, *, mtime: datetime) -> Path:
    """A login attempt truncated mid-write: valid name, unreadable expires_at."""
    directory = root / "logins"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{login_id}.json"
    path.write_text('{"login_id": "l_torn", "expires_at": "half-writ')
    (directory / f"{login_id}.session").write_bytes(b"staged-key")
    stamp = mtime.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_unparsable_login_falls_back_to_mtime(tmp_path, monkeypatch):
    """A live attempt mid-update must survive store cleanup --confirm."""
    from tgcli.login_state import LOGIN_TTL

    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    fresh = _write_unparsable_login(
        tmp_path, "l_torn", mtime=NOW - timedelta(minutes=1)
    )
    stale = _write_unparsable_login(
        tmp_path, "l_stale", mtime=NOW - LOGIN_TTL - timedelta(minutes=1)
    )

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["logins"]["live"]["count"] == 2  # json + staged session
    assert data["logins"]["expired"]["count"] == 2

    store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert fresh.exists(), "a login attempt written moments ago must survive cleanup"
    assert (tmp_path / "logins" / "l_torn.session").exists()
    assert not stale.exists()
    assert not (tmp_path / "logins" / "l_stale.session").exists()


def _write_naive_preview(root: Path, name: str, *, mtime: datetime) -> Path:
    """A preview whose `expires_at` lost its UTC offset (old build/hand edit)."""
    directory = root / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    naive = (NOW - timedelta(minutes=1)).replace(tzinfo=None)
    path.write_text(
        json.dumps({"payload": {"text": "hi"}, "expires_at": naive.isoformat()})
    )
    stamp = mtime.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def _write_naive_login(root: Path, login_id: str, *, mtime: datetime) -> Path:
    """A login attempt whose `expires_at` lost its UTC offset."""
    directory = root / "logins"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{login_id}.json"
    naive = (NOW - timedelta(minutes=1)).replace(tzinfo=None)
    path.write_text(
        json.dumps(
            {
                "login_id": login_id,
                "alias": "tmp",
                "method": "phone",
                "expires_at": naive.isoformat(),
            }
        )
    )
    (directory / f"{login_id}.session").write_bytes(b"staged-key")
    stamp = mtime.timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_naive_expires_at_falls_back_to_mtime(tmp_path, monkeypatch):
    """A timezone-less `expires_at` classifies by mtime instead of crashing.

    Comparing it against an aware `now` raised TypeError and took the whole
    inventory down; both commands exist to help a user already in trouble.
    """
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    preview = _write_naive_preview(
        tmp_path, "p_naive", mtime=NOW - timedelta(minutes=1)
    )
    login = _write_naive_login(tmp_path, "l_naive", mtime=NOW - timedelta(minutes=1))

    data = store_cmd.stats(tmp_path, now=NOW)
    store_cmd.stats_rows(data)

    assert data["previews"]["live"]["count"] == 1
    assert data["previews"]["expired"]["count"] == 0
    assert data["logins"]["live"]["count"] == 2  # json + staged session
    assert data["logins"]["expired"]["count"] == 0

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    # Fail-closed for deletion: an untrustworthy stamp is never reaped early.
    assert result["removed"] == []
    assert preview.exists()
    assert login.exists()
    assert (tmp_path / "logins" / "l_naive.session").exists()


def test_naive_expires_at_login_age_filter_uses_mtime(tmp_path, monkeypatch):
    """`--older-than` anchors a naive attempt on mtime instead of crashing."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    stale = _write_naive_login(
        tmp_path, "l_naive", mtime=NOW - login_state.LOGIN_TTL - timedelta(hours=2)
    )

    kept = store_cmd.cleanup(
        tmp_path, older_than=timedelta(days=1), confirm=True, now=NOW
    )
    assert kept["removed"] == []
    assert stale.exists()

    reaped = store_cmd.cleanup(
        tmp_path, older_than=timedelta(hours=1), confirm=True, now=NOW
    )
    assert "l_naive.json" in reaped["removed"]
    assert not stale.exists()
    assert not (tmp_path / "logins" / "l_naive.session").exists()


def test_cleanup_reaps_attempt_left_behind_by_expired_read(tmp_path, monkeypatch):
    """`load_attempt` no longer deletes, so cleanup must still reap the dead."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW - login_state.LOGIN_TTL
    )
    login_id = record["login_id"]
    staged = login_state.staged_session_path(login_id)
    staged.write_bytes(b"staged-key")

    with pytest.raises(NotFoundError, match="expired"):
        login_state.load_attempt(login_id, now=NOW)
    assert staged.exists()

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert f"{login_id}.json" in result["removed"]
    assert f"{login_id}.session" in result["removed"]
    assert not staged.exists()


def test_cleanup_confirm_allowed_under_no_send(tmp_path, monkeypatch, capsys):
    """TGCLI_NO_SEND guards Telegram sends, not local-state housekeeping."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    _seed_inventory(tmp_path)

    assert main(["store", "cleanup", "--confirm", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "p_spent0.used" in data["removed"]
    assert not (tmp_path / "previews" / "p_spent0.used").exists()
    assert (tmp_path / "audit.jsonl").exists()


def _write_login(
    root: Path,
    login_id: str,
    *,
    expires_at: datetime,
    staged: bytes = b"staged",
) -> None:
    directory = root / "logins"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{login_id}.json").write_text(
        json.dumps(
            {
                "login_id": login_id,
                "alias": "tmp",
                "method": "phone",
                "expires_at": expires_at.isoformat(),
            }
        )
    )
    (directory / f"{login_id}.session").write_bytes(staged)


def test_stats_reports_logins_and_session_backups(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)
    _write_login(tmp_path, "l_live", expires_at=NOW + timedelta(minutes=10))
    _write_login(tmp_path, "l_dead", expires_at=NOW - timedelta(minutes=1))
    bak = tmp_path / "sessions" / "main.session.bak"
    bak.write_bytes(b"backup-bytes")

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["logins"]["live"]["count"] == 2
    assert data["logins"]["expired"]["count"] == 2
    assert data["logins"]["live"]["bytes"] > 0
    assert data["session_backups"]["count"] == 1
    assert data["session_backups"]["bytes"] == len(b"backup-bytes")


def test_cleanup_reaps_expired_logins_keeps_live_and_bak(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)
    _write_login(tmp_path, "l_live", expires_at=NOW + timedelta(minutes=10))
    _write_login(tmp_path, "l_dead", expires_at=NOW - timedelta(minutes=1))
    bak = tmp_path / "sessions" / "main.session.bak"
    bak.write_bytes(b"backup-bytes")

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert "l_dead.json" in result["removed"]
    assert "l_dead.session" in result["removed"]
    assert "l_live.json" not in result["removed"]
    assert (tmp_path / "logins" / "l_live.json").exists()
    assert (tmp_path / "logins" / "l_live.session").exists()
    assert not (tmp_path / "logins" / "l_dead.json").exists()
    assert not (tmp_path / "logins" / "l_dead.session").exists()
    assert bak.exists()
    assert (tmp_path / "sessions" / "main.session").exists()
    assert result["kept"]["session_backups"] is True


def _seed_clone_media_cache(
    root: Path, clone_id: str = "abc123", *, payload=b"media"
) -> Path:
    clones = root / "clones"
    clones.mkdir(parents=True, exist_ok=True)
    (clones / f"{clone_id}.json").write_text('{"version":2}')
    cache = clones / f"{clone_id}-media"
    cache.mkdir()
    (cache / "src-2").write_bytes(payload)
    return cache


def _age_media_cache(cache: Path, age: timedelta) -> None:
    """Backdate the cache directory and its files relative to NOW."""
    stamp = (NOW - age).timestamp()
    for entry in sorted(cache.rglob("*")) + [cache]:
        os.utime(entry, (stamp, stamp))


def test_scan_reports_clone_sqlite_and_imported_buckets(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    from tgcli.clone import state

    s = state.CloneState.new(account_user_id=1, source_peer_id=2, source_title="S")
    state.save(s)
    imported = tmp_path / "clones" / f"{s.clone_id}.json.imported"
    imported.write_text('{"version":2}')

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["clones"]["db"]["count"] == 1
    assert data["clones"]["db"]["bytes"] > 0
    assert data["clones"]["imported"] == {
        "count": 1,
        "bytes": len(b'{"version":2}'),
    }
    assert data["clones"]["wal"]["count"] >= 0
    assert data["clones"]["shm"]["count"] >= 0


def test_scan_reports_clone_media_cache_bucket(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path, payload=b"abcdefghij")

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["clone_media_cache"] == {"count": 1, "bytes": 10}
    # Existing clones aggregate still includes the cache via _dir_bytes.
    assert data["clones"]["bytes"] >= 10 + len(b'{"version":2}')
    assert cache.exists()


def test_cleanup_confirm_removes_media_cache_keeps_clone_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path)
    _age_media_cache(cache, timedelta(days=1))
    state_file = tmp_path / "clones" / "abc123.json"

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert "abc123-media" in result["removed"]
    assert not cache.exists()
    assert state_file.exists()
    assert state_file.read_text() == '{"version":2}'


def test_cleanup_keeps_media_cache_a_running_sync_is_writing(tmp_path, monkeypatch):
    """A cache touched moments ago belongs to a live `clone sync`, not to litter.

    `store cleanup --confirm` with no `--older-than` reaps every other bucket
    only after its own TTL classified the record dead; the media cache has no
    `expires_at`, so mtime is the only liveness signal it has (ADR-0052).
    """
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path)
    _age_media_cache(cache, timedelta(minutes=2))

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert "abc123-media" not in result["removed"]
    assert "abc123-media" not in result["would_remove"]
    assert cache.exists()
    assert (cache / "src-2").exists()


def test_cleanup_media_cache_liveness_follows_newest_file(tmp_path, monkeypatch):
    """A long single-file download leaves the directory mtime behind.

    Creating `src-<id>` stamps the directory once; the half-gigabyte write that
    follows only advances the file's own mtime. Anchoring on the directory
    alone would reap the cache out from under the download it exists for.
    """
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path)
    _age_media_cache(cache, timedelta(days=2))
    fresh = (NOW - timedelta(minutes=1)).timestamp()
    os.utime(cache / "src-2", (fresh, fresh))

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert "abc123-media" not in result["removed"]
    assert cache.exists()


def test_cleanup_dry_run_lists_media_cache_without_deleting(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path)
    _age_media_cache(cache, timedelta(days=1))

    result = store_cmd.cleanup(tmp_path, confirm=False, now=NOW)

    assert "abc123-media" in result["would_remove"]
    assert result["removed"] == []
    assert cache.exists()
    assert (cache / "src-2").exists()


def test_cleanup_older_than_gates_media_cache_by_mtime(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path)
    # Make the cache look recent relative to NOW.
    _age_media_cache(cache, timedelta(hours=2))

    kept = store_cmd.cleanup(
        tmp_path, confirm=True, older_than=timedelta(days=1), now=NOW
    )
    assert "abc123-media" not in kept["removed"]
    assert cache.exists()

    _age_media_cache(cache, timedelta(days=3))
    removed = store_cmd.cleanup(
        tmp_path, confirm=True, older_than=timedelta(days=1), now=NOW
    )
    assert "abc123-media" in removed["removed"]
    assert not cache.exists()


def _vanish_after_first_stat(monkeypatch, victim: Path) -> None:
    """Let `victim` pass one stat and disappear before the next one.

    The scan lists a directory and then stats each entry, so a preview or a
    login consumed by a concurrent `tg` lands exactly in that window: the
    `is_file()` probe still sees it, the stat that follows does not.
    """
    real_stat = Path.stat
    seen = {"count": 0}

    def fake_stat(self, *args, **kwargs):
        if self == victim:
            seen["count"] += 1
            if seen["count"] > 1:
                raise FileNotFoundError(
                    errno.ENOENT, "No such file or directory", str(self)
                )
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)


def test_scan_survives_a_preview_that_vanishes_mid_walk(tmp_path, monkeypatch):
    """An offline, read-only inventory must never crash on a lost race."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)
    _vanish_after_first_stat(monkeypatch, tmp_path / "previews" / "p_live1.json")

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["previews"]["live"]["count"] == 1  # the vanished one is absent
    assert data["previews"]["expired"]["count"] == 1
    assert data["previews"]["spent"]["count"] == 3


def test_scan_survives_a_login_that_vanishes_mid_walk(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    # An unparsable attempt is anchored on mtime, so the walk has to stat it.
    victim = _write_unparsable_login(tmp_path, "l_torn", mtime=NOW - timedelta(hours=1))
    _write_login(tmp_path, "l_live", expires_at=NOW + timedelta(minutes=10))
    _vanish_after_first_stat(monkeypatch, victim)

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["logins"]["live"]["count"] == 2  # only the intact attempt pair


def test_scan_survives_a_clone_cache_file_that_vanishes_mid_walk(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path)
    _vanish_after_first_stat(monkeypatch, cache / "src-2")

    data = store_cmd.scan(tmp_path, now=NOW)

    assert data["clone_media_cache"]["count"] == 1


def test_cleanup_survives_a_preview_that_vanishes_mid_walk(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)
    # An unparsable preview is anchored on mtime, so selection has to stat it.
    victim = _write_unparsable_preview(
        tmp_path, "p_torn", mtime=NOW - PREVIEW_TTL - timedelta(minutes=1)
    )
    _vanish_after_first_stat(monkeypatch, victim)

    result = store_cmd.cleanup(tmp_path, confirm=True, older_than=timedelta(0), now=NOW)

    assert "p_spent1.used" in result["removed"]


def test_cleanup_survives_a_media_cache_that_vanishes_mid_walk(tmp_path, monkeypatch):
    """A batch that lands clears its own cache — possibly mid-walk."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path)
    _age_media_cache(cache, timedelta(days=1))
    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == cache and os.path.isdir(cache):
            shutil.rmtree(cache)
        return result

    monkeypatch.setattr(Path, "stat", fake_stat)

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert "abc123-media" not in result["removed"]
    assert not cache.exists()


def _hold_lock(path: Path):
    handle = path.open("w")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def test_cleanup_keeps_expired_login_whose_lock_is_held(tmp_path, monkeypatch):
    """An in-flight authorization can outlive LOGIN_TTL without becoming litter.

    The running login holds the staged session's flock for the whole attempt
    (authclient), so cleanup probes it the same non-blocking way
    `session.lock_held` does before reaping an expired attempt.
    """
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _write_login(tmp_path, "l_phone", expires_at=NOW - timedelta(minutes=1))
    logins = tmp_path / "logins"

    handle = _hold_lock(logins / "l_phone.lock")
    try:
        held = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()

    assert held["removed"] == []
    assert (logins / "l_phone.json").exists()
    assert (logins / "l_phone.session").exists()

    free = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert "l_phone.json" in free["removed"]
    assert "l_phone.session" in free["removed"]
    assert not (logins / "l_phone.session").exists()


def test_cleanup_keeps_media_cache_while_a_session_lock_is_held(tmp_path, monkeypatch):
    """A long upload-only phase writes nothing, so mtime alone is not liveness.

    `clone sync` holds its account session lock for the whole run; while any
    session lock is held, a `*-media` cache may still belong to that run
    (ADR-0052), whatever its mtime says.
    """
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    cache = _seed_clone_media_cache(tmp_path)
    _age_media_cache(cache, timedelta(days=2))
    sessions = tmp_path / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "main.session").write_bytes(b"session-bytes")

    handle = _hold_lock(sessions / "main.lock")
    try:
        held = store_cmd.cleanup(
            tmp_path, confirm=True, older_than=timedelta(days=1), now=NOW
        )
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()

    assert "abc123-media" not in held["removed"]
    assert "abc123-media" not in held["would_remove"]
    assert (cache / "src-2").exists()

    free = store_cmd.cleanup(
        tmp_path, confirm=True, older_than=timedelta(days=1), now=NOW
    )

    assert "abc123-media" in free["removed"]
    assert not cache.exists()


def test_cleanup_probes_locks_without_creating_them(tmp_path, monkeypatch):
    """A holder creates its lock file first, so cleanup only ever reads one."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    _seed_inventory(tmp_path)  # sessions/main.session, no lock beside it
    _write_login(tmp_path, "l_dead", expires_at=NOW - timedelta(minutes=1))
    cache = _seed_clone_media_cache(tmp_path)
    _age_media_cache(cache, timedelta(days=1))

    result = store_cmd.cleanup(tmp_path, confirm=True, now=NOW)

    assert "l_dead.session" in result["removed"]
    assert "abc123-media" in result["removed"]
    assert list((tmp_path / "sessions").glob("*.lock")) == []
    assert list((tmp_path / "logins").glob("*.lock")) == []
