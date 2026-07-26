"""Unit tests for contacts.resolvePhone shared cooldown."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tgcli import resolve_phone
from tgcli.errors import RateLimitError


def test_enforce_resolve_phone_cooldown_blocks_then_allows(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    clock = {"t": 50.0}
    monkeypatch.setattr(resolve_phone.time, "time", lambda: clock["t"])

    resolve_phone.enforce_resolve_phone_cooldown()
    try:
        resolve_phone.enforce_resolve_phone_cooldown()
        raise AssertionError("expected RateLimitError")
    except RateLimitError as err:
        assert err.details["retry_after"] == 3

    clock["t"] += resolve_phone.RESOLVE_PHONE_COOLDOWN_S
    resolve_phone.enforce_resolve_phone_cooldown()


def test_resolve_phone_cooldown_reservation_is_concurrency_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    target = resolve_phone._cooldown_path()
    original_exists = Path.exists
    readers = threading.Barrier(2, timeout=0.2)

    def synchronized_exists(path):
        exists = original_exists(path)
        if path == target and not exists:
            try:
                readers.wait()
            except threading.BrokenBarrierError:
                pass
        return exists

    monkeypatch.setattr(Path, "exists", synchronized_exists)

    def reserve():
        try:
            resolve_phone.enforce_resolve_phone_cooldown(now=100.0)
        except RateLimitError:
            return "limited"
        return "reserved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: reserve(), range(2)))

    assert sorted(outcomes) == ["limited", "reserved"]
