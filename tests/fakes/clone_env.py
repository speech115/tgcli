"""Shared clone fixtures, imported by name in each clone sync module."""

import pytest

from tests.fakes.clone import SAMPLE


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
