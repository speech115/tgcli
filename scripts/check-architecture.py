"""Fail-closed architecture ownership and hotspot budget ratchet.

Budgets are ceilings, not baselines: a file may shrink freely, and only growth
past its reviewed ceiling fails. Lower a ceiling once a file settles under it.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path


CEILINGS = {
    # +39 for the CONTRACT §2/§4 failure paths: --timeout expiry as TIMEOUT,
    # untranslated exceptions as one RUNTIME envelope, and a hung-up stdout
    # pipe leaving quietly instead of raising through the journal.
    "src/tgcli/cli.py": 527,
    "src/tgcli/parser.py": 516,
    "src/tgcli/preflight.py": 254,
    "src/tgcli/dispatch.py": 257,
    "src/tgcli/commands/batch.py": 96,
    "src/tgcli/read_ops.py": 434,
    # +20 for ADR-0049: the progress emitter lives in clone/progress.py, but
    # the reporter still has to be threaded down the sync → batch → transfer
    # call chain that clone.py owns.
    # +5 for the comments-unstarted warning: the text and its condition live in
    # clone/progress.py, but sync_text owns both moments worth warning at —
    # before the work (a FloodWait exit never reaches the tail) and after it.
    # +9 for ADR-0051 task 1: pass posts_cursor into decide/resolve and stop
    # copy_batch on deferred (never plant a flat cross-leg reply).
    # +32 for ADR-0051 tasks 2–4: windowed loop, posts_exhausted threading.
    # +12 for ADR-0052 task 1: cooldown callers pass zero-arg thunks so a
    # FloodWait retry can rebuild a fresh awaitable.
    # +15 for ADR-0052 task 2: short FloodWait arm-then-sleep-then-retry.
    # +21 for ADR-0052 task 3: per-process WaitBudget threaded with clone_state.
    # +18 for ADR-0052 task 4: persistent reupload media cache (no TemporaryDirectory).
    # +3 for PR #76 review: unlink stale cache path before striped re-download.
    # +17 for ADR-0055 pin wiring into sync_text.
    # +162 for ADR-0054 refresh preview/commit/rows on the clone surface.
    # +15 for PR #77 review fix: commit_refresh binds account/source_peer/id_map.
    # +6 for the integration: refresh's cooldown seams take the ADR-0052 thunk
    # and a per-process WaitBudget (three get_messages call sites wrapped).
    # +18 for the shared FloodGate: one short flood wait stalls every sibling
    # upload worker instead of each sleeping and charging the budget again.
    # +6 for ADR-0050 parity in refresh: preview and commit thread me and
    # source_kind into the renderer so non-broadcast clones keep author_of.
    "src/tgcli/commands/clone.py": 1597,
    # +21 for ADR-0055 pinned_dest_id / pin_occupied fields + validation.
    # +8 for id_map / retry_not_before validation on load (fail closed).
    "src/tgcli/clone/state.py": 332,
    # +22 for ADR-0051: posts_cursor / posts_exhausted kwargs + deferred
    # short-circuit in resolve (mirror of transport.decide's deferred plan).
    "src/tgcli/clone/quotes.py": 391,
    "src/tgcli/clone/quote_fallback.py": 127,
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

# Modules that write files read back later under the config/state roots must
# replace them atomically via tgcli.atomic — a bare `write_text` can be seen
# half-written by a concurrent invocation (the 1.2.0 store-cleanup vs live
# login race). Exports and probe files (media, doctor) are exempt: nothing
# re-reads them as state.
STATE_WRITER_MODULES = (
    "src/tgcli/safety.py",
    "src/tgcli/login_state.py",
    "src/tgcli/resolve_phone.py",
    "src/tgcli/config.py",
    "src/tgcli/commands/accounts.py",
    "src/tgcli/commands/media.py",
    "src/tgcli/commands/store.py",
    "src/tgcli/clone/state.py",
    "src/tgcli/clone/flood.py",
)


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

    for relative in STATE_WRITER_MODULES:
        path = root / relative
        if not path.exists():
            errors.append(
                f"{relative} is missing; STATE_WRITER_MODULES must list real "
                "modules (renaming one silently drops its write_text ban)"
            )
            continue
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
