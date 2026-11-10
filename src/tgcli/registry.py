"""Every `tg` command and what it needs, in one table.

`COMMANDS` maps each leaf of the parser (`"archive sync"`, `"send"`) to a
`Command`. The lifecycle (`cli`), preflight and network dispatch look a
command's properties up here instead of comparing `args.command`, so adding a
command is one entry plus its handler. `tests/test_command_classes.py` fails
the gate when a parser leaf has no entry or an entry has no leaf.

Preview → commit writes keep their own registry (`preview_commit.HANDSHAKES`);
their entry here routes to it.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from tgcli import handlers, preflight, preview_commit
from tgcli.archive import handlers as archive, preflight as archive_preflight
from tgcli.parser import command_key

Predicate = Callable[[argparse.Namespace], bool]
Hook = Callable[[argparse.ArgumentParser, argparse.Namespace], None]


def _always(args: argparse.Namespace) -> bool:
    return True


def _never(args: argparse.Namespace) -> bool:
    return False


@dataclass(frozen=True)
class Command:
    """What one command needs from the lifecycle.

    kind: `read` asks Telegram, `local-read` touches neither Telegram nor
      tgcli state, `write` changes either without a handshake, `handshake`
      writes through preview → commit.
    needs: `none` runs `run(args)` before the config is loaded, `config` runs
      `run(args, config)`, `network` awaits `run(tg, args, account)` in one
      session.
    gate: the readonly gate preflight enforces when `gated(args)` holds —
      `telegram` (readonly and TGCLI_NO_SEND) or `local` (readonly only).
      `handshake` is enforced by `preview_commit` on the commit leg and
      `handler` by the handler itself.
    deadline: the default `--timeout` (CONTRACT §1); None is no deadline.
    mutation_safe: open a session that never retries a request blind, for
      writes whose resend is not idempotent.
    prepare: argument checks run in preflight, after the gate.
    audit: details of one audit row written before the network call.
    emit: `rows` follows --json/--plain, `none` prints nothing (the command
      owns stdout).
    """

    kind: Literal["read", "local-read", "write", "handshake"]
    needs: Literal["none", "config", "network"]
    run: Callable
    gate: Literal["none", "telegram", "local", "handshake", "handler"] = "none"
    gated: Predicate = _always
    deadline: float | None | Callable[[argparse.Namespace], float | None] = 60.0
    mutation_safe: Predicate = _never
    prepare: Hook | None = None
    audit: Callable[[argparse.Namespace], dict] | None = None
    emit: Literal["rows", "none"] = "rows"

    def default_timeout(self, args: argparse.Namespace) -> float | None:
        return self.deadline(args) if callable(self.deadline) else self.deadline


def _then(*hooks: Hook) -> Hook:
    def run(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
        for hook in hooks:
            hook(parser, args)

    return run


def _args_only(check: Callable[[argparse.Namespace], None]) -> Hook:
    return lambda parser, args: check(args)


def _flag(name: str) -> Predicate:
    return lambda args: bool(getattr(args, name, None))


def _login_deadline(args: argparse.Namespace) -> float | None:
    # `--continue` waits on a person typing a code: no implicit deadline.
    return None if getattr(args, "continue_id", None) else 60.0


_READ = Command(kind="read", needs="network", run=handlers.read)
_HANDSHAKE = Command(
    kind="handshake",
    needs="network",
    run=handlers.handshake,
    gate="handshake",
    # The handshake registry names the commits that must not be retried blind.
    mutation_safe=preview_commit.mutation_safe,
)


def _archive_write(run: Callable, **kwargs) -> Command:
    return Command(kind="write", needs="network", run=run, gate="local", **kwargs)


def _archive_read(run: Callable, **kwargs) -> Command:
    return Command(kind="local-read", needs="config", run=run, **kwargs)


COMMANDS: dict[str, Command] = {
    "accounts list": Command(
        kind="local-read", needs="config", run=handlers.accounts_list
    ),
    "accounts show": Command(
        kind="local-read", needs="config", run=handlers.accounts_show
    ),
    "accounts remove": Command(
        kind="write",
        needs="config",
        run=handlers.accounts_remove,
        gate="handler",
        gated=_flag("confirm"),
    ),
    "accounts login": Command(
        kind="write",
        needs="config",
        run=handlers.accounts_login,
        gate="local",
        deadline=_login_deadline,
        prepare=preflight.prepare_login,
    ),
    # archive: local reads and writes of the archive store
    "archive history": _archive_read(
        archive.history,
        prepare=_args_only(archive_preflight.prepare_history),
    ),
    "archive list": _archive_read(archive.list_scope),
    "archive read": _archive_read(
        archive.read,
        prepare=_then(
            preflight.bounds("since", "until", "around_date"),
            _args_only(archive_preflight.prepare_read),
        ),
    ),
    "archive search": _archive_read(
        archive.search,
        prepare=_then(
            preflight.bounds("since", "until"),
            _args_only(archive_preflight.prepare_search),
        ),
    ),
    "archive status": _archive_read(archive.status),
    "archive transcribe": Command(
        kind="write",
        needs="config",
        run=archive.transcribe,
        gate="local",
        deadline=None,
        prepare=_args_only(archive_preflight.prepare_transcribe),
    ),
    "archive init": _archive_write(archive.init),
    "archive add": _archive_write(archive.add),
    "archive remove": _archive_write(archive.remove),
    "archive backfill": _archive_write(
        archive.backfill,
        deadline=None,
        prepare=_args_only(archive_preflight.apply_backfill),
    ),
    "archive sync": _archive_write(
        archive.sync,
        deadline=None,
        prepare=_args_only(archive_preflight.apply_sync),
    ),
    "archive rebaseline": _archive_write(archive.rebaseline),
    "clone status": Command(kind="local-read", needs="none", run=handlers.clone_status),
    "clone init": replace(_HANDSHAKE, deadline=None),
    "clone sync": Command(
        kind="write",
        needs="network",
        run=handlers.clone_sync,
        gate="telegram",
        deadline=None,
        mutation_safe=_always,
        prepare=preflight.limit("clone sync"),
    ),
    "draft list": _READ,
    "draft show": _READ,
    "draft set": _HANDSHAKE,
    "draft clear": _HANDSHAKE,
    # local housekeeping
    "doctor": Command(kind="local-read", needs="config", run=handlers.doctor),
    "store stats": Command(kind="local-read", needs="none", run=handlers.store_stats),
    "store cleanup": Command(
        kind="write",
        needs="none",
        run=handlers.store_cleanup,
        gate="local",
        gated=_flag("confirm"),
    ),
    # Telegram reads
    "contacts list": _READ,
    "contacts search": _READ,
    "count": _READ,
    "dialogs": replace(_READ, prepare=preflight.limit("dialogs", allow_zero=True)),
    "info": _READ,
    "latest": _READ,
    "message": _READ,
    "read": Command(
        kind="read",
        needs="network",
        run=handlers.read,
        prepare=_then(preflight.bounds("since", "until"), preflight.limit("read")),
    ),
    "resolve": _READ,
    "search": Command(
        kind="read",
        needs="network",
        run=handlers.read,
        prepare=preflight.prepare_search,
    ),
    "media manifest": Command(
        kind="read",
        needs="network",
        run=handlers.read,
        deadline=None,
        prepare=_then(preflight.bounds("since"), preflight.limit("media manifest")),
    ),
    # Network reads that write the caller's own output files
    "media download": Command(
        kind="read",
        needs="network",
        run=handlers.media_download,
        deadline=None,
        prepare=preflight.bounds("since"),
    ),
    "export messages": Command(
        kind="read",
        needs="network",
        run=handlers.export_messages,
        deadline=None,
        prepare=preflight.limit("export messages"),
    ),
    "export subscribers": Command(
        kind="read",
        needs="network",
        run=handlers.export_subscribers,
        deadline=None,
        prepare=preflight.limit("export subscribers"),
    ),
    # Telegram writes
    "send": _HANDSHAKE,
    "edit": _HANDSHAKE,
    "delete": _HANDSHAKE,
    "forward": _HANDSHAKE,
    "transcribe": Command(
        kind="write",
        needs="network",
        run=handlers.transcribe,
        # transcribeAudio is a server-side mutation (Premium quota, visible
        # to other clients): the --readonly gate applies (ADR-0079).
        gate="telegram",
        deadline=120.0,
        audit=lambda args: {"chat": args.chat, "message_id": args.message_id},
    ),
    "run": Command(
        kind="write",
        needs="network",
        run=handlers.run,
        gate="telegram",
        gated=_flag("write"),
        mutation_safe=_flag("write"),
        prepare=preflight.prepare_run,
        emit="none",
    ),
}


def lookup(args: argparse.Namespace) -> Command:
    key = command_key(args)
    try:
        return COMMANDS[key]
    except KeyError:
        raise AssertionError(f"unregistered command: {key}") from None
