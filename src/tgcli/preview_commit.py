"""One registry for preview-to-commit preflight, dispatch, audit, and finish."""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tgcli import safety
from tgcli.commands import (
    clone as clone_cmd,
    draft as draft_cmd,
    mutate as mutate_cmd,
    send as send_cmd,
)
from tgcli.config import Account
from tgcli.errors import PolicyError
from tgcli.parser import GLOBAL_DESTS

Run = Callable[[object, argparse.Namespace, Account], Awaitable[dict]]
Rows = Callable[[dict], list[tuple]]
BeforeAudit = Callable[[argparse.Namespace], dict]
AfterAudit = Callable[[argparse.Namespace, dict], dict]


@dataclass(frozen=True)
class Audit:
    before: BeforeAudit
    after: AfterAudit


@dataclass(frozen=True)
class Handshake:
    kind: str
    preview_call: Run
    commit_call: Run
    rows: Rows
    required: tuple[str, ...] = ()
    required_any: tuple[str, ...] = ()
    preview_error: str = ""
    requires_preview_flag: bool = True
    source_binding: str | None = None
    audit: Audit | None = None
    mutation_safe: bool = False


def _message_audit_before(args: argparse.Namespace) -> dict:
    details = {"preview_id": args.commit}
    if "random_id" in args.preview_payload:
        details["random_id"] = args.preview_payload["random_id"]
    return details


def _message_audit_after(args: argparse.Namespace, data: dict) -> dict:
    details = {"preview_id": args.commit, "message_id": data.get("message_id")}
    if "already_sent" in data:
        details["already_sent"] = data["already_sent"]
    return details


def _draft_audit_before(args: argparse.Namespace) -> dict:
    return {"preview_id": args.commit, "chat": args.preview_payload.get("chat")}


def _draft_audit_after(args: argparse.Namespace, data: dict) -> dict:
    return {
        "preview_id": args.commit,
        "chat": data.get("draft", {}).get("chat"),
    }


_MESSAGE_AUDIT = Audit(_message_audit_before, _message_audit_after)
_DRAFT_AUDIT = Audit(_draft_audit_before, _draft_audit_after)

HANDSHAKES = {
    ("send", None): Handshake(
        kind="send",
        preview_call=lambda tg, args, _account: send_cmd.prepare(
            tg,
            args.chat,
            args.text,
            reply_to=args.reply_to,
            file=args.file,
            caption=args.caption,
            topic=args.topic,
            silent=args.silent,
            fmt=args.format or "md",
        ),
        commit_call=lambda tg, args, _account: send_cmd.commit(
            tg,
            args.commit,
            args.preview_payload,
        ),
        rows=send_cmd.to_rows,
        required=("chat",),
        required_any=("text", "file"),
        preview_error=(
            "send requires CHAT (TEXT | --file PATH) --preview or --commit PREVIEW_ID"
        ),
        audit=_MESSAGE_AUDIT,
    ),
    ("edit", None): Handshake(
        kind="edit",
        preview_call=lambda tg, args, _account: mutate_cmd.prepare_edit(
            tg,
            args.chat,
            args.message_id,
            args.text,
            args.format or "plain",
        ),
        commit_call=lambda tg, args, _account: mutate_cmd.commit_edit(
            tg,
            args.commit,
            args.preview_payload,
        ),
        rows=mutate_cmd.to_rows,
        required=("chat", "message_id", "text"),
        preview_error=(
            "edit requires CHAT MESSAGE_ID TEXT --preview or --commit PREVIEW_ID"
        ),
        audit=_MESSAGE_AUDIT,
    ),
    ("delete", None): Handshake(
        kind="delete",
        preview_call=lambda tg, args, _account: mutate_cmd.prepare_delete(
            tg,
            args.chat,
            args.message_id,
        ),
        commit_call=lambda tg, args, _account: mutate_cmd.commit_delete(
            tg,
            args.commit,
            args.preview_payload,
        ),
        rows=mutate_cmd.to_rows,
        required=("chat", "message_id"),
        preview_error=(
            "delete requires CHAT MESSAGE_ID --preview or --commit PREVIEW_ID"
        ),
        audit=_MESSAGE_AUDIT,
    ),
    ("forward", None): Handshake(
        kind="forward",
        preview_call=lambda tg, args, _account: mutate_cmd.prepare_forward(
            tg,
            args.source,
            args.message_id,
            args.destination,
        ),
        commit_call=lambda tg, args, _account: mutate_cmd.commit_forward(
            tg,
            args.commit,
            args.preview_payload,
        ),
        rows=mutate_cmd.to_rows,
        required=("source", "message_id", "destination"),
        preview_error=(
            "forward requires SOURCE MESSAGE_ID DESTINATION --preview "
            "or --commit PREVIEW_ID"
        ),
        audit=_MESSAGE_AUDIT,
    ),
    ("clone", "init"): Handshake(
        kind="clone-init",
        preview_call=lambda tg, args, _account: clone_cmd.preview_init(
            tg,
            args.source,
            replace=args.replace,
            no_comments=args.no_comments,
        ),
        commit_call=lambda tg, args, account: clone_cmd.commit_init(
            tg,
            args.source,
            account.alias,
            args.preview_payload,
        ),
        rows=clone_cmd.init_rows,
        requires_preview_flag=False,
        source_binding="source",
        mutation_safe=True,
    ),
    ("draft", "set"): Handshake(
        kind="draft-set",
        preview_call=lambda tg, args, _account: draft_cmd.prepare_set(
            tg,
            args.chat,
            args.text,
            fmt=args.format or "md",
            reply_to=args.reply_to,
            topic=args.topic,
        ),
        commit_call=lambda tg, args, _account: draft_cmd.commit_set(
            tg,
            args.commit,
            args.preview_payload,
        ),
        rows=draft_cmd.mutation_to_rows,
        required=("chat", "text"),
        preview_error=("draft set requires CHAT TEXT --preview or --commit PREVIEW_ID"),
        audit=_DRAFT_AUDIT,
    ),
    ("draft", "clear"): Handshake(
        kind="draft-clear",
        preview_call=lambda tg, args, _account: draft_cmd.prepare_clear(
            tg,
            args.chat,
        ),
        commit_call=lambda tg, args, _account: draft_cmd.commit_clear(
            tg,
            args.commit,
            args.preview_payload,
        ),
        rows=draft_cmd.mutation_to_rows,
        required=("chat",),
        preview_error=("draft clear requires CHAT --preview or --commit PREVIEW_ID"),
        audit=_DRAFT_AUDIT,
    ),
}


def spec_for(args: argparse.Namespace) -> Handshake | None:
    command = args.command
    subcommand = getattr(args, f"{command}_command", None)
    return HANDSHAKES.get((command, subcommand))


def is_commit(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "commit", None))


def prepare(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Validate one registered handshake and begin a commit before config I/O."""
    spec = spec_for(args)
    if spec is None:
        return
    if is_commit(args):
        if _preview_arguments(args, spec):
            parser.error(
                f"{spec.kind.replace('-', ' ')} --commit accepts only a preview id"
            )
        safety.enforce_mutation_allowed(args.readonly)
        args.preview_payload = safety.begin_commit(args.commit, expected_kind=spec.kind)
        if spec.source_binding is not None:
            expected = getattr(args, spec.source_binding)
            if args.preview_payload.get(spec.source_binding) != expected:
                command = " ".join(
                    part for part in (args.command, spec.kind.split("-")[-1])
                )
                raise PolicyError(
                    f"{command} preview does not match this {spec.source_binding}"
                )
        return

    valid = (
        (not spec.requires_preview_flag or bool(getattr(args, "preview", False)))
        and all(_present(getattr(args, name, None)) for name in spec.required)
        and (
            not spec.required_any
            or any(_present(getattr(args, name, None)) for name in spec.required_any)
        )
    )
    if not valid:
        parser.error(spec.preview_error)


def _preview_arguments(args: argparse.Namespace, spec: Handshake) -> list[str]:
    """Command arguments typed beside `--commit`; the preview already fixed them.

    Derived from the parsed namespace, not listed per command: every argument
    of a handshake subcommand defaults to None/False, so anything else was
    typed and would be silently ignored by the commit.
    """
    keep = GLOBAL_DESTS | {"command", f"{args.command}_command", "commit"}
    if spec.source_binding is not None:
        keep |= {spec.source_binding}
    return [
        name
        for name, value in vars(args).items()
        if name not in keep and _present(value)
    ]


def mutation_safe(args: argparse.Namespace) -> bool:
    spec = spec_for(args)
    return (
        spec is not None
        and spec.mutation_safe
        and getattr(args, "commit", None) is not None
    )


async def dispatch(
    tg, args: argparse.Namespace, account: Account
) -> tuple[dict, list[tuple]] | None:
    """Invoke the registered preview or commit call and project its rows."""
    spec = spec_for(args)
    if spec is None:
        return None
    call = spec.commit_call if is_commit(args) else spec.preview_call
    data = await call(tg, args, account)
    return data, spec.rows(data)


def audit_before(args: argparse.Namespace, account: Account) -> None:
    spec = spec_for(args)
    if spec is None or not is_commit(args) or spec.audit is None:
        return
    safety.append_audit(
        spec.kind,
        account.alias,
        spec.audit.before(args),
    )


def audit_after(args: argparse.Namespace, account: Account, data: dict) -> None:
    """Write the result audit before spending a successfully committed preview."""
    spec = spec_for(args)
    if spec is None or not is_commit(args):
        return
    if spec.audit is not None:
        safety.append_audit(
            f"{spec.kind}-result",
            account.alias,
            spec.audit.after(args, data),
        )
    safety.finish_commit(args.commit)


def _present(value: object) -> bool:
    return value is not None and value is not False
