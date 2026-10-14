"""Agent sessions in this repo run readonly and cannot open sessions bare."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".claude" / "hooks" / "guard_session_files.py"


def run_hook(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def bash_call(command: str, cwd: str | None = None) -> str:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = cwd
    return json.dumps(payload)


def test_agent_settings_make_sessions_readonly_and_wire_the_hook():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())

    assert settings["env"]["TGCLI_READONLY"] == "1"
    bash_hooks = [
        hook["command"]
        for entry in settings["hooks"]["PreToolUse"]
        if entry["matcher"] == "Bash"
        for hook in entry["hooks"]
    ]
    assert any("guard_session_files.py" in command for command in bash_hooks)


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c \"import sqlite3; sqlite3.connect('main.session')\"",
        "python ~/.local/state/tgcli/sessions/main.session",
        "/usr/bin/python3.11 inspect.py main.session",
        "cd /tmp && python3 - <<'PY'\nTelegramClient('x.session')\nPY",
        "FOO=1 python3 read.py ~/.local/state/tgcli/sessions/a",
        "env python3 x.py work.session",
        "S=work.session\npython3 read.py $S",
        "env -i python3 inspect.py main.session",
        "env -u HOME -i FOO=1 python3 inspect.py main.session",
        "env --unset=HOME python3 inspect.py main.session",
        "sudo -u root python3 inspect.py main.session",
        "sudo env -i /usr/bin/python3 inspect.py main.session",
        "time -p python3 inspect.py main.session",
        "nohup python3 inspect.py main.session",
        "exec -a probe python3 inspect.py main.session",
        'env -S "python3 inspect.py main.session"',
    ],
)
def test_hook_blocks_a_bare_python_near_session_files(command):
    result = run_hook(bash_call(command))

    assert result.returncode == 2
    assert "uv run tg" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        "uv run tg --json accounts list",
        "uv run python scripts/probe.py main.session",
        ".venv/bin/python -c \"open('main.session')\"",
        "python3 scripts/check-docs.py",
        "ls ~/.local/state/tgcli/sessions/",
        "cat > notes.txt <<'EOF'\npython3 read.py main.session\nEOF",
    ],
)
def test_hook_allows_pinned_interpreters_and_unrelated_python(command):
    result = run_hook(bash_call(command))

    assert result.returncode == 0, result.stderr


def test_hook_blocks_a_bare_python_run_inside_a_sessions_directory():
    command = "python3 read.py main"
    sessions = "/Users/me/.local/state/tgcli/sessions"

    assert run_hook(bash_call(command, cwd=sessions)).returncode == 2
    assert run_hook(bash_call(command, cwd=sessions + "/work")).returncode == 2
    assert run_hook(bash_call(command, cwd="/Users/me/src/tgcli")).returncode == 0
    allowed = bash_call("uv run python read.py main", cwd=sessions)
    assert run_hook(allowed).returncode == 0


def test_hook_ignores_input_it_cannot_parse():
    assert run_hook("not json").returncode == 0
