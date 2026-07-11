import asyncio

import pytest

from tgcli.commands.export import _atomic_text_destination


def test_atomic_destination_cleans_temp_file_on_cancellation(tmp_path):
    destination = tmp_path / "out.jsonl"

    with pytest.raises(asyncio.CancelledError):
        with _atomic_text_destination(destination) as handle:
            handle.write("partial")
            raise asyncio.CancelledError()

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []
