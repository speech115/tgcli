"""Every CLI command is classified, and every write is refused when read-only.

Each command declares its class and readonly gate in `tgcli.registry`; a
command could once ship without a gate (the removed `accounts import` did). A
new command now fails here until it has a registry entry and sits in exactly
one class below.
"""

import argparse

import pytest

from tgcli.cli import main
from tgcli.parser import build_parser
from tgcli.preview_commit import HANDSHAKES
from tgcli.registry import COMMANDS

# Network reads: Telegram is only asked, never changed. Output files a read
# writes (`media download`, `export`) are the caller's own.
READS = {
    "contacts list",
    "contacts search",
    "count",
    "dialogs",
    "draft list",
    "draft show",
    "export messages",
    "export subscribers",
    "info",
    "latest",
    "media download",
    "media manifest",
    "message",
    "read",
    "resolve",
    "search",
    "thread",
}
# Commands that touch neither Telegram nor tgcli state.
LOCAL_READS = {
    "accounts list",
    "accounts show",
    "archive history",
    "archive list",
    "archive read",
    "archive search",
    "archive status",
    "clone export-state",
    "clone status",
    "doctor",
    "store stats",
}
# Writes without a preview/commit handshake, each with an argv that would
# write: under TGCLI_READONLY=1 every one must exit 2 before it does.
GATED_WRITES = {
    "accounts login": ["accounts", "login", "spare", "--phone", "+10000000000"],
    "accounts remove": ["accounts", "remove", "spare", "--confirm"],
    "archive add": ["archive", "add", "@chat"],
    "archive backfill": ["archive", "backfill", "@chat"],
    "archive init": ["archive", "init"],
    "archive rebaseline": ["archive", "rebaseline"],
    "archive remove": ["archive", "remove", "@chat"],
    "archive sync": ["archive", "sync"],
    "archive transcribe": ["archive", "transcribe"],
    "clone sync": ["clone", "sync", "@chat"],
    "run": ["run", "--write", "-"],
    "store cleanup": ["store", "cleanup", "--confirm"],
    "transcribe": ["transcribe", "@chat", "1"],
}
# Preview → commit writes: the commit leg must exit 2 when read-only.
HANDSHAKE_COMMITS = {
    "clone init": ["clone", "init", "@chat", "--commit", "p"],
    "delete": ["delete", "--commit", "p"],
    "draft clear": ["draft", "clear", "--commit", "p"],
    "draft set": ["draft", "set", "--commit", "p"],
    "edit": ["edit", "--commit", "p"],
    "forward": ["forward", "--commit", "p"],
    "send": ["send", "--commit", "p"],
}

CONFIG = """
default_account = "main"

[accounts.main]
api_id = 1
api_hash = "hash"
session = "main"

[accounts.spare]
api_id = 2
api_hash = "hash"
session = "spare"
"""


def _leaves(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()):
    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    if not subparsers:
        yield " ".join(prefix)
        return
    for action in subparsers:
        # Aliases share one parser; classify the command by its first name.
        seen: set[int] = set()
        for name, child in action.choices.items():
            if id(child) not in seen:
                seen.add(id(child))
                yield from _leaves(child, (*prefix, name))


def test_every_command_is_in_exactly_one_class():
    classes = [
        READS,
        LOCAL_READS,
        set(GATED_WRITES),
        set(HANDSHAKE_COMMITS),
    ]
    leaves = sorted(_leaves(build_parser()))
    unclassified = [leaf for leaf in leaves if not any(leaf in c for c in classes)]
    doubled = [leaf for leaf in leaves if sum(leaf in c for c in classes) > 1]
    stale = sorted(set().union(*classes) - set(leaves))
    assert (unclassified, doubled, stale) == ([], [], [])


def test_every_command_is_registered_with_its_class():
    """`tgcli.registry` is what the lifecycle routes on; it must agree with
    the parser's leaves and with the classes above, gate included."""
    expected = {
        **dict.fromkeys(READS, "read"),
        **dict.fromkeys(LOCAL_READS, "local-read"),
        **dict.fromkeys(GATED_WRITES, "write"),
        **dict.fromkeys(HANDSHAKE_COMMITS, "handshake"),
    }
    assert set(COMMANDS) == set(_leaves(build_parser()))
    assert {key: spec.kind for key, spec in COMMANDS.items()} == expected
    ungated = sorted(
        key
        for key, spec in COMMANDS.items()
        if (spec.kind in ("write", "handshake")) != (spec.gate != "none")
    )
    assert ungated == []
    assert all(
        (spec.kind == "handshake") == (spec.gate == "handshake")
        for spec in COMMANDS.values()
    )


def test_handshake_commits_match_the_preview_commit_registry():
    registered = {
        " ".join(part for part in key if part is not None) for key in HANDSHAKES
    }
    assert registered == set(HANDSHAKE_COMMITS)


@pytest.mark.parametrize(
    "argv",
    [*GATED_WRITES.values(), *HANDSHAKE_COMMITS.values()],
    ids=[*GATED_WRITES, *HANDSHAKE_COMMITS],
)
def test_every_write_is_refused_when_read_only(tmp_path, monkeypatch, argv):
    config = tmp_path / "config.toml"
    config.write_text(CONFIG)
    monkeypatch.setenv("TGCLI_CONFIG", str(config))
    monkeypatch.setenv("TGCLI_READONLY", "1")
    sessions = tmp_path / "state" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "spare.session").write_bytes(b"session")

    def no_telegram(*args, **kwargs):
        raise AssertionError("a read-only write reached the Telegram client")

    monkeypatch.setattr("tgcli.session.client", no_telegram)

    assert main(["--json", *argv]) == 2
    assert (sessions / "spare.session").read_bytes() == b"session"
