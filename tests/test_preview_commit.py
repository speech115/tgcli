from tgcli import preview_commit, safety


def test_preview_commit_registry_covers_every_handshake():
    assert {
        selector: spec.kind for selector, spec in preview_commit.HANDSHAKES.items()
    } == {
        ("send", None): "send",
        ("edit", None): "edit",
        ("delete", None): "delete",
        ("forward", None): "forward",
        ("clone", "init"): "clone-init",
        ("clone", "refresh"): "clone-refresh",
        ("draft", "set"): "draft-set",
        ("draft", "clear"): "draft-clear",
    }


def test_legacy_consume_preview_is_not_a_production_seam():
    assert not hasattr(safety, "consume_preview")
