#!/usr/bin/env python3
"""Publish thermos audit ticket bodies as GitHub issues.

Default: dry-run. Pass --apply to create (requires gh issues:write).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TICKETS = ROOT / "docs" / "thermos-audit-2026-08-13" / "tickets"
BACKLOG = "docs/thermos-audit-2026-08-13-backlog.md"


def parse_ticket(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SystemExit(f"no frontmatter: {path}")
    _, fm, body = text.split("---\n", 2)
    meta: dict[str, object] = {}
    for line in fm.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            items = [p.strip().strip("'\"") for p in raw[1:-1].split(",") if p.strip()]
            meta[key.strip()] = items
        else:
            meta[key.strip()] = raw.strip().strip("'\"")
    meta["body"] = body.lstrip()
    meta["path"] = path
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create GitHub issues (default is dry-run)",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        help="limit to ticket id(s), e.g. --only T01 --only T04",
    )
    args = parser.parse_args()

    if not TICKETS.is_dir():
        print(f"missing {TICKETS}", file=sys.stderr)
        return 1

    tickets = sorted(TICKETS.glob("T*.md"))
    if args.only:
        wanted = set(args.only)
        tickets = [p for p in tickets if p.name.split("-", 1)[0] in wanted]

    created = 0
    for path in tickets:
        ticket = parse_ticket(path)
        title = str(ticket["title"])
        labels = list(ticket["labels"])  # type: ignore[arg-type]
        tid = str(ticket["id"])
        priority = str(ticket["priority"])
        body = (
            f"<!-- thermos-audit-2026-08-13 {tid} {priority} -->\n\n"
            f"{ticket['body']}\n"
            f"---\n"
            f"Parent backlog: `{BACKLOG}`\n"
        )
        print(f"== {tid} ({priority}) ==")
        print(f"title: {title}")
        print(f"labels: {', '.join(labels)}")
        if not args.apply:
            print("(dry-run; pass --apply to create)\n")
            continue
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
            handle.write(body)
            body_path = Path(handle.name)
        cmd = [
            "gh",
            "issue",
            "create",
            "--title",
            title,
            "--body-file",
            str(body_path),
        ]
        for label in labels:
            cmd.extend(["--label", label])
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            print(result.stdout.strip() or "(created)")
            created += 1
        finally:
            body_path.unlink(missing_ok=True)
        print()

    if args.apply:
        print(f"created {created} issues")
    else:
        print(f"dry-run complete ({len(tickets)} tickets); re-run with --apply when gh has issues:write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
