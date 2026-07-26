"""Offline benchmark: clone-state persistence, JSON-rewrite vs SQLite/WAL.

Evidence for the bf-19 finding (audit 2026-07-26): `clone/state.py::save()`
rewrites the entire state file after every copied message, so total disk
I/O over a clone grows quadratically with message count. This script
measures today's exact write path (real `CloneState.to_dict()` + real
`tgcli.atomic.replace_text`, fsync included) against a SQLite/WAL
prototype (one INSERT per mapping + cursor update, synchronous=NORMAL),
plus the resume-time load cost of each.

Offline by design: no Telegram, no session files, everything under a
temp directory. Run: `uv run python scripts/bench-clone-state.py`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgcli import atomic  # noqa: E402
from tgcli.clone.state import VERSION, CloneState  # noqa: E402


def _base_state() -> CloneState:
    return CloneState(
        version=VERSION,
        account_user_id=1,
        source_peer_id=100,
        source_title="bench",
        created_at="2026-07-26T00:00:00+00:00",
        destination_peer_id=200,
        cursor=0,
    )


def bench_json(n: int, root: Path) -> tuple[float, int, float]:
    """Today's path: whole-file atomic rewrite per message."""
    path = root / "clone.json"
    state = _base_state()
    written = 0
    start = time.perf_counter()
    for i in range(1, n + 1):
        state.id_map[str(i)] = i
        state.cursor = i
        text = json.dumps(state.to_dict())
        atomic.replace_text(path, text)
        written += len(text)
    elapsed = time.perf_counter() - start
    load_start = time.perf_counter()
    CloneState.from_dict(json.loads(path.read_text()))
    load = time.perf_counter() - load_start
    return elapsed, written, load


def bench_sqlite(n: int, root: Path) -> tuple[float, int, float]:
    """Candidate: WAL database, one transaction per message."""
    path = root / "clone.db"
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value)")
    db.execute(
        "CREATE TABLE id_map (source_id INTEGER PRIMARY KEY, dest_id INTEGER NOT NULL)"
    )
    db.commit()
    start = time.perf_counter()
    for i in range(1, n + 1):
        with db:
            db.execute("INSERT INTO id_map VALUES (?, ?)", (i, i))
            db.execute("REPLACE INTO meta VALUES ('cursor', ?)", (i,))
    elapsed = time.perf_counter() - start
    db.close()
    load_start = time.perf_counter()
    db = sqlite3.connect(path)
    dict(db.execute("SELECT source_id, dest_id FROM id_map").fetchall())
    db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
    db.close()
    load = time.perf_counter() - load_start
    written = sum(f.stat().st_size for f in root.glob("clone.db*") if f.is_file())
    return elapsed, written, load


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json-sizes",
        type=int,
        nargs="+",
        default=[1000, 5000],
        help="message counts for the JSON path (quadratic: keep modest)",
    )
    parser.add_argument(
        "--sqlite-sizes", type=int, nargs="+", default=[1000, 5000, 10000, 50000]
    )
    args = parser.parse_args()

    def row(backend: str, n: int, wall: float, written: int, load: float) -> None:
        mb, ms = written / 1e6, load * 1e3
        print(f"{backend:<8} {n:>6} {wall:>8.2f} {mb:>11.1f} {ms:>8.1f}")

    print(f"{'backend':<8} {'msgs':>6} {'wall s':>8} {'MB written':>11} {'load ms':>8}")
    for backend, bench, sizes in (
        ("json", bench_json, args.json_sizes),
        ("sqlite", bench_sqlite, args.sqlite_sizes),
    ):
        for n in sizes:
            root = Path(tempfile.mkdtemp(prefix=f"bench-{backend}-"))
            try:
                row(backend, n, *bench(n, root))
            finally:
                shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
