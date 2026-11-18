"""Direct unit tests for clone legs."""

from tgcli.clone import legs, state


def _state():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.comments = "enabled"
    clone_state.discussion_source_peer_id = 55
    return clone_state


def test_discussion_leg_applies_megagroup_rules():
    leg = legs.discussion(_state())
    assert leg.source_kind == "megagroup"
    assert leg.destination_kind == "megagroup"
