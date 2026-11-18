"""Guards for the read_ops registry: the CLI adapter and dispatch derive from
one table, and these tests fail when a read operation is added to the union
without a matching row."""

from __future__ import annotations

from typing import get_args

import pytest

from tgcli import read_ops
from tgcli.parser import build_parser

# One representative invocation per op, in table order. A new read operation
# without an entry here fails test_every_operation_is_covered_by_this_file.
CLI_INVOCATIONS = {
    "dialogs": ["dialogs"],
    "read": ["read", "@chat"],
    "search": ["search", "@chat", "term"],
    "message": ["message", "@chat", "7"],
    "info": ["info", "@chat"],
    "resolve": ["resolve", "@user"],
    "media.manifest": ["media", "manifest", "@chat"],
}

OPERATION_NAMES = frozenset(
    cls.__dataclass_fields__["name"].default for cls in get_args(read_ops.ReadOperation)
)


def test_registry_covers_the_operation_union_exactly():
    assert frozenset(read_ops._SPECS) == OPERATION_NAMES


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
