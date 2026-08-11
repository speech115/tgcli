"""Fail-closed CLI validation for the `tg jobs` subsystem."""

from tgcli import safety
from tgcli.archive import backfill as backfill_mod, sync as sync_mod
from tgcli.commands import archive as archive_cmd
from tgcli.errors import PolicyError
from tgcli.jobs import model


def prepare(args) -> None:
    if args.command != "jobs":
        return
    command = args.jobs_command
    if command in ("add", "cancel"):
        safety.enforce_local_mutation_allowed(args.readonly)
    if command == "add":
        _prepare_add(args)
        return
    if command in ("show", "cancel"):
        args.key = model.validate_key(args.key)
        return
    if command == "run":
        _prepare_run(args)


def _prepare_add(args) -> None:
    args.key = model.validate_key(args.key)
    args.priority = model.validate_priority(args.priority)
    if args.job_kind == "archive-transcribe":
        args.job_spec = model.transcribe_spec(args.max_attempts)
        args.job_lane = "local"
        return
    if args.job_kind == "archive-backfill":
        chats = list(args.chats)
        private = bool(args.private)
        backfill_mod.validate_private_mode(private=private, chats=chats)
        if not private:
            chats = backfill_mod.validate_dialogs(
                chats, maximum=archive_cmd.MAX_BACKFILL_DIALOGS
            )
        args.job_spec = {
            "chats": chats,
            "limit": backfill_mod.validate_limit(
                args.limit,
                default=archive_cmd.DEFAULT_BACKFILL_LIMIT,
                maximum=archive_cmd.MAX_BACKFILL_LIMIT,
            ),
            "private": private,
        }
        args.job_lane = "telegram"
        return
    if args.job_kind == "archive-sync":
        args.job_spec = {
            "max_dialogs": sync_mod.validate_max_dialogs(
                args.max_dialogs,
                default=archive_cmd.DEFAULT_SYNC_DIALOGS,
                maximum=archive_cmd.MAX_SYNC_DIALOGS,
            ),
            "max_events": sync_mod.validate_max_events(
                args.max_events,
                default=archive_cmd.DEFAULT_SYNC_EVENTS,
                maximum=archive_cmd.MAX_SYNC_EVENTS,
            ),
            "max_media": sync_mod.validate_max_media(
                args.max_media,
                default=archive_cmd.DEFAULT_SYNC_MEDIA,
                maximum=archive_cmd.MAX_SYNC_MEDIA,
            ),
        }
        args.job_lane = "telegram"
        return
    args.job_spec = model.clone_spec(args.source)
    args.job_lane = "telegram"


def _prepare_run(args) -> None:
    cap = getattr(args, "max_runtime", None)
    if cap is None:
        raise PolicyError("jobs run requires --max-runtime")
    if cap > model.MAX_RUNTIME_SECONDS:
        raise PolicyError(
            f"jobs run --max-runtime accepts at most {model.MAX_RUNTIME_SECONDS:g}"
        )
    rearm = getattr(args, "rearm", None)
    if rearm is not None:
        if getattr(args, "session_role", None) is None:
            safety.enforce_local_mutation_allowed(args.readonly)
        else:
            safety.enforce_mutation_allowed(args.readonly)
        args.rearm = model.validate_key(rearm)
        return
    prepare_resolved_run(args, args.lane)


def prepare_resolved_run(args, lane: str) -> None:
    """Apply lane-specific gates after a rearm key resolves from local state."""
    if lane == "telegram":
        safety.enforce_mutation_allowed(args.readonly)
    elif lane == "local":
        safety.enforce_local_mutation_allowed(args.readonly)
    else:
        raise PolicyError(f"unknown jobs lane: {lane}")
    args.lane = lane
    role = getattr(args, "session_role", None)
    if lane == "telegram" and role is None:
        raise PolicyError("jobs telegram lane requires an explicit session role")
    if lane == "local" and role is not None:
        raise PolicyError("jobs local lane does not accept a session role")
