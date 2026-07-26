import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-link.sh"


def run_script(env):
    return subprocess.run(
        ["bash", str(SCRIPT)], capture_output=True, text=True, env={**os.environ, **env}
    )


def make_fake_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".venv" / "bin").mkdir(parents=True)
    entry = repo / ".venv" / "bin" / "tg"
    entry.write_text("#!/bin/sh\necho tgcli\n")
    entry.chmod(0o755)
    return repo


def test_creates_symlink_to_venv_entrypoint(tmp_path):
    repo = make_fake_repo(tmp_path)
    bin_dir = tmp_path / "bin"

    result = run_script({"TGCLI_BIN_DIR": str(bin_dir), "TGCLI_REPO": str(repo)})

    assert result.returncode == 0, result.stderr
    link = bin_dir / "tg"
    assert link.is_symlink()
    assert link.resolve() == (repo / ".venv" / "bin" / "tg").resolve()


def test_idempotent_rerun_keeps_link(tmp_path):
    repo = make_fake_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    env = {"TGCLI_BIN_DIR": str(bin_dir), "TGCLI_REPO": str(repo)}

    assert run_script(env).returncode == 0
    assert run_script(env).returncode == 0
    assert (bin_dir / "tg").is_symlink()


def test_fails_without_venv_entrypoint(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    result = run_script(
        {"TGCLI_BIN_DIR": str(tmp_path / "bin"), "TGCLI_REPO": str(repo)}
    )

    assert result.returncode == 1
    assert "uv sync" in result.stderr
