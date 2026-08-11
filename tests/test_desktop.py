"""Tests for the native dialog / URL open escape hatch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tgcli import desktop
from tgcli.errors import PolicyError


def test_ask_secret_osascript_argv_has_no_secret(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/bin/{name}")
    captured: list[list[str]] = []

    def fake_run(argv, **kwargs):
        captured.append(list(argv))
        return SimpleNamespace(
            returncode=0, stdout="button returned:OK, text returned:s3cret\n"
        )

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)

    value = desktop.ask_secret("Title", "Enter password", hidden=True)

    assert value == "s3cret"
    assert len(captured) == 1
    assert captured[0][:2] == ["osascript", "-e"]
    joined = " ".join(captured[0])
    assert "s3cret" not in joined
    assert "with hidden answer" in captured[0][2]


def test_ask_secret_preserves_leading_trailing_spaces(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(argv, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="button returned:OK, text returned:  spaced secret  \n",
        )

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.ask_secret("T", "P", hidden=True) == "  spaced secret  "


def test_ask_secret_hidden_false_omits_hidden_clause(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: f"/bin/{name}")

    def fake_run(argv, **kwargs):
        assert "with hidden answer" not in argv[2]
        return SimpleNamespace(returncode=0, stdout="text returned:12345\n")

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)
    assert desktop.ask_secret("T", "code", hidden=False) == "12345"


def test_ask_secret_falls_back_to_stdin_on_non_darwin(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    monkeypatch.setattr(desktop.sys.stdin, "readline", lambda: "from-stdin\n")
    assert desktop.ask_secret("T", "P", hidden=True) == "from-stdin"


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


def test_dialog_available_requires_darwin_and_osascript(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop.dialog_available() is False
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(desktop.shutil, "which", lambda name: None)
    assert desktop.dialog_available() is False
    monkeypatch.setattr(desktop.shutil, "which", lambda name: "/usr/bin/osascript")
    assert desktop.dialog_available() is True
