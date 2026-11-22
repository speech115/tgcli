"""INSTALL_FOR_AGENTS.md and the Claude Code plugin, run the way agents run them."""

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "INSTALL_FOR_AGENTS.md"
PLUGIN = ROOT / "plugins" / "tgcli"
HOOK = PLUGIN / "hooks" / "session-start.sh"
BEGIN = "<!-- tgcli:begin -->"
CONNECT = "6. Connect your agents"
UNINSTALL = "Uninstall"
CODEX_CONFIG = 'model = "gpt"\n\n[projects."/p"]\ntrust_level = "trusted"\n'


def guide_script(heading: str) -> str:
    section = GUIDE.read_text().split(f"\n## {heading}\n", 1)[1].split("\n## ")[0]
    match = re.search(r"<<'PY'\n(.*?)\nPY\n", section, re.S)
    assert match, f"no python heredoc under '## {heading}'"
    return match.group(1)


def agent_home(tmp_path: Path, *agents: str) -> Path:
    home = tmp_path / "home"
    skill = home / ".agents" / "skills" / "tgcli" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("skill v1\n")
    for agent in agents:
        (home / agent).mkdir()
    tg = home / ".local" / "bin" / "tg"
    tg.parent.mkdir(parents=True)
    tg.write_text("#!/bin/sh\n")
    tg.chmod(0o755)
    return home


def run_guide(home: Path, heading: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")
    }
    env.update(
        HOME=str(home),
        PATH="/usr/bin:/bin",
        TG_BIN=str(home / ".local" / "bin" / "tg"),
    )
    result = subprocess.run(
        [sys.executable, "-"],
        input=guide_script(heading),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result


def files(home: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(home)): path.read_bytes()
        for path in sorted(home.rglob("*"))
        if path.is_file()
    }


def test_connect_sets_up_every_agent_and_a_rerun_changes_nothing(tmp_path):
    home = agent_home(tmp_path, ".claude", ".codex", ".gemini")
    (home / ".codex" / "config.toml").write_text(CODEX_CONFIG)
    (home / ".claude" / "CLAUDE.md").write_text("# Mine\n")

    run_guide(home, CONNECT)
    first = files(home)
    run_guide(home, CONNECT)

    assert files(home) == first
    claude_md = (home / ".claude" / "CLAUDE.md").read_text()
    assert claude_md.startswith("# Mine\n\n")
    for path in (".claude/CLAUDE.md", ".codex/AGENTS.md", ".gemini/GEMINI.md"):
        assert (home / path).read_text().count(BEGIN) == 1
    antigravity = home / ".gemini" / "config" / "skills" / "tgcli" / "SKILL.md"
    assert antigravity.read_text() == "skill v1\n"
    config = tomllib.loads((home / ".codex" / "config.toml").read_text())
    assert config["model"] == "gpt"
    assert config["projects"] == {"/p": {"trust_level": "trusted"}}
    assert config["sandbox_workspace_write"] == {
        "writable_roots": [f"{home}/.config/tgcli", f"{home}/.local/state/tgcli"],
        "network_access": True,
    }
    backups = list((home / ".codex").glob("config.toml.bak-tgcli-*"))
    assert [backup.read_text() for backup in backups] == [CODEX_CONFIG]
    rule = (home / ".codex" / "rules" / "tgcli.rules").read_text()
    assert f'pattern=[["tg", "{home}/.local/bin/tg"]]' in rule
    assert 'decision="allow"' in rule
    assert rule.count('decision="prompt"') == 2
    policy = home / ".codex" / "rules" / "tgcli.rules"
    tg = f"{home}/.local/bin/tg"
    if shutil.which("codex"):
        for command, decision in [
            (["tg", "--json", "read", "me"], "allow"),
            (["tg", "--json", "send", "me", "hi", "--preview"], "allow"),
            (["tg", "--json", "send", "--commit", "abc"], "prompt"),
            ([tg, "delete", "--commit", "abc"], "prompt"),
        ]:
            out = subprocess.run(
                ["codex", "execpolicy", "check", "-r", str(policy), "--", *command],
                capture_output=True, text=True, check=True,
            ).stdout
            assert json.loads(out)["decision"] == decision, command


def test_connect_replaces_an_outdated_block_and_keeps_the_rest(tmp_path):
    home = agent_home(tmp_path, ".claude")
    claude_md = home / ".claude" / "CLAUDE.md"
    claude_md.write_text(f"top\n\n{BEGIN}\nold advice\n<!-- tgcli:end -->\nbottom\n")

    run_guide(home, CONNECT)

    text = claude_md.read_text()
    assert text.startswith("top\n\n")
    assert text.endswith("bottom\n")
    assert text.count(BEGIN) == 1
    assert "old advice" not in text


def test_connect_leaves_a_users_own_sandbox_table_for_a_hand_merge(tmp_path):
    home = agent_home(tmp_path, ".codex")
    config = home / ".codex" / "config.toml"
    own = '[sandbox_workspace_write]\nwritable_roots = ["/work"]\n'
    config.write_text(own)

    result = run_guide(home, CONNECT)

    assert config.read_text() == own
    assert "merge by hand" in result.stdout
    assert not list((home / ".codex").glob("config.toml.bak-tgcli-*"))


def test_connect_writes_codex_instructions_to_a_non_empty_override(tmp_path):
    home = agent_home(tmp_path, ".codex")
    (home / ".codex" / "AGENTS.override.md").write_text("override\n")

    run_guide(home, CONNECT)

    assert BEGIN in (home / ".codex" / "AGENTS.override.md").read_text()
    assert not (home / ".codex" / "AGENTS.md").exists()


def test_connect_touches_only_agents_that_are_installed(tmp_path):
    home = agent_home(tmp_path, ".codex")

    run_guide(home, CONNECT)

    assert not (home / ".claude").exists()
    assert not (home / ".gemini").exists()


def test_uninstall_removes_what_connect_added(tmp_path):
    home = agent_home(tmp_path, ".claude", ".codex", ".gemini")
    (home / ".codex" / "config.toml").write_text(CODEX_CONFIG)
    (home / ".claude" / "CLAUDE.md").write_text("# Mine\n")

    run_guide(home, CONNECT)
    run_guide(home, UNINSTALL)

    assert (home / ".codex" / "config.toml").read_text() == CODEX_CONFIG
    assert (home / ".claude" / "CLAUDE.md").read_text() == "# Mine\n"
    assert not (home / ".codex" / "AGENTS.md").exists()
    assert not (home / ".gemini" / "GEMINI.md").exists()


def run_hook(home: Path, **env: str) -> str:
    result = subprocess.run(
        ["sh", str(HOOK)],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", **env},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("\n") <= 1
    assert not result.stdout.startswith("{")
    return result.stdout


def installed(tmp_path: Path) -> Path:
    home = agent_home(tmp_path)
    (home / ".config" / "tgcli").mkdir(parents=True)
    (home / ".config" / "tgcli" / "config.toml").write_text("")
    sessions = home / ".local" / "state" / "tgcli" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "main.session").write_text("")
    return home


def test_hook_is_silent_when_tg_is_installed_and_logged_in(tmp_path):
    assert run_hook(installed(tmp_path)) == ""


def test_hook_says_when_tg_is_not_installed(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    assert "is not installed" in run_hook(home)


def test_hook_says_when_no_account_is_logged_in(tmp_path):
    home = installed(tmp_path)
    (home / ".local" / "state" / "tgcli" / "sessions" / "main.session").unlink()

    assert "tg setup" in run_hook(home)


def test_hook_reads_the_config_and_state_overrides(tmp_path):
    home = installed(tmp_path)
    elsewhere = tmp_path / "elsewhere"

    out = run_hook(
        home,
        TGCLI_CONFIG=str(elsewhere / "config.toml"),
        TGCLI_STATE_DIR=str(elsewhere / "state"),
    )

    assert "tg setup" in out


def test_hook_names_the_retired_tg_harness(tmp_path):
    home = installed(tmp_path)
    (home / ".local" / "share" / "uv" / "tools" / "tg-harness").mkdir(parents=True)

    assert "tg-harness" in run_hook(home)


def test_plugin_skill_is_a_copy_of_skill_md():
    copy = PLUGIN / "skills" / "tgcli" / "SKILL.md"

    assert copy.read_bytes() == (ROOT / "SKILL.md").read_bytes(), (
        "run: cp SKILL.md plugins/tgcli/skills/tgcli/SKILL.md"
    )


def test_marketplace_lists_the_plugin_and_omits_versions_so_commits_reach_users():
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    [entry] = marketplace["plugins"]
    manifest = json.loads(
        (ROOT / entry["source"] / ".claude-plugin" / "plugin.json").read_text()
    )

    assert marketplace["name"] == entry["name"] == manifest["name"] == "tgcli"
    assert (ROOT / entry["source"]).resolve() == PLUGIN
    assert "version" not in entry
    assert "version" not in manifest


def test_plugin_hook_runs_the_shipped_script_at_session_start():
    hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]
    [command] = [
        hook["command"] for entry in hooks["SessionStart"] for hook in entry["hooks"]
    ]

    assert command == 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"'
    assert HOOK.is_file()
