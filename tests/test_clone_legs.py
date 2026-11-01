"""Direct unit tests for clone legs."""

from tgcli.clone import legs, state


def _state():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 55
    return clone_state


def test_posts_leg_reads_and_writes_post_fields():
    clone_state = _state()
    leg = legs.posts(clone_state)
    leg.record_mapping(1, 10)
    leg.cursor = 1
    assert leg.dest_for(1) == 10
    assert clone_state.id_map == {"1": 10}
    assert clone_state.cursor == 1
    assert leg.source_kind == "broadcast"
    assert leg.destination_kind == "broadcast"


def test_posts_leg_reports_forum_destination_kind():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S", source_kind="forum"
    )
    assert legs.posts(clone_state).destination_kind == "forum"


def test_discussion_leg_reads_and_writes_discussion_fields():
    clone_state = _state()
    leg = legs.discussion(clone_state)
    leg.record_mapping(1, 10)
    leg.cursor = 1
    assert leg.dest_for(1) == 10
    assert clone_state.discussion_id_map == {"1": 10}
    assert clone_state.discussion_cursor == 1
    assert clone_state.id_map == {}
    assert clone_state.cursor == 0


def test_discussion_leg_applies_megagroup_rules():
    leg = legs.discussion(_state())
    assert leg.source_kind == "megagroup"
    assert leg.destination_kind == "megagroup"


def test_raw_ids_land_in_each_legs_own_map():
    clone_state = _state()
    posts, comments = legs.posts(clone_state), legs.discussion(clone_state)
    posts.record_raw_mapping(1, 10)
    comments.record_raw_mapping(1, 20)
    assert clone_state.id_map == {"1": 10}
    assert clone_state.discussion_id_map == {"1": 20}
    assert posts.dest_for_raw(1) == 10
    assert comments.dest_for_raw(1) == 20
    assert posts.other.dest_for_raw(1) == 20
    assert comments.other.dest_for_raw(1) == 10
    assert posts.dest_for_raw(2) is None
