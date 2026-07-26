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
from tgcli.errors import PolicyError


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


def test_sync_phase_reuses_discussion_entities_across_windows(tmp_path, monkeypatch):
    """ADR-0061: the ADR-0051 interleave calls sync_phase once per 50-batch
    window with one shared ResolveContext; the discussion peers cannot change
    identity mid-run, so later windows must not re-pay the two GetChannels
    RPCs the first window already made."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()

    class CountingTg(FakeTg):
        def __init__(self):
            super().__init__()
            self.entity_calls = 0

        async def get_entity(self, peer):
            self.entity_calls += 1
            return await super().get_entity(peer)

        async def get_messages(self, destination, limit=1):
            # verify_tail stays per-window (it is the foreign-post guard);
            # an empty tail satisfies it without faking history.
            return []

    tg = CountingTg()
    ctx = SimpleNamespace(anchors={})

    async def windows():
        for _ in range(3):
            await comments.sync_phase(
                tg,
                clone_state,
                SimpleNamespace(id=123),
                SimpleNamespace(id=999),
                mutate=None,
                copy_batch=None,
                counters={"skipped_service": 0, "skipped_autoforward": 0},
                limited=lambda: False,
                resolve_ctx=ctx,
            )

    asyncio.run(windows())

    assert tg.entity_calls == 2


def test_privatized_source_degrades_on_a_cached_window(tmp_path, monkeypatch):
    """ADR-0061 review fix: with entities cached after window 1, a source
    group that turns private before window 2 surfaces on the window's own
    reads — it must degrade to comments: unavailable exactly like the
    first-window resolve guard, never escape as a raw traceback."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()

    class PrivatizedOnSecondWindow(FakeTg):
        def __init__(self):
            super().__init__()
            self.windows = 0

        async def get_messages(self, destination, limit=1):
            return []

        async def iter_messages(self, entity, *, min_id=0, reverse=False):
            self.windows += 1
            if self.windows >= 2:
                raise telethon_errors.ChannelPrivateError(request=None)
            if False:  # pragma: no cover — async generator shape
                yield None

    tg = PrivatizedOnSecondWindow()
    ctx = SimpleNamespace(anchors={})

    async def two_windows():
        results = []
        for _ in range(2):
            results.append(
                await comments.sync_phase(
                    tg,
                    clone_state,
                    SimpleNamespace(id=123),
                    SimpleNamespace(id=999),
                    mutate=None,
                    copy_batch=None,
                    counters={"skipped_service": 0, "skipped_autoforward": 0},
                    limited=lambda: False,
                    resolve_ctx=ctx,
                )
            )
        return results

    assert asyncio.run(two_windows()) == [False, False]
    assert clone_state.comments == "unavailable"
    assert clone_state.discussion_cursor == 0
    assert clone_state.discussion_id_map == {}
    reloaded = state.load(clone_state.clone_id)
    assert reloaded is not None
    assert reloaded.comments == "unavailable"


def test_floodwait_from_a_cached_window_read_still_escapes(tmp_path, monkeypatch):
    """FloodWait on the window's reads must keep reaching the ADR-0045
    cooldown wrapper — the degrade guard may not swallow it."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()

    class FloodsOnRead(FakeTg):
        async def get_messages(self, destination, limit=1):
            return []

        async def iter_messages(self, entity, *, min_id=0, reverse=False):
            flood = telethon_errors.FloodWaitError(request=None)
            flood.seconds = 30
            raise flood
            if False:  # pragma: no cover — async generator shape
                yield None

    tg = FloodsOnRead()
    ctx = SimpleNamespace(anchors={}, source_group=tg.source_group)

    async def one_window():
        return await comments.sync_phase(
            tg,
            clone_state,
            SimpleNamespace(id=123),
            SimpleNamespace(id=999),
            mutate=None,
            copy_batch=None,
            counters={"skipped_service": 0, "skipped_autoforward": 0},
            limited=lambda: False,
            resolve_ctx=ctx,
        )

    with pytest.raises(telethon_errors.FloodWaitError):
        asyncio.run(one_window())
    assert clone_state.comments == "enabled"


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


@pytest.mark.parametrize(
    "error",
    [
        telethon_errors.ChannelPrivateError(request=None),
        telethon_errors.ChannelInvalidError(request=None),
        telethon_errors.ChatForbiddenError(request=None),
    ],
)
def test_sync_phase_maps_unreachable_discussion_destination_to_policy(
    tmp_path, monkeypatch, error
):
    """CONTRACT §11: an unavailable destination discussion group is exit 2.
    Only ValueError was mapped, so a destination the account was removed from
    escaped as a raw Telethon traceback."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    clone_state = seed()

    class DestinationGone(FakeTg):
        async def get_entity(self, peer):
            if peer.channel_id == 888:
                raise error
            return await super().get_entity(peer)

    with pytest.raises(PolicyError, match="discussion destination is unavailable"):
        run(DestinationGone(), clone_state)
