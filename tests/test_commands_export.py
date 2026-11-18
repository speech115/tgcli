import json

import pytest

from tgcli.commands.export import _resume_after_id
from tgcli.errors import ExportError


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
