import json

import pytest

from tgcli import preview_commit, safety
from tgcli.cli import main


def test_preview_commit_registry_covers_every_handshake():
    assert {
        selector: spec.kind for selector, spec in preview_commit.HANDSHAKES.items()
    } == {
        ("send", None): "send",
        ("edit", None): "edit",
        ("delete", None): "delete",
        ("forward", None): "forward",
        ("clone", "init"): "clone-init",
        ("draft", "set"): "draft-set",
        ("draft", "clear"): "draft-clear",
    }


def test_legacy_consume_preview_is_not_a_production_seam():
    assert not hasattr(safety, "consume_preview")


@pytest.mark.parametrize(
    "argv",
    [
        ["send", "--commit", "p_x", "--format", "html"],
        ["send", "--commit", "p_x", "--format", "md"],
        ["edit", "--commit", "p_x", "--format", "html"],
        ["clone", "init", "@source", "--commit", "p_x", "--replace"],
        ["clone", "init", "@source", "--commit", "p_x", "--no-comments"],
    ],
)
def test_commit_refuses_every_preview_flag(argv, monkeypatch, capsys):
    # The preview already fixed these choices; a flag beside --commit would be
    # silently ignored, so it is a usage error before any gate or I/O.
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    monkeypatch.setenv("TGCLI_READONLY", "1")

    assert main(["--json", *argv]) == 1

    out = capsys.readouterr()
    assert json.loads(out.out)["error"]["code"] == "USAGE"
    command = " ".join(argv[:2]) if argv[0] == "clone" else argv[0]
    assert f"{command} --commit accepts only a preview id" in out.err
