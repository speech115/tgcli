"""Guards for the read_ops registry: the CLI adapter, the batch adapter, and
dispatch all derive from one table, and these tests fail when a read operation
is added to the union without a matching row."""

from __future__ import annotations

import argparse
import re
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


def _cli_choices(argv: list[str], dest: str) -> tuple:
    """The choices argparse offers for one flag of one (sub)command."""
    target = build_parser()
    for name in argv:
        subparsers = next(
            action
            for action in target._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        target = subparsers.choices[name]
    action = next(action for action in target._actions if action.dest == dest)
    return tuple(action.choices)


@pytest.mark.parametrize(
    "argv, dest, choices",
    [
        (["dialogs"], "kind", read_ops.DIALOG_KINDS),
        (["media", "manifest"], "media_type", read_ops.MEDIA_KINDS),
    ],
)
def test_batch_enums_match_the_cli_choices(argv, dest, choices):
    """The batch adapter rejects exactly what the CLI parser rejects."""
    assert _cli_choices(argv, dest) == tuple(choices)


# T15 (thermos audit 2026-08-13): batch bool/int fields must reject anything
# that is not a real JSON boolean/integer, matching CLI argparse semantics —
# truthiness on a string like "false" must not silently flip a filter.
BATCH_BOOL_FIELDS = [
    ("dialogs", "unread_only"),
    ("search", "all"),
    ("info", "full"),
    ("contacts.search", "global"),
    ("thread", "replies"),
]

BATCH_INT_FIELDS = [
    ("dialogs", "limit"),
    ("read", "limit"),
    ("read", "before_id"),
    ("read", "after_id"),
    ("read", "topic"),
    ("search", "limit"),
    ("message", "message_id"),
    ("message", "context"),
    ("thread", "message_id"),
    ("thread", "depth"),
    ("thread", "limit"),
    ("media.manifest", "limit"),
]


@pytest.mark.parametrize("op, field", BATCH_BOOL_FIELDS)
@pytest.mark.parametrize("bad_value", ["false", "true", 1, 0, []])
def test_batch_bool_field_rejects_non_boolean(op, field, bad_value):
    payload = {**BATCH_PAYLOADS[op], field: bad_value}
    message = f"batch {op}.{field} must be a JSON boolean"
    with pytest.raises(PolicyError, match=re.escape(message)):
        read_ops.from_batch(payload)


@pytest.mark.parametrize("op, field", BATCH_INT_FIELDS)
@pytest.mark.parametrize("bad_value", ["20", 20.5, True, False, []])
def test_batch_int_field_rejects_non_integer(op, field, bad_value):
    payload = {**BATCH_PAYLOADS[op], field: bad_value}
    message = f"batch {op}.{field} must be a JSON integer"
    with pytest.raises(PolicyError, match=re.escape(message)):
        read_ops.from_batch(payload)


@pytest.mark.parametrize("op, field", BATCH_BOOL_FIELDS)
def test_batch_bool_field_accepts_real_booleans(op, field):
    for value in (True, False):
        payload = {**BATCH_PAYLOADS[op], field: value}
        read_ops.from_batch(payload)  # must not raise


@pytest.mark.parametrize("op, field", BATCH_INT_FIELDS)
def test_batch_int_field_accepts_real_integers(op, field):
    payload = {**BATCH_PAYLOADS[op], field: 3}
    read_ops.from_batch(payload)  # must not raise


def test_batch_optional_int_field_stays_none_when_absent():
    op = read_ops.from_batch({"op": "read", "chat": "@chat"})
    assert op.before_id is None
    assert op.after_id is None
    assert op.topic is None
