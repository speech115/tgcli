"""Network dispatch: one open session, one command, one (data, rows) pair.

Read commands go through the `read_ops` seam (ADR-0034); everything else is
routed to its command module here. Telethon rate-limit errors are translated
into the CLI's own error contract at this boundary.
"""

from __future__ import annotations

from telethon import errors as telethon_errors

from tgcli import output, read_ops, safety, session
from tgcli.commands import (
    api as api_cmd,
    batch as batch_cmd,
    changes as changes_cmd,
    clone as clone_cmd,
    dialog as dialog_cmd,
    draft as draft_cmd,
    export as export_cmd,
    media as media_cmd,
    mutate as mutate_cmd,
    send as send_cmd,
)
from tgcli.errors import PolicyError, RateLimitError


async def run_network(args, account) -> tuple[dict, list[tuple]]:
    mutation_safe = args.command == "clone" and (
        args.clone_command == "sync"
        or (args.clone_command == "init" and args.commit is not None)
        or (args.clone_command == "refresh" and args.commit is not None)
    )
    role = getattr(args, "session_role", None)
    token = safety.set_audit_role(role)
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
            if args.command == "send":
                if args.preview:
                    data = await send_cmd.prepare(
                        tg,
                        args.chat,
                        args.text,
                        reply_to=args.reply_to,
                        file=args.file,
                        caption=args.caption,
                        topic=args.topic,
                        silent=args.silent,
                        fmt=args.format,
                    )
                else:
                    data = await send_cmd.commit(
                        tg,
                        args.commit,  # type: ignore  # preview load guards None
                        args.preview_payload,
                    )
                return data, send_cmd.to_rows(data)
            if args.command == "edit":
                if args.preview:
                    data = await mutate_cmd.prepare_edit(
                        tg, args.chat, args.message_id, args.text, args.format
                    )
                else:
                    data = await mutate_cmd.commit_edit(
                        tg,
                        args.commit,  # type: ignore  # preview load guards None
                        args.preview_payload,
                    )
                return data, mutate_cmd.to_rows(data)
            if args.command == "delete":
                if args.preview:
                    data = await mutate_cmd.prepare_delete(
                        tg, args.chat, args.message_id
                    )
                else:
                    data = await mutate_cmd.commit_delete(
                        tg,
                        args.commit,  # type: ignore  # preview load guards None
                        args.preview_payload,
                    )
                return data, mutate_cmd.to_rows(data)
            if args.command == "forward":
                if args.preview:
                    data = await mutate_cmd.prepare_forward(
                        tg, args.source, args.message_id, args.destination
                    )
                else:
                    data = await mutate_cmd.commit_forward(
                        tg,
                        args.commit,  # type: ignore  # preview load guards None
                        args.preview_payload,
                    )
                return data, mutate_cmd.to_rows(data)
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
            if args.command == "changes":
                data = await changes_cmd.run_changes(
                    tg,
                    cursor_text=getattr(args, "changes_cursor", None),
                    init=bool(getattr(args, "init", False)),
                    peers=getattr(args, "changes_peers", None),
                    drop_peers=getattr(args, "changes_drop_peers", None),
                    wait=getattr(args, "changes_wait", None),
                )
                return data, changes_cmd.to_rows(data)
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
            if args.command == "clone" and args.clone_command == "init":
                if args.commit:
                    data = await clone_cmd.commit_init(
                        tg, args.source, account.alias, args.preview_payload
                    )
                else:
                    data = await clone_cmd.preview_init(
                        tg,
                        args.source,
                        replace=args.replace,
                        no_comments=args.no_comments,
                    )
                return data, clone_cmd.init_rows(data)
            if args.command == "clone" and args.clone_command == "sync":
                data = await clone_cmd.sync_text(
                    tg, args.source, account.alias, limit=args.limit
                )
                return data, clone_cmd.sync_rows(data)
            if args.command == "clone" and args.clone_command == "refresh":
                if args.commit:
                    data = await clone_cmd.commit_refresh(
                        tg, args.source, account.alias, args.preview_payload
                    )
                else:
                    data = await clone_cmd.preview_refresh(tg, args.source)
                return data, clone_cmd.refresh_rows(data)
            if args.command == "draft":
                return await _run_draft(tg, args)
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
    finally:
        safety.reset_audit_role(token)


async def _run_draft(tg, args) -> tuple[dict, list[tuple]]:
    if args.draft_command == "set":
        if args.preview:
            data = await draft_cmd.prepare_set(
                tg,
                args.chat,
                args.text,
                fmt=args.format,
                reply_to=args.reply_to,
                topic=args.topic,
            )
        else:
            data = await draft_cmd.commit_set(
                tg,
                args.commit,  # type: ignore  # preview load guards None
                args.preview_payload,
            )
        return data, draft_cmd.mutation_to_rows(data)
    if args.draft_command == "clear":
        if args.preview:
            data = await draft_cmd.prepare_clear(tg, args.chat)
        else:
            data = await draft_cmd.commit_clear(
                tg,
                args.commit,  # type: ignore  # preview load guards None
                args.preview_payload,
            )
        return data, draft_cmd.mutation_to_rows(data)
    raise AssertionError(f"unhandled draft command: {args.draft_command}")


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

    def progress(current: int, total: int | None) -> None:
        output.note(f"downloaded {current}/{total if total is not None else '?'} bytes")

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
