"""The tg argparse grammar: subparser tree only, no behaviour.

Kept apart from cli.py so the command surface can be read and changed without
touching process lifecycle, and from preflight.py so grammar-level shape checks
stay distinct from safety gates.
"""

from __future__ import annotations

import argparse
import functools
from pathlib import Path

from tgcli import __version__
from tgcli.archive import arguments as archive_arguments
from tgcli.formatting import FORMATS, mask_phones_in_text
from tgcli.messages import MEDIA_KINDS


class _Parser(argparse.ArgumentParser):
    """Every parser in the tree: flags must be spelled in full.

    argparse otherwise accepts any unique long-option prefix, so `--c` reaches
    `--confirm` and `--w` reaches `--write` — the gates that exist precisely
    because a mutation has to be typed deliberately. `add_subparsers` defaults
    `parser_class` to `type(self)`, so every subparser inherits this.

    Parse errors quote the caller's argv, and argparse writes them to stderr
    itself, past `output.emit_error`; they are masked here so a phone typed as
    an argument stays masked too (the CLI contract).
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        super().error(mask_phones_in_text(message))


def _global_flags() -> _Parser:
    global_flags = _Parser(add_help=False, argument_default=argparse.SUPPRESS)
    global_flags.add_argument("--account", help="account alias from config")
    global_flags.add_argument(
        "--session-role",
        dest="session_role",
        metavar="NAME",
        help="named session role beside the primary",
    )
    global_flags.add_argument("--json", action="store_true", help="JSON to stdout")
    global_flags.add_argument("--plain", action="store_true", help="TSV to stdout")
    global_flags.add_argument("--readonly", action="store_true")
    global_flags.add_argument("--timeout", type=float)
    global_flags.add_argument(
        "--max-runtime",
        type=float,
        dest="max_runtime",
        metavar="SECONDS",
        help="wall-clock cap for long runs: exhausting it is a normal stop, "
        "exit 0 with a resume pointer",
    )
    global_flags.add_argument("-v", "--verbose", action="store_true")
    return global_flags


GLOBAL_DESTS = frozenset(action.dest for action in _global_flags()._actions)


def build_parser() -> argparse.ArgumentParser:
    global_flags = _global_flags()
    parser = _Parser(
        prog="tg", description="Stateless Telegram CLI", parents=[global_flags]
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_accounts = sub.add_parser(
        "accounts", help="Manage accounts", parents=[global_flags]
    )
    accounts_sub = p_accounts.add_subparsers(dest="subcommand", required=True)
    accounts_sub.add_parser(
        "list", help="List configured accounts", parents=[global_flags]
    )
    p_show = accounts_sub.add_parser(
        "show",
        help="Show offline account and session status",
        parents=[global_flags],
    )
    p_show.add_argument("alias", metavar="ALIAS")
    p_remove = accounts_sub.add_parser(
        "remove",
        help="Remove a configured account",
        parents=[global_flags],
    )
    p_remove.add_argument("alias", metavar="ALIAS")
    p_remove.add_argument(
        "--confirm",
        action="store_true",
        help="actually remove; without this, refuse",
    )
    p_remove.add_argument(
        "--keep-session",
        action="store_true",
        help="leave the session file and .bak in place",
    )
    p_remove.add_argument(
        "--role",
        dest="remove_role",
        metavar="NAME",
        help="remove one named session role without touching config or primary",
    )
    p_login = accounts_sub.add_parser(
        "login",
        help="Authorize a session by phone + confirmation code",
        parents=[global_flags],
    )
    p_login.add_argument("alias", nargs="?", metavar="ALIAS")
    p_login.add_argument(
        "--continue",
        dest="continue_id",
        metavar="LOGIN_ID",
        help="resume a pending login attempt",
    )
    p_login.add_argument("--phone", help="phone number for authorization")
    p_login.add_argument("--api-id", dest="api_id", type=int)
    p_login.add_argument("--api-hash", dest="api_hash")
    p_login.add_argument(
        "--force",
        action="store_true",
        help="replace a still-authorized session",
    )
    p_login.add_argument(
        "--code",
        help="confirmation code, or - to read one line from stdin",
    )
    p_login.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the cloud password from stdin (never argv)",
    )
    p_login.add_argument(
        "--role",
        dest="login_role",
        metavar="NAME",
        help="authorize a named session role beside the primary",
    )

    p_dialogs = sub.add_parser("dialogs", help="List dialogs", parents=[global_flags])
    p_dialogs.add_argument("--limit", type=int, default=50)
    p_dialogs.add_argument("--unread-only", action="store_true")
    p_dialogs.add_argument("--kind", choices=["user", "group", "channel"])

    p_doctor = sub.add_parser(
        "doctor", help="Check environment and session health", parents=[global_flags]
    )
    p_doctor.add_argument(
        "--connect",
        action="store_true",
        help="also probe Telegram authorization (live)",
    )

    archive_arguments.add_to(sub, global_flags)

    p_read = sub.add_parser(
        "read", help="Read recent messages from a dialog", parents=[global_flags]
    )
    p_read.add_argument("chat", help="@username, t.me link, or dialog id")
    p_read.add_argument("--limit", type=int, default=20)
    p_read.add_argument(
        "--before-id", type=int, help="only messages older than this id"
    )
    p_read.add_argument("--after-id", type=int, help="only messages newer than this id")
    p_read.add_argument("--since", help="ISO date/datetime lower bound")
    p_read.add_argument("--until", help="ISO date/datetime upper bound")
    p_read.add_argument("--topic", type=int, help="forum topic id")

    p_search = sub.add_parser(
        "search", help="Search messages in a dialog", parents=[global_flags]
    )
    p_search.add_argument("chat", nargs="?", help="@username, t.me link, or dialog id")
    p_search.add_argument("query", nargs="?")
    p_search.add_argument("--all", action="store_true")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--from", dest="from_user")
    p_search.add_argument("--since", help="ISO date/datetime lower bound")

    p_message = sub.add_parser(
        "message", help="Read one message by id", parents=[global_flags]
    )
    p_message.add_argument("chat", help="@username, t.me link, or dialog id")
    p_message.add_argument("message_id", type=int)
    p_message.add_argument("--context", type=int, default=0)

    p_info = sub.add_parser("info", help="Show dialog metadata", parents=[global_flags])
    p_info.add_argument("chat", help="@username, t.me link, or dialog id")
    p_info.add_argument("--full", action="store_true")

    p_resolve = sub.add_parser(
        "resolve",
        help="Resolve a phone, @username, link, or id to a peer",
        parents=[global_flags],
    )
    p_resolve.add_argument("ref", help="+phone, @username, t.me link, or dialog id")

    p_media = sub.add_parser(
        "media", help="Inspect or download message media", parents=[global_flags]
    )
    media_sub = p_media.add_subparsers(dest="media_command", required=True)
    p_download = media_sub.add_parser("download", parents=[global_flags])
    p_download.add_argument("source", help="t.me link or chat reference")
    p_download.add_argument("message_id", nargs="?", type=int)
    p_download.add_argument(
        "--output", help="final output path or bulk output directory"
    )
    p_download.add_argument(
        "--parallel",
        type=int,
        default=4,
        help="chunk requests in flight for one large file (default 4)",
    )
    p_download.add_argument(
        "--message-ids",
        dest="message_ids",
        help="comma-separated message ids for bulk download (max 100)",
    )
    p_download.add_argument(
        "--type",
        dest="media_type",
        choices=list(MEDIA_KINDS),
        help="bulk: keep only this media kind",
    )
    p_download.add_argument(
        "--since", help="bulk: ISO 8601 lower bound on message date"
    )
    p_download.add_argument(
        "--limit",
        type=int,
        dest="download_limit",
        help="bulk filter mode max items (default 100, max 100)",
    )
    p_download.add_argument(
        "--codec",
        choices=("h264", "h265", "hevc", "av1"),
        help="story video: pick this encoding from document/alt_documents",
    )
    p_manifest = media_sub.add_parser(
        "manifest",
        help="List media in a chat without downloading",
        parents=[global_flags],
    )
    p_manifest.add_argument("source", help="@username, t.me link, or dialog id")
    p_manifest.add_argument(
        "--type",
        dest="media_type",
        choices=list(MEDIA_KINDS),
        help="keep only this media kind",
    )
    p_manifest.add_argument("--since", help="ISO 8601 lower bound on message date")
    p_manifest.add_argument("--limit", type=int, default=100)

    p_send = sub.add_parser(
        "send", help="Preview and commit a message", parents=[global_flags]
    )
    p_send.add_argument("chat", nargs="?", help="target for --preview")
    p_send.add_argument("text", nargs="?", help="message text for --preview")
    p_send.add_argument(
        "--format",
        choices=FORMATS,
        default=None,
        dest="format",
        help=(
            "rich-text format of TEXT/caption, default md "
            "(html supports quote/spoiler/custom emoji)"
        ),
    )
    p_send.add_argument("--preview", action="store_true")
    p_send.add_argument("--commit", metavar="PREVIEW_ID")
    p_send.add_argument("--reply-to", type=int, dest="reply_to")
    p_send.add_argument("--file")
    p_send.add_argument("--caption")
    p_send.add_argument("--topic", type=int)
    p_send.add_argument("--silent", action="store_true")

    p_edit = sub.add_parser(
        "edit", help="Preview and commit a message edit", parents=[global_flags]
    )
    p_edit.add_argument("chat", nargs="?")
    p_edit.add_argument("message_id", nargs="?", type=int)
    p_edit.add_argument("text", nargs="?")
    p_edit.add_argument(
        "--format",
        choices=FORMATS,
        default=None,
        dest="format",
        help=(
            "rich-text format of TEXT, default plain "
            "(html supports quote/spoiler/custom emoji)"
        ),
    )
    p_edit.add_argument("--preview", action="store_true")
    p_edit.add_argument("--commit", metavar="PREVIEW_ID")

    p_delete = sub.add_parser(
        "delete", help="Preview and commit a message deletion", parents=[global_flags]
    )
    p_delete.add_argument("chat", nargs="?")
    p_delete.add_argument("message_id", nargs="?", type=int)
    p_delete.add_argument("--preview", action="store_true")
    p_delete.add_argument("--commit", metavar="PREVIEW_ID")

    p_forward = sub.add_parser(
        "forward",
        help="Preview and commit forwarding a message to another chat",
        parents=[global_flags],
    )
    p_forward.add_argument("from_chat", nargs="?", help="source chat for --preview")
    p_forward.add_argument(
        "message_id", nargs="?", type=int, help="id of the message to forward"
    )
    p_forward.add_argument("chat", nargs="?", help="destination chat for --preview")
    p_forward.add_argument("--preview", action="store_true")
    p_forward.add_argument("--commit", metavar="PREVIEW_ID")
    p_forward.add_argument(
        "--copy",
        action="store_true",
        help='send as a new message, without the "Forwarded from" header',
    )
    p_forward.add_argument("--silent", action="store_true")

    p_run = sub.add_parser(
        "run",
        help="Run a Python script with the authenticated client",
        parents=[global_flags],
    )
    p_run.add_argument(
        "--write", action="store_true", help="allow requests that change Telegram"
    )
    p_run.add_argument("script", metavar="SCRIPT", help="script path, or - for stdin")
    p_run.add_argument("script_args", nargs=argparse.REMAINDER, metavar="ARGS")

    p_export = sub.add_parser(
        "export", help="Export Telegram data", parents=[global_flags]
    )
    export_sub = p_export.add_subparsers(dest="export_kind", required=True)
    p_export_messages = export_sub.add_parser("messages", parents=[global_flags])
    p_export_messages.add_argument("chat", help="@username, t.me link, or dialog id")
    p_export_messages.add_argument("--output", required=True, type=Path)
    p_export_messages.add_argument("--limit", type=int)
    p_export_messages.add_argument(
        "--after-id",
        type=int,
        dest="after_id",
        help="export only messages with id greater than this",
    )
    p_export_messages.add_argument(
        "--append",
        action="store_true",
        help="append JSONL (requires --after-id or --resume)",
    )
    p_export_messages.add_argument(
        "--resume",
        action="store_true",
        help="append from last JSONL message id in --output",
    )
    p_clone = sub.add_parser(
        "clone", help="Copy a supported chat", parents=[global_flags]
    )
    clone_sub = p_clone.add_subparsers(dest="clone_command", required=True)
    p_clone_status = clone_sub.add_parser("status", parents=[global_flags])
    p_clone_status.add_argument(
        "source", nargs="?", help="filter to one source (id or title substring)"
    )
    p_clone_init = clone_sub.add_parser("init", parents=[global_flags])
    p_clone_init.add_argument("source", help="source channel, supergroup, or dialog")
    p_clone_init.add_argument("--commit", metavar="PREVIEW_ID")
    p_clone_init.add_argument(
        "--replace",
        action="store_true",
        help="supersede an incompatible or stale clone: archive its state and "
        "start a fresh destination pair",
    )
    p_clone_init.add_argument(
        "--no-comments",
        action="store_true",
        help="create a posts-only clone without a discussion-group peer",
    )
    p_clone_sync = clone_sub.add_parser("sync", parents=[global_flags])
    clone_sync_what = p_clone_sync.add_mutually_exclusive_group(required=True)
    clone_sync_what.add_argument(
        "source", nargs="?", help="source channel, supergroup, or dialog"
    )
    clone_sync_what.add_argument(
        "--all",
        action="store_true",
        help="sync every clone of this account in one run",
    )
    p_clone_sync.add_argument("--limit", type=int)
    return parser


def command_path(parser: argparse.ArgumentParser, args: argparse.Namespace) -> str:
    """The subcommand words the caller chose, e.g. `archive sync`.

    Each level names its own dest (`command`, `store_command`, ...), so the
    path is read off the tree rather than from a list kept beside it.
    """
    words: list[str] = []
    current: argparse.ArgumentParser | None = parser
    while current is not None:
        action = next(
            (
                item
                for item in current._actions
                if isinstance(item, argparse._SubParsersAction)
            ),
            None,
        )
        if action is None:
            break
        name = getattr(args, action.dest, None)
        if name is None:
            break
        words.append(name)
        current = action.choices.get(name)
    return " ".join(words)


@functools.cache
def _tree() -> argparse.ArgumentParser:
    return build_parser()


def command_key(args: argparse.Namespace) -> str:
    """The full command path of a parsed namespace, e.g. `archive sync`.

    The key of `tgcli.registry.COMMANDS` and of the journal's `command` field;
    read off a cached parse tree so no module needs the caller's parser.
    """
    return command_path(_tree(), args)
