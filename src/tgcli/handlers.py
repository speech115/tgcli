"""Command handlers: turn a parsed namespace into one command module call.

Each function here is the `run` of one entry in `tgcli.registry`. Local
handlers take `(args)` or `(args, config)`; network handlers are coroutines
taking `(tg, args, account)`. All of them return `(data, rows)`.
"""

from __future__ import annotations

import asyncio

from tgcli import output, preview_commit, read_ops, safety, session
from tgcli.clone import sync as sync_clone
from tgcli.commands import (
    accounts as accounts_cmd,
    archive as archive_cmd,
    clone as clone_cmd,
    doctor as doctor_cmd,
    export as export_cmd,
    inbox as inbox_cmd,
    login as login_cmd,
    media as media_cmd,
    run as run_cmd,
    store as store_cmd,
    transcribe as transcribe_cmd,
)
from tgcli.config import resolve_account
from tgcli.errors import PolicyError
from tgcli.governor import pacing

# Local commands that need neither config nor Telegram.


def clone_status(args) -> tuple[dict, list[tuple]]:
    data = clone_cmd.list_clones(args.source)
    for entry in data["clones"]:
        if clone_cmd.half_initialized(entry):
            output.note(
                f"clone {entry['source']['id']} has comments enabled but no "
                "linked discussion group; finish it with: tg clone init "
                f"{entry['source']['id']}"
            )
    return data, clone_cmd.status_rows(data)


def clone_export_state(args) -> tuple[dict, list[tuple]]:
    data = clone_cmd.export_state(args.source)
    return data, []


def store_stats(args) -> tuple[dict, list[tuple]]:
    data = store_cmd.stats(session.state_dir())
    return data, store_cmd.stats_rows(data)


def store_cleanup(args) -> tuple[dict, list[tuple]]:
    data = store_cmd.cleanup(
        session.state_dir(),
        older_than=getattr(args, "older_than", None),
        include_pending=bool(getattr(args, "include_pending", False)),
        confirm=bool(args.confirm),
    )
    return data, store_cmd.cleanup_rows(data)


# Local commands that read the config.


def accounts_list(args, config) -> tuple[dict, list[tuple]]:
    data = accounts_cmd.list_accounts(config)
    return data, accounts_cmd.to_rows(data)


def accounts_show(args, config) -> tuple[dict, list[tuple]]:
    data = accounts_cmd.show_account(config, args.alias)
    return data, accounts_cmd.show_rows(data)


def accounts_remove(args, config) -> tuple[dict, list[tuple]]:
    if args.confirm:
        # Gated here, after the config loads, not in preflight: a missing
        # config has always been reported before the readonly refusal.
        safety.enforce_local_mutation_allowed(args.readonly)
    data = accounts_cmd.remove_account(
        config,
        args.alias,
        confirm=bool(args.confirm),
        keep_session=bool(getattr(args, "keep_session", False)),
        role=getattr(args, "remove_role", None),
    )
    return data, accounts_cmd.remove_rows(data)


def accounts_login(args, config) -> tuple[dict, list[tuple]]:
    if getattr(args, "continue_id", None):
        data = asyncio.run(
            login_cmd.continue_login(
                login_id=args.continue_id,
                code=getattr(args, "code", None),
                password_stdin=bool(getattr(args, "password_stdin", False)),
            )
        )
    else:
        data = asyncio.run(
            login_cmd.start_login(
                config,
                args.alias,
                phone=args.phone,
                api_id=getattr(args, "api_id", None),
                api_hash=getattr(args, "api_hash", None),
                force=bool(getattr(args, "force", False)),
                role=getattr(args, "login_role", None),
            )
        )
    return data, login_cmd.login_rows(data)


def doctor(args, config) -> tuple[dict, list[tuple]]:
    # Late import: cli owns the --timeout deadline and imports this module.
    from tgcli.cli import _run_with_deadline

    connect = bool(getattr(args, "connect", False))
    coro = doctor_cmd.run(config, args.account, connect=connect, readonly=args.readonly)
    if connect:
        data = _run_with_deadline(coro, args.timeout)
    else:
        data = asyncio.run(coro)
    return data, doctor_cmd.to_rows(data)


def inbox_ack(args, config) -> tuple[dict, list[tuple]]:
    account = resolve_account(config, args.account)
    data = inbox_cmd.ack(account.alias, args.token)
    return data, inbox_cmd.ack_rows(data)


# Network commands: one open session each.


async def read(tg, args, account) -> tuple[dict, list[tuple]]:
    operation = read_ops.from_cli(args)
    if operation is None:
        raise AssertionError(f"no read operation for {args.command}")
    result = await read_ops.execute(tg, operation)
    return result.data, result.rows


async def inbox_read(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await inbox_cmd.fetch_inbox(tg, account.alias, limit=args.limit)
    return data, inbox_cmd.to_rows(data)


async def handshake(tg, args, account) -> tuple[dict, list[tuple]]:
    result = await preview_commit.dispatch(tg, args, account)
    if result is None:
        raise AssertionError(f"no handshake registered for {args.command}")
    return result


async def transcribe(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await transcribe_cmd.transcribe_message(
        tg, args.chat, args.message_id, timeout=args.timeout
    )
    return data, transcribe_cmd.to_rows(data)


async def run(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await run_cmd.run(
        tg,
        args.run_code,
        account.alias,
        argv=args.script_args,
        write=args.write,
    )
    return data, []


async def export_messages(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await export_cmd.export_messages(
        tg,
        args.chat,
        args.output,
        limit=args.limit,
        after_id=getattr(args, "after_id", None),
        append=bool(getattr(args, "append", False)),
        resume=bool(getattr(args, "resume", False)),
    )
    return data, export_cmd.to_rows(data)


async def export_subscribers(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await export_cmd.export_subscribers(
        tg,
        args.channel,
        args.output,
        limit=args.limit,
        usernames_only=args.usernames_only,
    )
    return data, export_cmd.to_rows(data)


async def clone_sync(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await sync_clone.sync_text(tg, args.source, account.alias, limit=args.limit)
    return data, sync_clone.sync_rows(data)


async def archive_init(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.init_archive(tg, account.alias)
    return data, archive_cmd.init_rows(data)


async def archive_add(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.add_chat(tg, account.alias, args.chat)
    return data, archive_cmd.add_rows(data)


async def archive_remove(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.remove_chat(tg, account.alias, args.chat)
    return data, archive_cmd.remove_rows(data)


async def archive_backfill(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.backfill(
        tg,
        account.alias,
        list(getattr(args, "chats", None) or []),
        limit=args.limit,
        private=bool(getattr(args, "private", False)),
        max_dialogs=args.max_dialogs,
        should_stop=pacing.wall_clock_exhausted,
    )
    return data, archive_cmd.backfill_rows(data)


async def archive_sync(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.sync(
        tg,
        account.alias,
        max_events=args.max_events,
        max_dialogs=args.max_dialogs,
        max_media=args.max_media,
        should_stop=pacing.wall_clock_exhausted,
    )
    if data.get("remaining") and pacing.wall_clock_exhausted():
        data["stop_reason"] = "wall_clock_cap"
    return data, archive_cmd.sync_rows(data)


async def archive_rebaseline(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.rebaseline(tg, account.alias)
    return data, archive_cmd.rebaseline_rows(data)


async def media_download(tg, args, account) -> tuple[dict, list[tuple]]:
    message_ids_raw = getattr(args, "message_ids", None)
    bulk = bool(
        message_ids_raw
        or getattr(args, "media_type", None)
        or getattr(args, "since", None)
        or getattr(args, "download_limit", None) is not None
    )
    if bulk and args.message_id is not None:
        raise PolicyError("do not pass a single message_id with bulk media flags")
    if getattr(args, "codec", None) is not None and not media_cmd.is_story_link(
        args.source
    ):
        raise PolicyError("--codec applies to story sources only")

    def progress(current: int, total: int | None) -> None:
        output.note(f"downloaded {current}/{total if total is not None else '?'} bytes")

    if media_cmd.is_story_link(args.source):
        if bulk:
            raise PolicyError("story links support single download only")
        source = media_cmd.parse_source(args.source, args.message_id)
        data = await media_cmd.download_media(
            tg,
            source,
            account.alias,
            output=args.output,
            parallel=args.parallel,
            codec=args.codec,
            progress=progress,
        )
        return data, media_cmd.to_rows(data)

    if bulk:
        ids = media_cmd.parse_message_ids(message_ids_raw) if message_ids_raw else None
        data = await media_cmd.download_media_bulk(
            tg,
            args.source,
            account.alias,
            message_ids=ids,
            kind=getattr(args, "media_type", None),
            since=getattr(args, "since", None),
            limit=getattr(args, "download_limit", None),
            output=args.output,
            progress=progress,
        )
        return data, media_cmd.bulk_to_rows(data)

    source = media_cmd.parse_source(args.source, args.message_id)
    data = await media_cmd.download_media(
        tg,
        source,
        account.alias,
        output=args.output,
        parallel=args.parallel,
        codec=args.codec,
        progress=progress,
    )
    return data, media_cmd.to_rows(data)
