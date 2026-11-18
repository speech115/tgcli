"""Tests for the native dialog / URL open escape hatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tgcli import desktop
from tgcli.errors import PolicyError


def test_ask_secret_hidden_false_omits_hidden_clause(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(argv, **kwargs):
        assert "with hidden answer" not in argv[2]
        return SimpleNamespace(returncode=0, stdout="text returned:12345\n")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.ask_secret("T", "code", hidden=False) == "12345"


def test_ask_secret_falls_back_when_osascript_missing(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    monkeypatch.setattr(desktop.sys.stdin, "readline", lambda: "no-dialog\n")
    assert desktop.ask_secret("T", "P", hidden=True) == "no-dialog"


def test_ask_secret_cancel_raises_clean_error(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="User canceled.")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    with pytest.raises(PolicyError, match="cancelled"):
        desktop.ask_secret("T", "P", hidden=True)
