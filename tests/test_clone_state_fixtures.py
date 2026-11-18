"""Clone-state databases written by an earlier build still load unchanged.

The two `.db` files were written by the build before the direct state layer;
`tests/fixtures/make_clone_state_fixtures.py` rebuilds them.
"""

import shutil
from pathlib import Path

from tgcli.clone import state

FIXTURES = Path(__file__).parent / "fixtures"
ACCOUNT = 100000001


def _install(name: str, source_peer_id: int, kind: str) -> str:
    clone_id = state.clone_id(ACCOUNT, source_peer_id, kind)
    state.ensure_clones_dir()
    shutil.copyfile(FIXTURES / f"clone_state_{name}.db", state.path_for(clone_id))
    return clone_id


def test_a_forum_clone_written_by_the_previous_build_loads_whole():
    loaded = state.load(_install("forum", 1234567891, "forum"))

    assert loaded is not None
    assert (loaded.source_kind, loaded.destination_kind) == ("forum", "forum")
    assert loaded.destination_peer_id == 1987654322
    assert loaded.cursor == 3
    assert loaded.pin_occupied is True
    assert loaded.pinned_dest_id is None
    assert [loaded.topic_dest_for(n) for n in (2, 9, 3)] == [1002, 1009, None]
    assert loaded.dest_for(1) == 11
    assert loaded.max_destination_id() == 1009
    assert loaded.comments == "none"


def test_a_previous_builds_database_takes_this_builds_writes():
    clone_id = _install("broadcast", 1234567890, "broadcast")
    loaded = state.load(clone_id)
    assert loaded is not None
    loaded.record_mapping(43, 430)
    loaded.record_discussion_mapping(7, 8)
    loaded.record_anchor(12, 500)
    loaded.cursor = 43
    state.save(loaded)

    again = state.load(clone_id)

    assert again is not None
    assert [again.dest_for(n) for n in (7, 43)] == [70, 430]
    assert again.discussion_dest_for(7) == 8
    assert again.anchor_dest_for(12) == 500
    assert again.cursor == 43
    assert len(again.id_map) == 4
