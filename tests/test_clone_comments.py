"""Unit tests for comments.sync_phase source-group resolve guards.

Mirrors the refusal shapes covered for attribution._resolve and roster.collect
in 1.2.8: a source discussion group that turns private must not escape as a
raw traceback through cli.py's unrecognized-exception path.
"""

import asyncio
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import comments, state


class FakeTg:
    """Minimal duck-typed client for comments.sync_phase source resolve."""

    def __init__(self, *, entity_error=None, discussion_entity=True):
        self._entity_error = entity_error
        self._discussion_entity = discussion_entity
        self.source_group = SimpleNamespace(id=55)
        self.destination_group = SimpleNamespace(
            id=888, creator=True, broadcast=False, megagroup=True, forum=False
        )
        self.iterated = False

    async def get_entity(self, peer):
        assert isinstance(peer, types.PeerChannel)
        if peer.channel_id == 55:
            if self._entity_error is not None:
                raise self._entity_error
            if not self._discussion_entity:
                raise ValueError("unresolved")
            return self.source_group
        if peer.channel_id == 888:
            return self.destination_group
        raise AssertionError(f"unexpected peer {peer.channel_id}")

    async def iter_messages(self, entity, *, min_id=0, reverse=False):
        self.iterated = True
        if False:  # pragma: no cover — async generator shape
            yield None


def seed(*, comments_value="enabled"):
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=123, source_title="Source channel"
    )
    clone_state.destination_peer_id = 999
    clone_state.comments = comments_value
    clone_state.discussion_source_peer_id = 55
    clone_state.discussion_destination_peer_id = 888
    clone_state.discussion_linked = True
    return clone_state


def run(tg, clone_state):
    async def boom(*_args, **_kwargs):
        raise AssertionError("copy_batch must not run when source is unreadable")

    return asyncio.run(
        comments.sync_phase(
            tg,
            clone_state,
            SimpleNamespace(id=123),
            SimpleNamespace(id=999),
            mutate=None,
            copy_batch=boom,
            counters={
                "skipped_service": 0,
                "skipped_autoforward": 0,
            },
            limited=lambda: False,
            resolve_ctx=SimpleNamespace(anchors={}),
        )
    )


def test_sync_phase_marks_unavailable_when_source_group_turned_private(
    tmp_path, monkeypatch
):
    """Telegram refuses to name the peer with an RPCError, not a ValueError.
    Phase 1 has already copied posts; a later private group must degrade to
    comments: unavailable, never raise out of sync_phase."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()
    tg = FakeTg(entity_error=telethon_errors.ChannelPrivateError(request=None))
    more = run(tg, clone_state)
    assert more is False
    assert clone_state.comments == "unavailable"
    assert tg.iterated is False
    # persisted so the next sync skips phase 2 without re-probing forever
    reloaded = state.load(clone_state.clone_id)
    assert reloaded is not None
    assert reloaded.comments == "unavailable"


def test_sync_phase_clears_discussion_progress_when_marking_unavailable(
    tmp_path, monkeypatch
):
    """A partially advanced phase-2 cursor/id_map is incompatible with
    comments != enabled (state.from_dict). Degrade must reset both or the
    next sync dies on load with 'manual repair required'."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()
    clone_state.discussion_cursor = 5
    clone_state.discussion_id_map = {"1": 2}
    state.save(clone_state)
    tg = FakeTg(entity_error=telethon_errors.ChannelPrivateError(request=None))
    assert run(tg, clone_state) is False
    assert clone_state.comments == "unavailable"
    assert clone_state.discussion_cursor == 0
    assert clone_state.discussion_id_map == {}
    reloaded = state.load(clone_state.clone_id)
    assert reloaded is not None
    assert reloaded.comments == "unavailable"
    assert reloaded.discussion_cursor == 0
    assert reloaded.discussion_id_map == {}


def test_sync_phase_marks_unavailable_when_source_group_unresolved(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()
    tg = FakeTg(discussion_entity=False)
    more = run(tg, clone_state)
    assert more is False
    assert clone_state.comments == "unavailable"
    assert tg.iterated is False


def test_sync_phase_marks_unavailable_when_source_group_forbidden(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()
    tg = FakeTg(entity_error=telethon_errors.ChatForbiddenError(request=None))
    more = run(tg, clone_state)
    assert more is False
    assert clone_state.comments == "unavailable"


def test_sync_phase_propagates_floodwait_from_source_resolve(tmp_path, monkeypatch):
    """FloodWait must still escape so the ADR-0045 cooldown wrapper arms."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()
    flood = telethon_errors.FloodWaitError(request=None)
    flood.seconds = 30
    tg = FakeTg(entity_error=flood)
    with pytest.raises(telethon_errors.FloodWaitError):
        run(tg, clone_state)
    assert clone_state.comments == "enabled"
