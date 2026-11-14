"""Rebuild the clone-state databases in this directory.

    TGCLI_STATE_DIR=$(mktemp -d) uv run python \
        tests/fixtures/make_clone_state_fixtures.py

The checked-in files were written by the code before the direct state layer
(dict round trip with dirty-key sets); `tests/test_clone_state_fixtures.py`
loads them with the current code. Rerun this only when the schema moves, and
then the new files replace these.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from tgcli.clone import state

HERE = Path(__file__).resolve().parent
PEER = {"account_user_id": 100000001, "source_peer_id": 1234567890}


def broadcast() -> state.CloneState:
    s = state.CloneState.new(
        **PEER, source_title="Example Channel", source_kind="broadcast"
    )
    s.created_at = "2026-01-02T03:04:05+00:00"
    s.last_synced_at = "2026-02-03T04:05:06+00:00"
    s.creation_marker = "tgcli-clone-fixture-abc123"
    s.destination_peer_id = 1987654321
    s.destination_title = "[Clone] Example Channel"
    s.destination_username = "example_clone"
    s.cursor = 42
    s.retry_not_before = "2026-03-04T05:06:07+00:00"
    s.comments = state.Comments.ENABLED
    s.discussion_source_peer_id = 55
    s.discussion_destination_peer_id = 66
    s.discussion_linked = True
    s.discussion_cursor = 9
    s.pinned_dest_id = 30
    for source, dest in ((7, 70), (8, 80), (42, 420)):
        s.record_mapping(source, dest)
    for source, dest in ((3, 4), (5, 6)):
        s.record_discussion_mapping(source, dest)
    s.record_avatar(123, 999)
    s.record_avatar(55, 998)
    s.record_anchor(10, 500)
    s.record_anchor(11, 500)
    return s


def forum() -> state.CloneState:
    s = state.CloneState.new(
        account_user_id=100000001,
        source_peer_id=1234567891,
        source_title="Example Forum",
        source_kind="forum",
    )
    s.created_at = "2026-01-02T03:04:05+00:00"
    s.destination_peer_id = 1987654322
    s.cursor = 3
    s.pin_occupied = True
    s.record_topic(2, 1002)
    s.record_topic(9, 1009)
    s.record_mapping(1, 11)
    return s


def main() -> int:
    for name, build in (("broadcast", broadcast), ("forum", forum)):
        built = build()
        state.save(built)
        shutil.copyfile(state.path_for(built.clone_id), HERE / f"clone_state_{name}.db")
        print(f"{name}: {built.clone_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
