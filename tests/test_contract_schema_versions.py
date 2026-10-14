"""CONTRACT's JSON examples name the store schema versions the code writes."""

from __future__ import annotations

import re
from pathlib import Path

from tgcli.archive import schema as archive_schema
from tgcli.clone import statedb as clone_statedb

CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "CONTRACT.md"
JSON_BLOCK = re.compile(r"```json\n(.*?)```", re.DOTALL)
VERSION = re.compile(r'"schema_version":(\d+)')


def _documented_versions() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {"archive": [], "clone": []}
    for block in JSON_BLOCK.findall(CONTRACT.read_text(encoding="utf-8")):
        store = "clone" if '"clone_id"' in block else "archive"
        found[store].extend(int(n) for n in VERSION.findall(block))
    return found


def test_contract_examples_name_the_current_store_schema_versions():
    found = _documented_versions()
    # Fewer examples than this means the scan stopped finding them.
    assert len(found["archive"]) >= 2
    assert len(found["clone"]) >= 1
    assert set(found["archive"]) == {archive_schema.SCHEMA_VERSION}
    assert set(found["clone"]) == {clone_statedb.SCHEMA_VERSION}
