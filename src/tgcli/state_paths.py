"""tgcli's state root and the private-file primitives every state owner uses.

Depends on nothing in tgcli, so config, audit and the session layer can all
build on it without importing one another.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import IO


def state_dir() -> Path:
    return Path(os.environ.get("TGCLI_STATE_DIR", "~/.local/state/tgcli")).expanduser()


def _repair_mode(path: Path, mode: int) -> None:
    """Fail-open chmod: permissions are protection, not a new failure mode."""
    try:
        if path.stat().st_mode & 0o777 != mode:
            os.chmod(path, mode)
    except OSError:
        pass


def private_dir(path: Path) -> Path:
    """Create ``path`` (and missing parents) and force the leaf to 0700."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    _repair_mode(path, 0o700)
    return path


def ensure_state_dir(*parts: str) -> Path:
    """Create and mode-repair the state root and each named level under it.

    Everything under the state root is tgcli's property and guards account
    secrets (a `.session` file is full access to a Telegram account), so
    every level is forced to 0700. `mkdir(mode=...)` alone is not enough:
    the mode applies only to the leaf, is masked by umask, and
    `exist_ok=True` keeps a wrong mode on an existing directory.
    Directories above the state root are never touched — a custom
    TGCLI_STATE_DIR's parents are not ours.
    """
    path = private_dir(state_dir())
    for part in parts:
        path = private_dir(path / part)
    return path


def ensure_parent_dir(path: Path) -> Path:
    """Create ``path``'s directory: 0700 at every level under the state root
    (via `ensure_state_dir`), a plain `mkdir -p` anywhere else."""
    parent = path.parent
    try:
        relative = parent.relative_to(state_dir())
    except ValueError:
        parent.mkdir(parents=True, exist_ok=True)
        return parent
    return ensure_state_dir(*relative.parts)


def restrict_file(path: Path) -> None:
    """Force a tgcli state file to 0600, fail-open (missing file: no-op)."""
    _repair_mode(path, 0o600)


def create_private(path: Path) -> None:
    """Create `path` empty at 0600 if it is missing; an existing file is untouched.

    Call it before a library (SQLite, Telethon) creates the file, so a new
    file is never readable by others, not even until `restrict_file` runs.
    SQLite gives its -wal/-shm sidecars the database file's mode.
    """
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))


def open_private_append(path: Path) -> IO[str]:
    """Open `path` for appending text, creating it at 0600 if it is missing."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a")
