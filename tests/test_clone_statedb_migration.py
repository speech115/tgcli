"""SQLite schema migrations."""

from __future__ import annotations

import sqlite3

import pytest

from tgcli.clone import state
from tgcli.errors import PolicyError


def _fresh(**overrides) -> state.CloneState:
    s = state.CloneState.new(
        account_user_id=100000001,
        source_peer_id=1234567890,
        source_title="Example Channel",
    )
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


@pytest.mark.parametrize(
    "intents",
    [
        '{"posts": {"kind": "batch", "sources": []}}',
        '{"posts": {"kind": "rest", "sources": [1, 2]}}',
        '{"elsewhere": {"kind": "batch", "sources": [1]}}',
        '{"posts": {"kind": "batch", "sources": ["1"]}}',
        '["posts"]',
        "not json",
    ],
)
def test_impossible_send_intent_is_refused(tmp_path, monkeypatch, intents):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    s = _fresh()
    state.save(s)
    conn = sqlite3.connect(state.path_for(s.clone_id))
    conn.execute("UPDATE meta SET send_intents = ?", (intents,))
    conn.commit()
    conn.close()

    with pytest.raises(PolicyError, match="manual repair"):
        state.load(s.clone_id)
