"""Golden JSON files for the CLI's stdout: tests/snapshots/<name>.json.

A changed shape fails with a diff. Regenerate after an intended change with
`UPDATE_SNAPSHOTS=1 uv run pytest tests/test_json_snapshots.py` and review
the diff in git: the files are the JSON contract callers depend on.
"""

import json
import os
from pathlib import Path

SNAPSHOTS = Path(__file__).resolve().parent.parent / "snapshots"

# Values that differ on every run; the key stays so its presence is pinned.
VOLATILE = frozenset(
    {
        "preview_id",
        "expires_at",
        "created_at",
        "random_id",
        "path",
        "audit_path",
        "last_backfill_at",
        "rank",  # FTS5 bm25 score: varies with the SQLite build
    }
)


def scrub(value):
    if isinstance(value, dict):
        return {
            key: "<volatile>" if key in VOLATILE else scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def assert_snapshot(name: str, data) -> None:
    path = SNAPSHOTS / f"{name}.json"
    rendered = json.dumps(scrub(data), indent=2, sort_keys=True, ensure_ascii=False)
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.write_text(rendered + "\n")
        return
    assert path.exists(), f"no snapshot {path.name}; run with UPDATE_SNAPSHOTS=1"
    assert rendered == path.read_text().rstrip("\n"), f"{path.name} changed"
