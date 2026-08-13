"""Argument grammar owned by the `tg archive` subsystem (ADR-0115)."""


def add_to(sub, global_flags) -> None:
    parser = sub.add_parser(
        "archive",
        help="Local archive store (ADR-0068)",
        parents=[global_flags],
    )
    commands = parser.add_subparsers(dest="archive_command", required=True)
    commands.add_parser(
        "init", help="Create and bind the account archive store", parents=[global_flags]
    )
    add = commands.add_parser(
        "add", help="Opt a group/channel into archive scope", parents=[global_flags]
    )
    add.add_argument("chat", help="@username, t.me link, or dialog id")
    remove = commands.add_parser(
        "remove",
        help="Remove a group/channel from archive scope",
        parents=[global_flags],
    )
    remove.add_argument("chat", help="@username, t.me link, or dialog id")
    commands.add_parser(
        "list", help="List standing + explicit archive scope", parents=[global_flags]
    )
    commands.add_parser(
        "status", help="Offline archive freshness and counts", parents=[global_flags]
    )
    search = commands.add_parser(
        "search", help="Offline FTS5 archive search", parents=[global_flags]
    )
    search.add_argument("query", help="FTS5 MATCH query")
    search.add_argument("--chat", help="archived peer scope")
    search.add_argument(
        "--from", dest="from_user", help="sender id, @username, or name"
    )
    search.add_argument("--since", help="ISO date/datetime lower bound")
    search.add_argument("--until", help="ISO date/datetime upper bound")
    search.add_argument(
        "--kind",
        choices=["text", "photo", "video", "video_note", "audio", "voice", "document"],
        help="message media kind",
    )
    search.add_argument(
        "--transcripts-only", action="store_true", help="match transcript text only"
    )
    search.add_argument(
        "--sort", choices=["relevance", "date"], help="result order (default relevance)"
    )
    search.add_argument("--limit", type=int, help="max hits (default 20, cap 50)")
    search.add_argument("--page", type=int, help="1-based result page")
    read = commands.add_parser(
        "read", help="Read the local archive timeline offline", parents=[global_flags]
    )
    read.add_argument("chat", help="archived peer scope")
    read.add_argument("--around-id", type=int, help="center timeline on message id")
    read.add_argument("--around-date", help="center timeline on ISO date/datetime")
    read.add_argument("--since", help="ISO date/datetime lower bound")
    read.add_argument("--until", help="ISO date/datetime upper bound")
    read.add_argument("--limit", type=int, help="timeline rows (default 20, cap 50)")
    history = commands.add_parser(
        "history",
        help="Read local revisions and deletion history offline",
        parents=[global_flags],
    )
    history.add_argument("chat", help="archived peer scope")
    history.add_argument("message_id", type=int)
    backfill = commands.add_parser(
        "backfill",
        help="Backfill dialogs into the archive (CHAT list or --private)",
        parents=[global_flags],
    )
    backfill.add_argument(
        "chats",
        nargs="*",
        metavar="CHAT",
        help="one or more dialogs (no empty→all sentinel; omit with --private)",
    )
    backfill.add_argument(
        "--private",
        action="store_true",
        help="enumerate standing private 1:1 dialogs under --max-dialogs",
    )
    backfill.add_argument(
        "--limit",
        type=int,
        help="messages per dialog (default 100, hard cap 1000)",
    )
    backfill.add_argument(
        "--max-dialogs",
        type=int,
        help="private enumeration cap (default 20, hard cap 100)",
    )
    sync = commands.add_parser(
        "sync",
        help="Apply tg changes delta into the archive",
        parents=[global_flags],
    )
    sync.add_argument(
        "--max-events",
        type=int,
        help="catch-up message budget per run (default 500, hard cap 5000)",
    )
    sync.add_argument(
        "--max-dialogs",
        type=int,
        help="channel catch-up dialogs per run (default 20, hard cap 50)",
    )
    sync.add_argument(
        "--max-media",
        type=int,
        help="voice/video-note media downloads per run (default 50, hard cap 500)",
    )
    transcribe = commands.add_parser(
        "transcribe",
        help="Transcribe queued voice/video notes with local Parakeet",
        parents=[global_flags],
    )
    transcribe.add_argument(
        "--limit", type=int, help="media items per run (default 20, hard cap 100)"
    )
    transcribe.add_argument(
        "--max-attempts",
        type=int,
        help="retryable attempts before no transcript (default 3, hard cap 5)",
    )
    commands.add_parser(
        "rebaseline",
        help="Explicitly re-init the archive changes cursor (gap recovery)",
        parents=[global_flags],
    )
