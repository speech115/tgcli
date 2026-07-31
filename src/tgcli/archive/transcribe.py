"""Foreground local Parakeet transcription queue (ADR-0068 Phase 4)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tgcli.archive import store as store_mod
from tgcli.errors import PolicyError

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_MAX_ATTEMPTS = 3
MAX_MAX_ATTEMPTS = 5
PROCESS_TIMEOUT_SECONDS = 15 * 60
TRANSCRIBE_COMMAND = "transcribe"


class _RetryableError(Exception):
    pass


class _TerminalError(Exception):
    pass


def validate_limit(value: int | None, *, label: str = "limit") -> int:
    if value is None:
        return DEFAULT_LIMIT
    if value <= 0:
        raise PolicyError(f"archive transcribe --{label} must be positive")
    if value > MAX_LIMIT:
        raise PolicyError(
            f"archive transcribe --{label} accepts at most {MAX_LIMIT}"
        )
    return value


def validate_max_attempts(value: int | None) -> int:
    if value is None:
        return DEFAULT_MAX_ATTEMPTS
    if value <= 0:
        raise PolicyError("archive transcribe --max-attempts must be positive")
    if value > MAX_MAX_ATTEMPTS:
        raise PolicyError(
            f"archive transcribe --max-attempts accepts at most {MAX_MAX_ATTEMPTS}"
        )
    return value


def _engine_path() -> str:
    command = shutil.which(TRANSCRIBE_COMMAND)
    if command is None:
        raise PolicyError(
            "local Parakeet CLI is unavailable; install the offline "
            "FluidAudio/Parakeet `transcribe` command"
        )
    return command


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:500]


def _resolve_media_path(account_dir: Path, raw_path: str | None) -> Path:
    if not raw_path:
        raise _TerminalError("transcript queue row has no media path")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise _TerminalError("transcript media path escapes the account store")
    return account_dir / relative


def _extract_result(output_dir: Path) -> tuple[str, str, str]:
    manifest_path = output_dir / "manifest.json"
    transcript_path = output_dir / "transcript.json"
    if not manifest_path.is_file() or not transcript_path.is_file():
        raise _TerminalError(
            "Parakeet output is missing manifest.json or transcript.json"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _TerminalError(f"invalid Parakeet output: {exc}") from exc
    engine = str(manifest.get("engine") or "")
    if "parakeet" not in engine.casefold() and "fluidaudio" not in engine.casefold():
        raise _TerminalError(f"unexpected transcription engine: {engine or 'unknown'}")
    turns = transcript.get("turns")
    if not isinstance(turns, list):
        raise _TerminalError("Parakeet transcript.json has no turns list")
    text = "\n".join(
        str(turn.get("text", "")).strip()
        for turn in turns
        if isinstance(turn, dict) and str(turn.get("text", "")).strip()
    ).strip()
    if not text:
        raise _TerminalError("Parakeet returned an empty transcript")
    model_version = str(
        manifest.get("asr_model") or engine.rsplit("-", 1)[-1] or "unknown"
    )
    return text, engine, model_version


def _run_engine(command: str, media_path: Path) -> tuple[str, str, str]:
    with tempfile.TemporaryDirectory(
        prefix=".transcribe-", dir=str(media_path.parent)
    ) as temp_dir:
        output_dir = Path(temp_dir)
        argv = [
            command,
            str(media_path),
            "--speakers",
            "off",
            "--lang",
            "auto",
            "--out",
            str(output_dir),
        ]
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=PROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise _RetryableError("Parakeet process timed out") from exc
        except OSError as exc:
            raise _RetryableError(f"could not run Parakeet: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            message = f"Parakeet exited with status {result.returncode}"
            if detail:
                message += f": {detail[:400]}"
            if result.returncode == 2:
                raise _TerminalError(message)
            raise _RetryableError(message)
        return _extract_result(output_dir)


def _base_result(limit: int, max_attempts: int) -> dict[str, Any]:
    return {
        "limit": limit,
        "max_attempts": max_attempts,
        "queued": 0,
        "attempted": 0,
        "transcribed": 0,
        "retryable": 0,
        "no_transcript": 0,
        "skipped_missing_media": 0,
        "remaining": False,
        "errors": [],
    }


def run_queue(
    conn,
    account_dir: Path,
    *,
    limit: int,
    max_attempts: int,
) -> dict[str, Any]:
    """Drain the newest ready queue rows through the local Parakeet CLI."""
    result = _base_result(limit, max_attempts)
    rows = store_mod.list_transcript_queue(conn, limit=limit, max_attempts=max_attempts)
    result["queued"] = len(rows)
    if not rows:
        return result
    command = _engine_path()
    for row in rows:
        peer_id = int(row["peer_id"])
        message_id = int(row["message_id"])
        try:
            media_path = _resolve_media_path(account_dir, row.get("media_path"))
            if not media_path.is_file():
                with conn:
                    store_mod.record_media_failure(
                        conn,
                        peer_id,
                        message_id,
                        error="media file is not available",
                    )
                result["skipped_missing_media"] += 1
                continue
            result["attempted"] += 1
            text, model, model_version = _run_engine(command, media_path)
        except _RetryableError as exc:
            status = (
                "no_transcript"
                if int(row["attempts"]) + 1 >= max_attempts
                else "retryable"
            )
            error = _error_text(exc)
            with conn:
                store_mod.record_transcript_failure(
                    conn,
                    peer_id,
                    message_id,
                    status=status,
                    error=error,
                )
            result["no_transcript" if status == "no_transcript" else "retryable"] += 1
            result["errors"].append(
                {
                    "peer_id": peer_id,
                    "message_id": message_id,
                    "status": status,
                    "error": error,
                }
            )
        except _TerminalError as exc:
            error = _error_text(exc)
            with conn:
                store_mod.record_transcript_failure(
                    conn,
                    peer_id,
                    message_id,
                    status="no_transcript",
                    error=error,
                )
            result["no_transcript"] += 1
            result["errors"].append(
                {
                    "peer_id": peer_id,
                    "message_id": message_id,
                    "status": "no_transcript",
                    "error": error,
                }
            )
        else:
            with conn:
                store_mod.record_transcript_success(
                    conn,
                    peer_id,
                    message_id,
                    text=text,
                    model=model,
                    model_version=model_version,
                )
            result["transcribed"] += 1
    result["remaining"] = bool(
        store_mod.list_transcript_queue(conn, limit=1, max_attempts=max_attempts)
    )
    return result
