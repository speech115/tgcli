"""`tg-serve`: run `tg` commands one after another over one held session (ADR-0124).

A parent process (an adapter such as an MCP server) starts it for one account
and session role and talks JSON lines over stdin/stdout:

    -> {"ready": true, "account": "main", "role": "mcp"}       once, after connecting
    <- {"argv": ["read", "--limit", "5", "--", "@chan"], "stdin": ""}
    -> {"exit_code": 0, "stdout": "...", "stderr": "..."}

Each request is a full `tg` invocation through `cli.main` (parsing, preflight,
readonly gates, preview -> commit, audit, the invocation journal), with
`--account`/`--session-role` fixed by the server and `--json` added. What
differs from a `tg` process: the session stays connected and locked between
requests, the coroutines run on one loop, and the parser is built once. The
server opens no socket and exits when stdin closes.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
import threading
from typing import IO

from tgcli import cli, loop, output, session
from tgcli.config import load_config, resolve_account
from tgcli.errors import ConfigError, TgcliError
from tgcli.parser import build_parser

# The server fixes these; a request that sets them would reach another session.
RESERVED_FLAGS = frozenset({"--account", "--session-role"})


def request_argv(request: object) -> list[str]:
    """The request's command argv, or ConfigError when it is malformed."""
    if not isinstance(request, dict):
        raise ConfigError("a request is a JSON object with an argv list")
    argv = request.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(a, str) for a in argv)
    ):
        raise ConfigError("argv must be a non-empty list of strings")
    options = argv[: argv.index("--")] if "--" in argv else argv
    if any(a.split("=", 1)[0] in RESERVED_FLAGS for a in options):
        raise ConfigError("--account and --session-role are fixed by tg-serve")
    stdin = request.get("stdin", "")
    if not isinstance(stdin, str):
        raise ConfigError("stdin must be a string")
    return argv


def handle(line: str, prefix: list[str], parser: argparse.ArgumentParser) -> dict:
    """Run one request line as a `tg` invocation and capture what it printed."""
    out, err = io.StringIO(), io.StringIO()
    try:
        request = json.loads(line)
        argv = request_argv(request)
    except (json.JSONDecodeError, TgcliError) as exc:
        message = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
        error = {"error": {"code": "USAGE", "message": f"bad request: {message}"}}
        return {"exit_code": 1, "stdout": json.dumps(error) + "\n", "stderr": ""}
    # stdin is the request pipe: a command that reads stdin gets the request's text.
    with (
        contextlib.redirect_stdout(out),
        contextlib.redirect_stderr(err),
        _stdin(io.StringIO(request.get("stdin", ""))),
    ):
        code = cli.main([*prefix, "--json", *argv], parser=parser)
    return {"exit_code": code, "stdout": out.getvalue(), "stderr": err.getvalue()}


@contextlib.contextmanager
def _stdin(stream: IO[str]):
    previous, sys.stdin = sys.stdin, stream
    try:
        yield
    finally:
        sys.stdin = previous


def serve(
    alias: str | None, role: str | None, requests: IO[str], replies: IO[str]
) -> int:
    account = resolve_account(load_config(), alias)
    prefix = ["--account", account.alias, *(["--session-role", role] if role else [])]
    parser = build_parser()
    event_loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=event_loop.run_forever, name="tg-serve", daemon=True
    )
    thread.start()
    held = session.hold(account, role=role)
    with loop.running_on(event_loop):
        loop.run(held.__aenter__())
        try:
            _reply(replies, {"ready": True, "account": account.alias, "role": role})
            for line in requests:
                if line.strip():
                    _reply(replies, handle(line, prefix, parser))
        finally:
            loop.run(held.__aexit__(None, None, None))
    event_loop.call_soon_threadsafe(event_loop.stop)
    thread.join(timeout=5)
    return 0


def _reply(replies: IO[str], payload: dict) -> None:
    replies.write(json.dumps(payload, ensure_ascii=False) + "\n")
    replies.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tg-serve", description="Run tg commands over one held session (ADR-0124)."
    )
    parser.add_argument("--account", help="account alias from config")
    parser.add_argument(
        "--session-role",
        dest="session_role",
        metavar="NAME",
        help="session role to hold (ADR-0062)",
    )
    args = parser.parse_args(argv)
    # Replies own the real stdout; whatever else a command prints is captured.
    replies = sys.stdout
    sys.stdout = sys.stderr
    try:
        return serve(args.account, args.session_role, sys.stdin, replies)
    except TgcliError as err:
        # The parent reads stdout for the ready line: a failed start answers there.
        sys.stdout = replies
        output.emit_error(err, as_json=True)
        return err.exit_code
    finally:
        sys.stdout = replies


def entrypoint() -> None:
    sys.exit(main())
