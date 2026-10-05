"""Fail-closed architecture ownership and hotspot budget ratchet.

Budgets are ceilings, not baselines: a file may shrink freely, and only growth
past its reviewed ceiling fails. Lower a ceiling once a file settles under it.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

# Reviewed line ceilings for hot files. git blame holds why each one moved.
CEILINGS = {
    "src/tgcli/cli.py": 690,
    "src/tgcli/parser.py": 620,
    "src/tgcli/preflight.py": 250,
    "src/tgcli/archive/arguments.py": 140,
    "src/tgcli/archive/preflight.py": 170,
    "src/tgcli/archive/offline.py": 70,
    "src/tgcli/dispatch.py": 373,
    "src/tgcli/commands/batch.py": 100,
    "src/tgcli/commands/changes.py": 556,
    "src/tgcli/read_ops.py": 474,
    "src/tgcli/commands/clone.py": 1060,
    "src/tgcli/clone/state.py": 485,
    "src/tgcli/clone/quotes.py": 392,
    "src/tgcli/clone/quote_fallback.py": 127,
    "src/tgcli/clone/send.py": 290,
    "src/tgcli/archive/store.py": 96,
    "src/tgcli/archive/schema.py": 437,
    "src/tgcli/archive/messages.py": 147,
    "src/tgcli/archive/transcripts.py": 202,
    "src/tgcli/archive/sync_state.py": 350,
    "src/tgcli/archive/peers.py": 74,
    "src/tgcli/archive/private_enum.py": 170,
    "src/tgcli/archive/sync.py": 615,
    "src/tgcli/archive/backfill.py": 320,
    "src/tgcli/archive/transcribe.py": 251,
    "src/tgcli/archive/explore.py": 557,
    "src/tgcli/archive/search.py": 67,
    "src/tgcli/archive/media.py": 72,
    "src/tgcli/commands/archive.py": 544,
    "src/tgcli/commands/archive_jobs.py": 140,
    "src/tgcli/commands/jobs.py": 163,
    "src/tgcli/jobs/arguments.py": 79,
    "src/tgcli/jobs/model.py": 95,
    "src/tgcli/jobs/preflight.py": 108,
    "src/tgcli/jobs/db.py": 280,
    "src/tgcli/jobs/runner.py": 490,
    "src/tgcli/jobs/store.py": 670,
    "src/tgcli/governor/__init__.py": 14,
    "src/tgcli/governor/gate.py": 227,
    "src/tgcli/governor/ledger.py": 525,
    "src/tgcli/governor/pacing.py": 236,
    "src/tgcli/governor/probe.py": 86,
    "src/tgcli/governor/registry.py": 133,
    "src/tgcli/governor/seam.py": 66,
    "src/tgcli/commands/store.py": 537,
}

# Modules that must reach read commands only through the read_ops seam
# (ADR-0034). cli.py's split into parser/preflight/dispatch keeps the same
# invariant, so every piece of the old monolith stays covered.
READ_OWNERSHIP_MODULES = (
    "src/tgcli/cli.py",
    "src/tgcli/parser.py",
    "src/tgcli/preflight.py",
    "src/tgcli/dispatch.py",
    "src/tgcli/commands/batch.py",
)

EXCLUSIVE_READ_MODULES = {
    "dialogs",
    "identity",
    "info",
    "read",
    "search",
    "thread",
}


def _state_write_errors(path: Path, relative: str) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    errors: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "write_text"
        ):
            errors.append(
                f"{relative}:{node.lineno} calls write_text; state files must "
                "go through tgcli.atomic.replace_text"
            )
    return errors


def _import_from_module(node: ast.ImportFrom, package: tuple[str, ...]) -> str:
    if node.level == 0:
        return node.module or ""
    keep = len(package) - node.level + 1
    base = package[: max(keep, 0)]
    suffix = tuple((node.module or "").split(".")) if node.module else ()
    return ".".join((*base, *suffix))


def _read_ownership_errors(path: Path, relative: str) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    errors: set[str] = set()
    module_aliases: dict[str, str] = {}
    manifest_aliases: set[str] = set()
    batch_adapter = relative == "src/tgcli/commands/batch.py"
    package = ("tgcli", "commands") if batch_adapter else ("tgcli",)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ImportFrom, ast.Import)):
            continue
        imported_from = (
            _import_from_module(node, package)
            if isinstance(node, ast.ImportFrom)
            else None
        )
        if isinstance(node, ast.ImportFrom) and imported_from == "tgcli.commands":
            for alias in node.names:
                module = alias.name
                local = alias.asname or module
                module_aliases[local] = module
                if module in EXCLUSIVE_READ_MODULES or (
                    batch_adapter and module == "media"
                ):
                    errors.add(f"{relative} imports read command module {module}")
        elif isinstance(node, ast.ImportFrom) and imported_from.startswith(
            "tgcli.commands."
        ):
            module = imported_from.rsplit(".", 1)[1]
            if module in EXCLUSIVE_READ_MODULES:
                errors.add(f"{relative} imports read command module {module}")
            for alias in node.names:
                if module == "media" and alias.name == "manifest":
                    manifest_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            prefix = "tgcli.commands."
            for alias in node.names:
                if not alias.name.startswith(prefix):
                    continue
                module = alias.name.removeprefix(prefix)
                module_aliases[alias.asname or module] = module
                if module in EXCLUSIVE_READ_MODULES or (
                    batch_adapter and module == "media"
                ):
                    errors.add(f"{relative} imports read command module {module}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and module_aliases.get(node.func.value.id) == "media"
            and node.func.attr == "manifest"
        ) or (isinstance(node.func, ast.Name) and node.func.id in manifest_aliases):
            errors.add(f"{relative} dispatches shared read media.manifest")
    return errors


def check(root: Path) -> list[str]:
    errors: list[str] = []
    for relative, budget in CEILINGS.items():
        path = root / relative
        try:
            line_count = len(path.read_text().splitlines())
        except FileNotFoundError:
            errors.append(f"{relative} is missing")
            continue
        if line_count > budget:
            errors.append(
                f"{relative} has {line_count} lines; reviewed ceiling is {budget}"
            )

    for relative in READ_OWNERSHIP_MODULES:
        path = root / relative
        if not path.exists():
            continue
        errors.extend(sorted(_read_ownership_errors(path, relative)))

    # ADR-0107: deny bare write_text in every production module by default.
    # A newly added or renamed state writer is covered without registry upkeep.
    source_root = root / "src/tgcli"
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        errors.extend(_state_write_errors(path, relative))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    args = parser.parse_args(argv)
    errors = check(args.root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("architecture check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
