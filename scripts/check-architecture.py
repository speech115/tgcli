"""Fail-closed architecture ownership and hotspot budget ratchet.

Budgets are ceilings, not baselines: a file may shrink freely, and only growth
past its reviewed ceiling fails. Lower a ceiling once a file settles under it.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# ADR-0058: a file may exceed its reviewed ceiling by up to GRACE lines
# without failing the gate, so feature branches never edit ceilings and the
# shared-file conflict class disappears. The integrator trues the ceilings up
# at merge with --strict (zero grace). Growth past ceiling + GRACE still
# fails everywhere — the anti-bloat control is a band, not a hole.
GRACE = 50

# ADR-0057 raised five ceilings by the exact cost of the lint policy, never
# more: ruff's isort separates stdlib / third-party / first-party import
# blocks with a blank line, and E501 splits a handful of over-long lines.
# Layout only — no file gained a statement.
CEILINGS = {
    # +39 for the CONTRACT §2/§4 failure paths: --timeout expiry as TIMEOUT,
    # untranslated exceptions as one RUNTIME envelope, and a hung-up stdout
    # pipe leaving quietly instead of raising through the journal.
    # +signal handling and the whole-body deadline (1.2.16).
    # +22 for ADR-0062: the audit-role window wrapping the whole cli-level
    # mutation path (set/reset around _audit_before → network → _audit_after).
    # +21 for ADR-0068: offline archive list/status/search path in _execute.
    # +9 for ADR-0068 Phase 4: offline transcribe routing.
    # +24 for ADR-0068 Phase 5: offline read/history routing.
    # +4 for ADR-0070 Phase 6: refresh routing.
    # +50 for ADR-0072: the SIGALRM hang-detector re-arm that reads governed
    # sleep off pacing and extends the deadline instead of killing the run,
    # the governed-sleep-aware _run_with_deadline poll loop, the
    # _long_running_command exemption list (media/export/clone
    # init,sync,refresh/archive refresh), and the journal's
    # governed_sleep_ms/request_count/stop_reason fields.
    # +42 trued up at the 2.0.4 merge (ADR-0081/0083): the clone-status
    # pending-import and half-initialized stderr pointers, doctor's
    # readonly argument, and finish_commit for the clone commits.
    "src/tgcli/cli.py": 705,
    # +2 for ADR-0057: isort section blanks, E501 split in the --format help.
    # +47 for ADR-0062 --session-role / accounts --role flags and the
    # ADR-0063 tg changes subcommand surface.
    # +50 for ADR-0068: tg archive subcommand surface (init/add/remove/list/
    # status/backfill/search).
    # +30 for ADR-0068 Phase 3: --private/--max-dialogs backfill mode and the
    # sync/rebaseline subcommands.
    # +18 for ADR-0068 Phase 4: --max-media and the transcribe subcommand.
    # +39 for ADR-0068 Phase 5: search filter/sort/paging flags plus the
    # read and history subcommands.
    # +30 for ADR-0070 Phase 6: the refresh subcommand and its caps.
    # +8 for ADR-0072: the --max-runtime wall-clock cap flag.
    # +19 trued up at the 2.0.4 merge: `clone status --all` (ADR-0081)
    # on top of growth the grace band had been absorbing.
    "src/tgcli/parser.py": 759,
    # +40 for ADR-0062 role validation and ADR-0063 changes preflight.
    # +37 for ADR-0068: archive preflight (readonly gates, backfill/search
    # caps).
    # +25 for ADR-0068 Phase 3: private-mode validation and sync caps.
    # +21 for ADR-0068 Phase 4: media/transcribe cap validation.
    # +43 for ADR-0068 Phase 5: filter/date/sort/paging validation for
    # search, read, and history.
    # +10 for ADR-0070 Phase 6: refresh cap validation.
    # +13 for ADR-0072: --max-runtime and --timeout positivity validation.
    # +7 trued up at the 2.0.4 merge (ADR-0083): clone init/refresh moved
    # to the begin/finish preview handshake, folded into one loop.
    "src/tgcli/preflight.py": 447,
    # +2 for ADR-0057: isort section blanks.
    # +17 for ADR-0062: role lookup threaded into session.client.
    # +23 for ADR-0068: archive network dispatch (init/add/remove/backfill).
    # +16 for ADR-0068 Phase 3: sync/rebaseline dispatch.
    # +1 for ADR-0068 Phase 4: media budget threading.
    # +11 for ADR-0070 Phase 6: refresh dispatch.
    # +26 trued up at the 2.0.4 merge. Not caused by that release — the
    # grace band had been carrying it since earlier work; the ratchet is
    # the integrator's job and nobody had done it.
    "src/tgcli/dispatch.py": 353,
    "src/tgcli/commands/batch.py": 96,
    # +3 for ADR-0057: isort section blanks.
    "src/tgcli/read_ops.py": 437,
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
    # -397 for the 1.2.16 split into clone/cooldown.py, clone/reupload.py and
    # clone/init_peers.py: the ratchet tightens instead of loosening.
    # +29 for ADR-0060: export-state command, status schema_version/integrity,
    # store stats .db/WAL/SHM breakdown on the clone surface.
    "src/tgcli/commands/clone.py": 1230,
    # +21 for ADR-0055 pinned_dest_id / pin_occupied fields + validation.
    # +8 for id_map / retry_not_before validation on load (fail closed).
    # +48 for ADR-0060: the CloneState seam delegating to clone/statedb.py
    # (SQLite load/save/supersede, one-time JSON import, path_for .db).
    # +10 for ADR-0072: MAX_COOLDOWN_S migrated in-module off clone/flood.py,
    # plus the set_cooldown docstring noting the field is legacy now that
    # floods arm the governor's per-type cooldown instead.
    # +18 trued up at the 2.0.4 merge (ADR-0081): destination_title /
    # destination_username fields, their validation, and to_dict/from_dict.
    "src/tgcli/clone/state.py": 409,
    # +22 for ADR-0051: posts_cursor / posts_exhausted kwargs + deferred
    # short-circuit in resolve (mirror of transport.decide's deferred plan).
    # +1 for ADR-0061: the ResolveContext destination_group field.
    "src/tgcli/clone/quotes.py": 392,
    "src/tgcli/clone/quote_fallback.py": 127,
    # ADR-0068: the archive package grew across phases 0-4 untracked. Ceilings
    # are seeded at the phase-4 size so further growth is deliberate; store.py
    # is the one to split first if it keeps growing (schema + migrations +
    # message/transcript/scope/sync accessors all live there today).
    # +6 for the Phase 5 review fix (Unicode casefold helper). Still the
    # first split candidate if it grows again.
    "src/tgcli/archive/store.py": 1021,
    "src/tgcli/archive/sync.py": 596,
    # +6 for ADR-0072: backfill_dialogs/backfill_one/backfill_private thread
    # through pacing's rolling breadth budget, wall-clock cap, and
    # sleep_flood instead of clone.flood.WaitBudget, plus the
    # stop_reason/deferred/resume fields when a sweep stops early.
    "src/tgcli/archive/backfill.py": 320,
    "src/tgcli/archive/transcribe.py": 251,
    # ADR-0069: read-only query composition split out of store/commands so
    # Phase 5 SQL does not land in the persistence hotspot.
    "src/tgcli/archive/explore.py": 578,
    # search.py shrank to MATCH normalization + peer resolution once
    # explore.py superseded its query path (ADR-0069).
    "src/tgcli/archive/search.py": 78,
    # ADR-0070: the one-shot refresh composition and its media attempt
    # taxonomy, kept out of sync.py/store.py.
    # +50 for ADR-0072: sync_types_cooling's partial-cooldown defer path
    # (skip dispatch when a sync-relevant RPC type is cooling rather than
    # waking into the same refusal), the wall-clock-cap pre-check, and the
    # deferred/stop_reason/refresh reporting either path returns.
    "src/tgcli/archive/refresh.py": 196,
    "src/tgcli/archive/media.py": 72,
    # +7 for ADR-0072: skip get_me entirely on a scheduled wake into a known
    # cooldown (sync_types_cooling), reusing store_mod.read_meta's
    # account_user_id instead of an RPC (review fix M2).
    "src/tgcli/commands/archive_refresh.py": 118,
    # +1 for ADR-0072: cooldown_mod.cooled_account calls swapped for plain
    # tg.get_me(), plus stop_reason/deferred/resume reporting on
    # backfill_dialogs's chats-mode result.
    "src/tgcli/commands/archive.py": 546,
    # ADR-0072: the governor package (account-wide request pacing and
    # cooldowns around Telethon's private ``_call``) landed across phases
    # 0-2 with no ceilings at all; seed all seven modules at their current
    # size now so further growth here is deliberate.
    # __init__.py: package docstring plus the public re-export surface.
    "src/tgcli/governor/__init__.py": 14,
    # gate.py: the governed _call wrapper — refuse locally while cooling,
    # arm the cooldown from the server's own retry_after after a flood.
    "src/tgcli/governor/gate.py": 186,
    # ledger.py: persisted governor state (cooldowns, pacing reservations,
    # peer breadth) in SQLite under the state dir, ADR-0060's statedb.py
    # pattern, keyed by account_user_id.
    "src/tgcli/governor/ledger.py": 416,
    # pacing.py: sleep-before-dispatch pacing and the rolling breadth
    # budget — the start-to-start minimum interval per request type.
    "src/tgcli/governor/pacing.py": 231,
    # probe.py: the self-verifying probe that asks the server whether a
    # recorded cooldown deadline still holds, once per confirmed deadline.
    "src/tgcli/governor/probe.py": 86,
    # registry.py: which Telegram request type belongs to which paced
    # class, and the cooldown key every request type resolves to.
    "src/tgcli/governor/registry.py": 133,
    # seam.py: the governed seam itself, Telethon's private _call, plus
    # verify_seam's fail-fast check that it still has the expected shape.
    "src/tgcli/governor/seam.py": 66,
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


def check(root: Path, grace: int = GRACE) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for relative, budget in CEILINGS.items():
        path = root / relative
        try:
            line_count = len(path.read_text().splitlines())
        except FileNotFoundError:
            errors.append(f"{relative} is missing")
            continue
        if line_count > budget + grace:
            errors.append(
                f"{relative} has {line_count} lines; reviewed ceiling is {budget}"
            )
        elif line_count > budget:
            warnings.append(
                f"{relative} has {line_count} lines; over ceiling {budget} but "
                f"within grace +{grace} (the integrator ratchets at merge)"
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
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).parents[1])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="zero grace: fail on any growth past a ceiling (integrator "
        "merge-time true-up, ADR-0058)",
    )
    args = parser.parse_args(argv)
    errors, warnings = check(args.root, grace=0 if args.strict else GRACE)
    for warning in warnings:
        print(warning, file=sys.stderr)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("architecture check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
