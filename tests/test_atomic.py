"""Durability of the sanctioned state writer (`tgcli.atomic`)."""

from __future__ import annotations

import errno
import inspect
import os
import stat

from tgcli import atomic
from tgcli.commands import export


def test_replace_text_fsyncs_the_parent_directory(tmp_path, monkeypatch):
    """The rename itself must reach the disk, not just the temp file's bytes.

    A command that reported success and then lost its state file to a power
    cut is exactly the failure atomic writes exist to prevent.
    """
    real_fsync = os.fsync
    synced_dirs: list[int] = []

    def recording_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            synced_dirs.append(fd)
            return
        real_fsync(fd)

    monkeypatch.setattr(atomic.os, "fsync", recording_fsync)
    target = tmp_path / "state.json"

    atomic.replace_text(target, '{"a": 1}')

    assert synced_dirs, "the parent directory was never fsynced"
    assert target.read_text() == '{"a": 1}'


def test_replace_text_survives_a_directory_fsync_refusal(tmp_path, monkeypatch):
    """Some filesystems refuse to fsync a directory; the write still stands."""
    real_fsync = os.fsync

    def refusing_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "fsync not supported")
        real_fsync(fd)

    monkeypatch.setattr(atomic.os, "fsync", refusing_fsync)
    target = tmp_path / "state.json"

    atomic.replace_text(target, '{"a": 2}')

    assert target.read_text() == '{"a": 2}'
    assert oct(target.stat().st_mode & 0o777) == "0o600"
    assert list(tmp_path.glob(".state.json-*.tmp")) == []


def test_replace_text_survives_an_unopenable_parent(tmp_path, monkeypatch):
    """A directory that cannot even be opened for fsync is not a write error."""
    real_open = os.open

    def refusing_open(path, flags, *args, **kwargs):
        if os.path.isdir(path):
            raise OSError(errno.EACCES, "permission denied")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(atomic.os, "open", refusing_open)
    target = tmp_path / "state.json"

    atomic.replace_text(target, '{"a": 3}')

    assert target.read_text() == '{"a": 3}'


def test_fsync_directory_is_the_public_parent_helper():
    """Cross-module callers must use the public name (ADR-0108 helper)."""
    assert callable(atomic.fsync_directory)
    assert not hasattr(atomic, "_fsync_directory")


def test_export_uses_public_fsync_directory():
    source = inspect.getsource(export)
    assert "atomic._fsync_directory" not in source
    assert "atomic.fsync_directory" in source
