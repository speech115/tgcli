"""Unit tests for contacts.resolvePhone shared cooldown."""

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
