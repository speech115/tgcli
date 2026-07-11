import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "mirror_probe.py"


def test_help_describes_read_only_contract():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "read-only" in result.stdout.lower()
    assert "--output" in result.stdout


def test_output_is_required():
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "example",
            "--account",
            "main",
            "--role",
            "owned",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--output" in result.stderr


def load_script():
    spec = importlib.util.spec_from_file_location("mirror_probe_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_writes_the_same_redacted_report_to_stdout_and_file(
    tmp_path, monkeypatch, capsys
):
    module = load_script()
    report = {
        "probe_version": 1,
        "runtime": {"telethon": "test", "telegram_layer": 1},
        "source": {
            "fingerprint": "0123456789abcdef",
            "protected": True,
            "role": "owned",
        },
        "capabilities": [],
    }

    async def fake_run(args):
        assert args.chat == "private-source"
        assert args.account == "main"
        return report

    monkeypatch.setattr(module, "run", fake_run)
    destination = tmp_path / "report.json"
    exit_code = module.main(
        [
            "private-source",
            "--account",
            "main",
            "--role",
            "owned",
            "--limit",
            "20",
            "--samples-per-kind",
            "3",
            "--output",
            str(destination),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert json.loads(captured.out) == report
    assert json.loads(destination.read_text()) == report
