"""Argument grammar owned by the `tg jobs` subsystem (ADR-0087)."""


def add_to(sub, global_flags) -> None:
    parser = sub.add_parser(
        "jobs", help="Persist and run typed foreground jobs", parents=[global_flags]
    )
    commands = parser.add_subparsers(dest="jobs_command", required=True)
    add = commands.add_parser("add", help="Add one typed job", parents=[global_flags])
    kinds = add.add_subparsers(dest="job_kind", required=True)

    transcribe = kinds.add_parser(
        "archive-transcribe",
        help="Drain the local archive transcription queue",
        parents=[global_flags],
    )
    transcribe.add_argument("--key", required=True, help="stable job key")
    transcribe.add_argument(
        "--max-attempts",
        type=int,
        help="retryable attempts before no transcript (default 3, hard cap 5)",
    )
    _add_generation_flags(transcribe)

    backfill = kinds.add_parser(
        "archive-backfill",
        help="Backfill one archive dialog per quantum",
        parents=[global_flags],
    )
    backfill.add_argument("--key", required=True, help="stable job key")
    backfill.add_argument("chats", nargs="*", metavar="CHAT")
    backfill.add_argument("--private", action="store_true")
    backfill.add_argument("--limit", type=int)
    _add_generation_flags(backfill)

    sync = kinds.add_parser(
        "archive-sync",
        help="Apply one archive changes quantum",
        parents=[global_flags],
    )
    sync.add_argument("--key", required=True, help="stable job key")
    sync.add_argument("--max-events", type=int)
    sync.add_argument("--max-dialogs", type=int)
    sync.add_argument("--max-media", type=int)
    _add_generation_flags(sync)

    clone = kinds.add_parser(
        "clone-sync",
        help="Run one 50-batch clone window",
        parents=[global_flags],
    )
    clone.add_argument("--key", required=True, help="stable job key")
    clone.add_argument("source")
    _add_generation_flags(clone)

    commands.add_parser(
        "list", help="List latest job generations", parents=[global_flags]
    )
    show = commands.add_parser(
        "show", help="Show one job and its recent events", parents=[global_flags]
    )
    show.add_argument("key")
    cancel = commands.add_parser(
        "cancel", help="Request cooperative cancellation", parents=[global_flags]
    )
    cancel.add_argument("key")
    run = commands.add_parser(
        "run", help="Run one foreground lane", parents=[global_flags]
    )
    selection = run.add_mutually_exclusive_group(required=True)
    selection.add_argument("--lane", choices=("telegram", "local"))
    selection.add_argument("--rearm", metavar="KEY")


def _add_generation_flags(parser) -> None:
    parser.add_argument(
        "--priority", choices=("low", "normal", "high"), default="normal"
    )
    parser.add_argument("--replace", action="store_true")
