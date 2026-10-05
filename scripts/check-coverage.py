#!/usr/bin/env python3
"""Validate docs/FEATURES.md against the pinned Telethon TL namespaces."""

import argparse
import pkgutil
import re
import sys
from collections.abc import Set
from pathlib import Path

from telethon.tl import functions

from tgcli.commands.run import DENIED_NAMESPACES

DENIED_STATUS = "denied"
VALID_STATUSES = {"wrapped", "run", "excluded", DENIED_STATUS}
NAMESPACE_RE = re.compile(r"[a-z][a-z0-9]*")
PLANNED_STATUS_RE = re.compile(r"planned:[1-9][0-9]*")
DEFAULT_FEATURES = Path(__file__).resolve().parents[1] / "docs" / "FEATURES.md"


def discover_namespaces() -> set[str]:
    return {module.name for module in pkgutil.iter_modules(functions.__path__)}


def parse_matrix(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) != 3 or not NAMESPACE_RE.fullmatch(cells[0]):
            continue
        rows.append((cells[0], cells[1], cells[2]))
    return rows


def valid_status(status: str) -> bool:
    return status in VALID_STATUSES or bool(PLANNED_STATUS_RE.fullmatch(status))


def validate(
    features_path: Path,
    namespaces: set[str] | None = None,
    denied_namespaces: Set[str] | None = None,
) -> list[str]:
    namespaces = namespaces if namespaces is not None else discover_namespaces()
    denied_namespaces = (
        DENIED_NAMESPACES if denied_namespaces is None else denied_namespaces
    )
    errors = []
    seen = set()
    status_by_namespace: dict[str, str] = {}
    for namespace, status, notes in parse_matrix(features_path):
        if namespace in seen:
            errors.append(f"duplicate namespace: {namespace}")
        seen.add(namespace)
        status_by_namespace.setdefault(namespace, status)
        if namespace not in namespaces:
            errors.append(f"unknown namespace: {namespace}")
        if not valid_status(status):
            errors.append(f"invalid status for {namespace}: {status}")
        if status == "excluded" and not notes:
            errors.append(f"excluded namespace {namespace} needs a reason")
    for namespace in sorted(namespaces - seen):
        errors.append(f"missing namespace: {namespace}")
    matrix_denied = {
        namespace
        for namespace, status in status_by_namespace.items()
        if status == DENIED_STATUS
    }
    for namespace in sorted(denied_namespaces - matrix_denied):
        status = status_by_namespace.get(namespace, "missing")
        errors.append(f"tg run denies {namespace} but matrix status is {status}")
    for namespace in sorted(matrix_denied - denied_namespaces):
        errors.append(f"matrix marks {namespace} denied but tg run allows it")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Telethon namespace coverage")
    parser.add_argument("--features", type=Path, default=DEFAULT_FEATURES)
    args = parser.parse_args(argv)
    errors = validate(args.features)
    if errors:
        for error in errors:
            print(f"coverage error: {error}", file=sys.stderr)
        return 1
    print(f"coverage OK: {len(discover_namespaces())} namespaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
