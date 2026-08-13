"""Archive peer-identity read seam (thermos T30 / ADR-0116)."""

from __future__ import annotations

from tgcli.archive import peers as peers_mod, search as search_mod, store as store_mod


def test_peers_resolve_prefers_explicit_scope_over_sync_state(tmp_path):
    conn = store_mod.connect(tmp_path / "archive.db")
    try:
        store_mod.ensure_meta(conn, account_user_id=1, account_alias="t")
        store_mod.add_scope(
            conn,
            peer_id=10,
            kind="user",
            title="Scope Title",
            username="scoped",
            chat_ref="@scoped",
        )
        store_mod.upsert_sync_state(
            conn,
            10,
            oldest_id=1,
            newest_id=1,
            more=False,
            kind="user",
            title="Sync Title",
            username="syncu",
            chat_ref="@syncu",
        )
        store_mod.upsert_sync_state(
            conn,
            20,
            oldest_id=1,
            newest_id=1,
            more=False,
            kind="user",
            title="Only Sync",
            username="onlysync",
            chat_ref="@onlysync",
        )

        assert peers_mod.resolve(conn, 10) == {
            "chat_ref": "@scoped",
            "title": "Scope Title",
            "username": "scoped",
            "kind": "user",
        }
        assert peers_mod.resolve(conn, 20)["username"] == "onlysync"
        assert peers_mod.resolve(conn, 99) == {
            "chat_ref": "99",
            "title": None,
            "username": None,
            "kind": None,
        }
        assert search_mod.resolve_peer_id(conn, "@scoped") == 10
        assert search_mod.resolve_peer_id(conn, "@onlysync") == 20
        assert "COALESCE(s.chat_ref, ss.chat_ref)" in peers_mod.IDENTITY_SELECT
        assert "LEFT JOIN scope" in peers_mod.IDENTITY_JOINS
        assert "LEFT JOIN sync_state" in peers_mod.IDENTITY_JOINS
    finally:
        conn.close()
