"""Network dispatch: one open session, one command, one (data, rows) pair.

Read commands go through the `read_ops` seam (ADR-0034); everything else is
routed to its command module here. Telethon rate-limit errors are translated
into the CLI's own error contract at this boundary.
"""

from __future__ import annotations

from telethon import errors as telethon_errors

from tgcli import changes_cursor, output, preview_commit, read_ops, session
from tgcli.commands import (
    api as api_cmd,
    archive as archive_cmd,
    batch as batch_cmd,
    changes as changes_cmd,
    clone as clone_cmd,
    dialog as dialog_cmd,
    export as export_cmd,
    media as media_cmd,
    mutate as mutate_cmd,
    run as run_cmd,
    transcribe as transcribe_cmd,
)
from tgcli.errors import PolicyError, RateLimitError
from tgcli.governor import pacing


async def run_network(args, account) -> tuple[dict, list[tuple]]:
    mutation_safe = (
        preview_commit.mutation_safe(args)
        or (args.command == "clone" and args.clone_command == "sync")
        or (args.command == "run" and args.write)
    )
    # cli._execute already holds the audit-role context var for the whole
    # invocation (including this coroutine); only the network session itself
    # needs the role here.
    role = getattr(args, "session_role", None)
    try:
        async with session.client(
            account, mutation_safe=mutation_safe, role=role
        ) as tg:
            read_operation = read_ops.from_cli(args)
            if read_operation is not None:
                result = await read_ops.execute(tg, read_operation)
                return result.data, result.rows
            if args.command == "batch":
                ops = batch_cmd.parse_ops(args.batch_lines)
                results, first_exit = await batch_cmd.run_batch(
                    tg, ops, fail_fast=bool(getattr(args, "fail_fast", False))
                )
                return {"_batch_results": results, "_batch_exit": first_exit}, []
            if args.command == "media" and args.media_command == "download":
                return await _download_media(tg, args, account)
            if args.command == "transcribe":
                data = await transcribe_cmd.transcribe_message(
                    tg, args.chat, args.message_id, timeout=args.timeout
                )
                return data, transcribe_cmd.to_rows(data)
            handshake = await preview_commit.dispatch(tg, args, account)
            if handshake is not None:
                return handshake
            if args.command == "mark-read":
                data = await mutate_cmd.mark_read(tg, args.chat)
                return data, mutate_cmd.to_rows(data)
            if args.command == "mark-unread":
                data = await mutate_cmd.mark_unread(tg, args.chat)
                return data, mutate_cmd.to_rows(data)
            if args.command == "dialog":
                return await _run_dialog(tg, args)
            if args.command == "api":
                return await api_cmd.call(tg, args.method, args.params), []
            if args.command == "run":
                data = await run_cmd.run(
                    tg,
                    args.run_code,
                    account.alias,
                    argv=args.script_args,
                    write=args.write,
                )
                return data, []
            if args.command == "changes":
                data = await changes_cmd.run_changes(
                    tg,
                    cursor_text=getattr(args, "changes_cursor", None),
                    binding_key=changes_cursor.account_binding_key(
                        alias=account.alias,
                        api_id=account.api_id,
                        api_hash=account.api_hash,
                    ),
                    init=bool(getattr(args, "init", False)),
                    peers=getattr(args, "changes_peers", None),
                    drop_peers=getattr(args, "changes_drop_peers", None),
                    wait=getattr(args, "changes_wait", None),
                )
                return data, changes_cmd.to_rows(data)
            if args.command == "archive":
                return await _run_archive(tg, args, account)
            if args.command == "export":
                if args.export_kind == "messages":
                    data = await export_cmd.export_messages(
                        tg,
                        args.chat,
                        args.output,
                        limit=args.limit,
                        after_id=getattr(args, "after_id", None),
                        append=bool(getattr(args, "append", False)),
                        resume=bool(getattr(args, "resume", False)),
                    )
                else:
                    data = await export_cmd.export_subscribers(
                        tg, args.channel, args.output, limit=args.limit
                    )
                return data, export_cmd.to_rows(data)
            if args.command == "clone" and args.clone_command == "sync":
                data = await clone_cmd.sync_text(
                    tg, args.source, account.alias, limit=args.limit
                )
                return data, clone_cmd.sync_rows(data)
            raise AssertionError(f"unhandled network command: {args.command}")
    except telethon_errors.TakeoutInitDelayError as exc:
        raise RateLimitError(
            f"takeout is unavailable for {exc.seconds}s; retry after {exc.seconds}s",
            retry_after=exc.seconds,
        ) from exc
    except telethon_errors.FloodWaitError as exc:
        raise RateLimitError(
            f"rate limited for {exc.seconds}s", retry_after=exc.seconds
        ) from exc


async def _run_archive(tg, args, account) -> tuple[dict, list[tuple]]:
    cmd = args.archive_command
    alias = account.alias
    if cmd == "init":
        data = await archive_cmd.init_archive(tg, alias)
        return data, archive_cmd.init_rows(data)
    if cmd == "add":
        data = await archive_cmd.add_chat(tg, alias, args.chat)
        return data, archive_cmd.add_rows(data)
    if cmd == "remove":
        data = await archive_cmd.remove_chat(tg, alias, args.chat)
        return data, archive_cmd.remove_rows(data)
    if cmd == "backfill":
        data = await archive_cmd.backfill(
            tg,
            alias,
            list(getattr(args, "chats", None) or []),
            limit=getattr(args, "limit", None),
            private=bool(getattr(args, "private", False)),
            max_dialogs=getattr(args, "max_dialogs", None),
            should_stop=pacing.wall_clock_exhausted,
        )
        return data, archive_cmd.backfill_rows(data)
    if cmd == "sync":
        data = await archive_cmd.sync(
            tg,
            alias,
            max_events=getattr(args, "max_events", None),
            max_dialogs=getattr(args, "max_dialogs", None),
            max_media=getattr(args, "max_media", None),
            should_stop=pacing.wall_clock_exhausted,
        )
        if data.get("remaining") and pacing.wall_clock_exhausted():
            data["stop_reason"] = "wall_clock_cap"
        return data, archive_cmd.sync_rows(data)
    if cmd == "rebaseline":
        data = await archive_cmd.rebaseline(tg, alias)
        return data, archive_cmd.rebaseline_rows(data)
    raise AssertionError(f"unhandled network archive command: {cmd}")


async def _download_media(tg, args, account) -> tuple[dict, list[tuple]]:
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


async def _run_dialog(tg, args) -> tuple[dict, list[tuple]]:
    cmd = args.dialog_command
    if cmd in ("pin", "unpin"):
        data = await dialog_cmd.set_pinned(tg, args.chat, pinned=cmd == "pin")
    elif cmd in ("archive", "unarchive"):
        data = await dialog_cmd.set_archived(tg, args.chat, archived=cmd == "archive")
    elif cmd == "mute":
        data = await dialog_cmd.set_muted(
            tg,
            args.chat,
            muted=True,
            until=getattr(args, "until", None),
            forever=bool(getattr(args, "forever", False)),
        )
    elif cmd == "unmute":
        data = await dialog_cmd.set_muted(tg, args.chat, muted=False)
    else:
        raise AssertionError(f"unhandled dialog command: {cmd}")
    return data, dialog_cmd.to_rows(data)
