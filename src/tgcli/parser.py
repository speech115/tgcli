"""The tg argparse grammar: subparser tree only, no behaviour.

Kept apart from cli.py so the command surface can be read and changed without
touching process lifecycle, and from preflight.py so grammar-level shape checks
stay distinct from safety gates.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tgcli import __version__
from tgcli.commands import media as media_cmd, store as store_cmd


def _older_than_type(value: str):
    try:
        return store_cmd.parse_older_than(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    global_flags = argparse.ArgumentParser(
        add_help=False, argument_default=argparse.SUPPRESS
    )
    global_flags.add_argument("--account", help="account alias from config")
    global_flags.add_argument("--json", action="store_true", help="JSON to stdout")
    global_flags.add_argument("--plain", action="store_true", help="TSV to stdout")
    global_flags.add_argument("--readonly", action="store_true")
    global_flags.add_argument("--timeout", type=float)
    global_flags.add_argument("-v", "--verbose", action="store_true")

    parser = argparse.ArgumentParser(
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
    p_import = accounts_sub.add_parser(
        "import",
        help="Copy authorized sessions from the old stack",
        parents=[global_flags],
    )
    p_import.add_argument("aliases", nargs="*", metavar="ALIAS")
    p_import.add_argument("--source-root", type=Path, default=Path("~"))
    p_import.add_argument("--force", action="store_true")
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
    p_login = accounts_sub.add_parser(
        "login",
        help="Authorize a session (QR by default, --phone fallback)",
        parents=[global_flags],
    )
    p_login.add_argument("alias", nargs="?", metavar="ALIAS")
    p_login.add_argument(
        "--continue",
        dest="continue_id",
        metavar="LOGIN_ID",
        help="resume a pending login attempt",
    )
    p_login.add_argument("--phone", help="authorize by phone + confirmation code")
    p_login.add_argument("--api-id", dest="api_id", type=int)
    p_login.add_argument("--api-hash", dest="api_hash")
    p_login.add_argument(
        "--force",
        action="store_true",
        help="replace a still-authorized session",
    )
    p_login.add_argument(
        "--qr-format",
        choices=("link", "text"),
        default=argparse.SUPPRESS,
        help="QR payload shape (default: link)",
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

    p_store = sub.add_parser(
        "store", help="Inspect and clean local state", parents=[global_flags]
    )
    store_sub = p_store.add_subparsers(dest="store_command", required=True)
    store_sub.add_parser("stats", help="Inventory local state", parents=[global_flags])
    p_cleanup = store_sub.add_parser(
        "cleanup", help="Reap spent/expired previews", parents=[global_flags]
    )
    p_cleanup.add_argument(
        "--older-than",
        dest="older_than",
        type=_older_than_type,
        help="only artefacts older than N days (or Nd/Nh)",
    )
    p_cleanup.add_argument(
        "--include-pending",
        action="store_true",
        help="also remove .pending previews far past TTL",
    )
    p_cleanup.add_argument(
        "--confirm",
        action="store_true",
        help="actually delete; without this, dry-run only",
    )

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

    p_latest = sub.add_parser(
        "latest", help="Read the latest dialog message", parents=[global_flags]
    )
    p_latest.add_argument("chat", help="@username, t.me link, or dialog id")

    p_message = sub.add_parser(
        "message", help="Read one message by id", parents=[global_flags]
    )
    p_message.add_argument("chat", help="@username, t.me link, or dialog id")
    p_message.add_argument("message_id", type=int)
    p_message.add_argument("--context", type=int, default=0)

    p_info = sub.add_parser("info", help="Show dialog metadata", parents=[global_flags])
    p_info.add_argument("chat", help="@username, t.me link, or dialog id")
    p_info.add_argument("--full", action="store_true")

    p_count = sub.add_parser(
        "count", help="Count dialog messages", parents=[global_flags]
    )
    p_count.add_argument("chat", help="@username, t.me link, or dialog id")

    p_resolve = sub.add_parser(
        "resolve",
        help="Resolve a phone, @username, link, or id to a peer",
        parents=[global_flags],
    )
    p_resolve.add_argument("ref", help="+phone, @username, t.me link, or dialog id")

    p_mutual = sub.add_parser(
        "mutual-chats",
        help="List chats shared with a user",
        parents=[global_flags],
    )
    p_mutual.add_argument("ref", help="@username, t.me link, or user id")

    p_batch = sub.add_parser(
        "batch",
        help="Run read-only ops from JSONL stdin (ADR-0032)",
        parents=[global_flags],
    )
    p_batch.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after the first failed op",
    )

    p_thread = sub.add_parser(
        "thread",
        help="Read a reply chain (ancestors; optional replies)",
        parents=[global_flags],
    )
    p_thread.add_argument("chat")
    p_thread.add_argument("message_id", type=int)
    p_thread.add_argument(
        "--replies",
        action="store_true",
        help="include comment/forum replies when a cheap thread API exists",
    )
    p_thread.add_argument(
        "--depth",
        type=int,
        default=20,
        help="max ancestor steps (default 20, hard cap 100)",
    )
    p_thread.add_argument(
        "--limit",
        type=int,
        default=50,
        help="max replies when --replies is set (default 50)",
    )

    p_contacts = sub.add_parser(
        "contacts", help="List or search Telegram contacts", parents=[global_flags]
    )
    contacts_sub = p_contacts.add_subparsers(dest="contacts_command", required=True)
    contacts_sub.add_parser("list", parents=[global_flags])
    p_contacts_search = contacts_sub.add_parser("search", parents=[global_flags])
    p_contacts_search.add_argument(
        "query", help="case-insensitive substring over name/username"
    )
    p_contacts_search.add_argument(
        "--global",
        action="store_true",
        dest="use_global",
        help="search Telegram's global directory instead of local contacts",
    )

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
    p_download.add_argument("--parallel", type=int, default=1)
    p_download.add_argument(
        "--message-ids",
        dest="message_ids",
        help="comma-separated message ids for bulk download (max 100)",
    )
    p_download.add_argument(
        "--type",
        dest="media_type",
        choices=list(media_cmd.MEDIA_KINDS),
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
    p_manifest = media_sub.add_parser(
        "manifest",
        help="List media in a chat without downloading",
        parents=[global_flags],
    )
    p_manifest.add_argument("source", help="@username, t.me link, or dialog id")
    p_manifest.add_argument(
        "--type",
        dest="media_type",
        choices=list(media_cmd.MEDIA_KINDS),
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
        choices=("plain", "md", "html"),
        default="md",
        dest="format",
        help=(
            "rich-text format of TEXT/caption "
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
        choices=("plain", "md", "html"),
        default="plain",
        dest="format",
        help="rich-text format of TEXT (html supports quote/spoiler/custom emoji)",
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
        "forward", help="Preview and commit a forward", parents=[global_flags]
    )
    p_forward.add_argument("source", nargs="?")
    p_forward.add_argument("message_id", nargs="?", type=int)
    p_forward.add_argument("destination", nargs="?")
    p_forward.add_argument("--preview", action="store_true")
    p_forward.add_argument("--commit", metavar="PREVIEW_ID")

    p_mark_read = sub.add_parser(
        "mark-read", help="Mark a dialog as read", parents=[global_flags]
    )
    p_mark_read.add_argument("chat")

    p_mark_unread = sub.add_parser(
        "mark-unread", help="Mark a dialog as unread", parents=[global_flags]
    )
    p_mark_unread.add_argument("chat")

    p_dialog = sub.add_parser(
        "dialog", help="Change inbox dialog state", parents=[global_flags]
    )
    dialog_sub = p_dialog.add_subparsers(dest="dialog_command", required=True)
    p_dialog_pin = dialog_sub.add_parser(
        "pin", help="Pin a dialog", parents=[global_flags]
    )
    p_dialog_pin.add_argument("chat")
    p_dialog_unpin = dialog_sub.add_parser(
        "unpin", help="Unpin a dialog", parents=[global_flags]
    )
    p_dialog_unpin.add_argument("chat")
    p_dialog_archive = dialog_sub.add_parser(
        "archive", help="Archive a dialog", parents=[global_flags]
    )
    p_dialog_archive.add_argument("chat")
    p_dialog_unarchive = dialog_sub.add_parser(
        "unarchive", help="Unarchive a dialog", parents=[global_flags]
    )
    p_dialog_unarchive.add_argument("chat")
    p_dialog_mute = dialog_sub.add_parser(
        "mute", help="Mute a dialog", parents=[global_flags]
    )
    p_dialog_mute.add_argument("chat")
    p_dialog_mute.add_argument(
        "--until", help="unmute at this ISO 8601 timestamp (UTC if naive)"
    )
    p_dialog_mute.add_argument(
        "--forever",
        action="store_true",
        help="mute indefinitely (explicit; omit is not forever)",
    )
    p_dialog_unmute = dialog_sub.add_parser(
        "unmute", help="Unmute a dialog", parents=[global_flags]
    )
    p_dialog_unmute.add_argument("chat")

    p_api = sub.add_parser(
        "api", help="Call an allowlisted raw TL method", parents=[global_flags]
    )
    p_api.add_argument("method", metavar="METHOD")
    p_api.add_argument("--params", metavar="JSON")
    p_api.add_argument("--write", action="store_true")
    p_api.add_argument("--confirm", metavar="METHOD")

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
    p_export_subscribers = export_sub.add_parser("subscribers", parents=[global_flags])
    p_export_subscribers.add_argument(
        "channel", help="@username, t.me link, or dialog id"
    )
    p_export_subscribers.add_argument("--output", required=True, type=Path)
    p_export_subscribers.add_argument("--limit", type=int)

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
    p_clone_sync.add_argument("source", help="source channel, supergroup, or dialog")
    p_clone_sync.add_argument("--limit", type=int)
    p_clone_refresh = clone_sub.add_parser("refresh", parents=[global_flags])
    p_clone_refresh.add_argument("source", help="source channel, supergroup, or dialog")
    p_clone_refresh.add_argument("--commit", metavar="PREVIEW_ID")

    p_draft = sub.add_parser(
        "draft", help="Show, list, set, or clear dialog drafts", parents=[global_flags]
    )
    draft_sub = p_draft.add_subparsers(dest="draft_command", required=True)
    draft_sub.add_parser("list", parents=[global_flags])
    p_draft_show = draft_sub.add_parser("show", parents=[global_flags])
    p_draft_show.add_argument("chat")
    p_draft_set = draft_sub.add_parser("set", parents=[global_flags])
    p_draft_set.add_argument("chat", nargs="?")
    p_draft_set.add_argument("text", nargs="?")
    p_draft_set.add_argument(
        "--format",
        choices=("plain", "md", "html"),
        default=None,
        dest="format",
        help="rich-text format of TEXT (default md, mirrors send)",
    )
    p_draft_set.add_argument("--preview", action="store_true")
    p_draft_set.add_argument("--commit", metavar="PREVIEW_ID")
    p_draft_set.add_argument("--reply-to", type=int, dest="reply_to")
    p_draft_set.add_argument("--topic", type=int)
    p_draft_clear = draft_sub.add_parser("clear", parents=[global_flags])
    p_draft_clear.add_argument("chat", nargs="?")
    p_draft_clear.add_argument("--preview", action="store_true")
    p_draft_clear.add_argument("--commit", metavar="PREVIEW_ID")

    return parser
