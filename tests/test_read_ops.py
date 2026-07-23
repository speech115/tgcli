"""Guards for the read_ops registry: the CLI adapter, the batch adapter, and
dispatch all derive from one table, and these tests fail when a read operation
is added to the union without a matching row."""

from __future__ import annotations

from typing import get_args

import pytest

from tgcli import read_ops
from tgcli.errors import PolicyError
from tgcli.parser import build_parser


# One representative invocation per op, in table order. A new read operation
# without an entry here fails test_every_operation_is_covered_by_this_file.
CLI_INVOCATIONS = {
    "dialogs": ["dialogs"],
    "read": ["read", "@chat"],
    "search": ["search", "@chat", "term"],
    "latest": ["latest", "@chat"],
    "message": ["message", "@chat", "7"],
    "info": ["info", "@chat"],
    "count": ["count", "@chat"],
    "resolve": ["resolve", "@user"],
    "mutual-chats": ["mutual-chats", "@user"],
    "contacts.list": ["contacts", "list"],
    "contacts.search": ["contacts", "search", "term"],
    "media.manifest": ["media", "manifest", "@chat"],
    "thread": ["thread", "@chat", "7"],
    "draft.show": ["draft", "show", "@chat"],
    "draft.list": ["draft", "list"],
}

BATCH_PAYLOADS = {
    "dialogs": {"op": "dialogs"},
    "read": {"op": "read", "chat": "@chat"},
    "search": {"op": "search", "chat": "@chat", "query": "term"},
    "latest": {"op": "latest", "chat": "@chat"},
    "message": {"op": "message", "chat": "@chat", "message_id": 7},
    "info": {"op": "info", "chat": "@chat"},
    "count": {"op": "count", "chat": "@chat"},
    "resolve": {"op": "resolve", "ref": "@user"},
    "mutual-chats": {"op": "mutual-chats", "ref": "@user"},
    "contacts.list": {"op": "contacts.list"},
    "contacts.search": {"op": "contacts.search", "query": "term"},
    "media.manifest": {"op": "media.manifest", "source": "@chat"},
    "thread": {"op": "thread", "chat": "@chat", "message_id": 7},
    "draft.show": {"op": "draft.show", "chat": "@chat"},
    "draft.list": {"op": "draft.list"},
}

OPERATION_NAMES = frozenset(
    cls.__dataclass_fields__["name"].default for cls in get_args(read_ops.ReadOperation)
)


def test_registry_covers_the_operation_union_exactly():
    assert read_ops.BATCH_OP_NAMES == OPERATION_NAMES


def test_operation_discriminator_cannot_be_overridden():
    with pytest.raises(TypeError, match="unexpected keyword argument 'name'"):
        read_ops.Dialogs(
            limit=50,
            unread_only=False,
            kind=None,
            name="read",
        )


def test_every_operation_is_covered_by_this_file():
    assert set(CLI_INVOCATIONS) == OPERATION_NAMES
    assert set(BATCH_PAYLOADS) == OPERATION_NAMES


@pytest.mark.parametrize("name", sorted(CLI_INVOCATIONS))
def test_cli_invocation_builds_its_operation(name):
    args = build_parser().parse_args(CLI_INVOCATIONS[name])

    operation = read_ops.from_cli(args)

    assert operation is not None
    assert operation.name == name


@pytest.mark.parametrize("name", sorted(BATCH_PAYLOADS))
def test_batch_payload_builds_its_operation(name):
    assert read_ops.from_batch(BATCH_PAYLOADS[name]).name == name


@pytest.mark.parametrize(
    "argv",
    [
        ["send", "@chat", "hi"],
        ["media", "download", "@chat", "7"],
        ["doctor"],
    ],
)
def test_non_read_commands_do_not_map_to_an_operation(argv):
    assert read_ops.from_cli(build_parser().parse_args(argv)) is None


def test_unknown_batch_op_is_a_policy_error():
    with pytest.raises(PolicyError, match="unhandled batch op"):
        read_ops.from_batch({"op": "nope"})
