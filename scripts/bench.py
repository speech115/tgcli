#!/usr/bin/env python3
"""Live benchmark: exercise every tgcli command against a real account.

Run explicitly (writes one bench message to your own Saved Messages):

    .venv/bin/python scripts/bench.py [--account main] [--tg PATH]
                                      [--subscribers-channel CHANNEL]

Contract mirrors the CLI: JSON report on stdout, human table on stderr,
exit 0 when nothing failed (SKIP is not a failure), exit 1 otherwise.
Takeout-gated exports SKIP on rate limiting (exit 5) instead of failing.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The default needs a channel whose participants the account may list.
DEFAULT_SUBSCRIBERS_CHANNEL = "@mir_ivanova"  # MIR Сергея Иванова (own channel)
STEP_TIMEOUT_SECONDS = 120


def find_media_message(messages) -> int | None:
    for message in messages:
        if message.get("media"):
            return message["id"]
    return None


def classify(exit_code: int, *, allow_rate_limit_skip: bool) -> str:
    if exit_code == 0:
        return "PASS"
    if exit_code == 5 and allow_rate_limit_skip:
        return "SKIP"
    return "FAIL"


def format_table(results) -> str:
    width = max((len(result["step"]) for result in results), default=4)
    lines = [f"{'step'.ljust(width)}  status  ms      detail"]
    for result in results:
        lines.append(
            f"{result['step'].ljust(width)}  {result['status']:<6}  "
            f"{result['duration_ms']:<6}  {result['detail']}"
        )
    return "\n".join(lines)


def build_report(account: str, results) -> dict:
    return {
        "bench": {
            "account": account,
            "passed": sum(1 for r in results if r["status"] == "PASS"),
            "skipped": sum(1 for r in results if r["status"] == "SKIP"),
            "failed": sum(1 for r in results if r["status"] == "FAIL"),
            "total_ms": sum(r["duration_ms"] for r in results),
            "results": results,
        }
    }


def find_tg(override: str | None) -> str:
    if override:
        return override
    local = ROOT / ".venv" / "bin" / "tg"
    if local.exists():
        return str(local)
    found = shutil.which("tg")
    if not found:
        sys.exit("bench: no tg binary found; pass --tg PATH")
    return found


class Runner:
    def __init__(self, tg: str, account: str):
        self.tg = tg
        self.account = account
        self.results = []

    def run(self, step: str, argv, *, allow_rate_limit_skip: bool = False):
        command = [self.tg, "--account", self.account, *argv, "--json"]
        started = time.perf_counter()
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=STEP_TIMEOUT_SECONDS
            )
            exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            exit_code, stdout, stderr = (
                1,
                "",
                f"bench timeout after {STEP_TIMEOUT_SECONDS}s",
            )
        duration_ms = int((time.perf_counter() - started) * 1000)

        status = classify(exit_code, allow_rate_limit_skip=allow_rate_limit_skip)
        data = None
        if status == "PASS":
            try:
                data = json.loads(stdout)
            except ValueError:
                status, exit_code = "FAIL", exit_code or 1
        detail = "" if status == "PASS" else _error_detail(stdout, stderr, exit_code)
        self.record(step, status, duration_ms, detail)
        return data

    def record(self, step: str, status: str, duration_ms: int, detail: str = ""):
        self.results.append(
            {
                "step": step,
                "status": status,
                "duration_ms": duration_ms,
                "detail": detail,
            }
        )


def _error_detail(stdout: str, stderr: str, exit_code: int) -> str:
    for stream in (stderr, stdout):
        for line in stream.splitlines():
            line = line.strip()
            if line:
                return f"exit {exit_code}: {line[:120]}"
    return f"exit {exit_code}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--account", default="main")
    parser.add_argument("--tg", help="path to the tg binary")
    parser.add_argument(
        "--subscribers-channel",
        default=DEFAULT_SUBSCRIBERS_CHANNEL,
        help="channel for the subscribers export step (needs admin rights)",
    )
    args = parser.parse_args()

    runner = Runner(find_tg(args.tg), args.account)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    runner.run("dialogs", ["dialogs", "--limit", "10"])
    read_data = runner.run("read", ["read", "me", "--limit", "10"])
    runner.run("search", ["search", "me", "bench", "--limit", "5"])
    latest = runner.run("latest", ["latest", "me"])

    if latest:
        runner.run("message", ["message", "me", str(latest["message"]["id"])])
    else:
        runner.record("message", "SKIP", 0, "no latest message to fetch")

    runner.run("info", ["info", "me"])
    runner.run("count", ["count", "me"])
    runner.run("api-read", ["api", "users.getFullUser", "--params", '{"id":"me"}'])

    preview = runner.run(
        "send-preview", ["send", "me", f"tgcli bench {stamp}", "--preview"]
    )
    if preview:
        runner.run("send-commit", ["send", "--commit", preview["preview_id"]])
    else:
        runner.record("send-commit", "SKIP", 0, "no preview id from send-preview")

    with tempfile.TemporaryDirectory(prefix="tgcli-bench-") as tmp:
        media_id = find_media_message(read_data["messages"]) if read_data else None
        if media_id is None:
            discovery = runner.run("media-discover", ["read", "me", "--limit", "100"])
            if discovery:
                media_id = find_media_message(discovery["messages"])
        if media_id is None:
            runner.record(
                "media-download",
                "SKIP",
                0,
                "no media in Saved Messages; forward any photo to yourself",
            )
        else:
            runner.run(
                "media-download",
                [
                    "media",
                    "download",
                    "me",
                    str(media_id),
                    "--output",
                    f"{tmp}/media.bin",
                ],
            )

        runner.run(
            "export-messages",
            [
                "export",
                "messages",
                "me",
                "--output",
                f"{tmp}/messages.jsonl",
                "--limit",
                "50",
            ],
            allow_rate_limit_skip=True,
        )
        runner.run(
            "export-subscribers",
            [
                "export",
                "subscribers",
                args.subscribers_channel,
                "--output",
                f"{tmp}/subscribers.csv",
                "--limit",
                "200",
            ],
            allow_rate_limit_skip=True,
        )

    print(format_table(runner.results), file=sys.stderr)
    print(json.dumps(build_report(args.account, runner.results), ensure_ascii=False))
    return 0 if not any(r["status"] == "FAIL" for r in runner.results) else 1


if __name__ == "__main__":
    sys.exit(main())
