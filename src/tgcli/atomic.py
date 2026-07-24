"""Atomic file replacement for state and config writers.

Every file under the config or state roots that is read back later must be
replaced atomically: a plain `write_text` can be observed half-written by a
concurrent invocation (the `store cleanup` vs live login race, fixed in
1.2.0, started exactly there). `scripts/check-architecture.py` bans
`write_text` in state-writing modules; this helper is the sanctioned path.
"""

import os
import tempfile
from pathlib import Path


def replace_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Atomically replace `path` with `text` at `mode`.

    Writes to a same-directory temp file, fsyncs, chmods, then `os.replace`s
    over the target, so readers only ever see the old or the new content.
    """
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
