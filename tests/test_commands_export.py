import asyncio
import json

import pytest

from tgcli.commands.export import _atomic_text_destination, _resume_after_id
from tgcli.errors import ExportError


def test_atomic_destination_cleans_temp_file_on_cancellation(tmp_path):
    destination = tmp_path / "out.jsonl"

    with pytest.raises(asyncio.CancelledError):
        with _atomic_text_destination(destination) as handle:
            handle.write("partial")
            raise asyncio.CancelledError()

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def _jsonl(tmp_path, last):
    destination = tmp_path / "messages.jsonl"
    destination.write_text(json.dumps(last) + "\n", encoding="utf-8")
    return destination


@pytest.mark.parametrize("bad", [True, False, 0, -1, "5", 5.0, None])
def test_resume_rejects_a_non_positive_int_id(tmp_path, bad):
    destination = _jsonl(tmp_path, {"id": bad})

    with pytest.raises(ExportError) as excinfo:
        _resume_after_id(destination)

    assert "no valid id" in str(excinfo.value)


def test_resume_accepts_a_positive_int_id(tmp_path):
    assert _resume_after_id(_jsonl(tmp_path, {"id": 7})) == 7
