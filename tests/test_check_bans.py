from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "check-bans.py"


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_minimal_tree(
    root: Path,
) -> None:
    (root / "src/tgcli").mkdir(parents=True, exist_ok=True)


def test_ban_check_rejects_a_flood_caught_by_one_class(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/archive/media.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "from telethon import errors\n"
        "try:\n    pass\nexcept errors.FloodWaitError:\n    pass\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "media.py:4 names FloodWaitError" in result.stdout
    assert "tgcli.tg_errors.FLOOD_ERRORS" in result.stdout

    taxonomy = tmp_path / "src/tgcli/tg_errors.py"
    taxonomy.write_text(
        "from telethon import errors\n"
        "FLOOD_ERRORS = (errors.FloodWaitError, errors.FloodPremiumWaitError)\n"
    )
    module.write_text("")
    assert _run(tmp_path).returncode == 0


def test_ban_check_rejects_a_history_read_with_no_page_wait(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/commands/read.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "async def fetch(tg, entity):\n"
        "    async for message in tg.iter_messages(entity, limit=500):\n"
        "        yield message\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "read.py:2 calls iter_messages without wait_time" in result.stdout
    assert "wait_time=pace.PAGE_WAIT_S" in result.stdout
    assert "wait_time=pace.INTERACTIVE_PAGE_WAIT" in result.stdout

    module.write_text(
        "async def fetch(tg, entity):\n"
        "    async for message in tg.iter_messages(\n"
        "        entity, limit=500, wait_time=pace.PAGE_WAIT_S\n"
        "    ):\n"
        "        yield message\n"
    )
    assert _run(tmp_path).returncode == 0


def test_repository_passes_ban_checks():
    result = _run(Path(__file__).parents[1])

    assert result.returncode == 0, result.stdout


def test_ban_check_rejects_write_text_in_state_module(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/login_state.py"
    module.write_text("def save(path, text):\n    path.write_text(text)\n")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "login_state.py:2 calls write_text" in result.stdout
    assert "tgcli.atomic.replace_text" in result.stdout


def test_ban_check_accepts_atomic_writer_in_state_module(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/login_state.py"
    module.write_text(
        "from tgcli import atomic\n\n"
        "def save(path, text):\n    atomic.replace_text(path, text)\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout


def test_ban_check_rejects_raw_chatref_parse_outside_chatref(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/commands/send.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "from tgcli import chatref\n"
        "async def go(tg, chat):\n"
        "    return await tg.get_entity(chatref.parse(chat))\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "send.py:3 calls chatref.parse directly" in result.stdout
    assert "tgcli.chatref.resolve" in result.stdout

    module.write_text(
        "from tgcli import chatref\n"
        "async def go(tg, chat):\n"
        "    return await chatref.resolve(tg, chat)\n"
    )
    assert _run(tmp_path).returncode == 0


def test_ban_check_rejects_async_except_exception_without_abort_guard(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/clone/ergonomics.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "async def mute(tg):\n"
        "    try:\n"
        "        await tg()\n"
        "    except Exception:\n"
        "        return False\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "ergonomics.py:4 swallows Exception in an async block" in result.stdout
    assert "RUN_ABORTING_ERRORS" in result.stdout

    module.write_text(
        "from tgcli.tg_errors import NOT_REFUSALS\n"
        "async def mute(tg):\n"
        "    try:\n"
        "        await tg()\n"
        "    except NOT_REFUSALS:\n"
        "        raise\n"
        "    except Exception:\n"
        "        return False\n"
    )
    assert _run(tmp_path).returncode == 0
