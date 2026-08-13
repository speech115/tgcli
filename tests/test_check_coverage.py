import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-coverage.py"


def test_current_features_matrix_covers_the_installed_layer():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    assert "coverage OK: 23 namespaces" in result.stdout


def test_checker_reports_missing_unknown_duplicate_and_bad_status(tmp_path):
    features = tmp_path / "FEATURES.md"
    features.write_text(
        "| TL namespace | Status | Notes |\n"
        "|---|---|---|\n"
        "| account | api | raw TL |\n"
        "| account | wrapped | duplicate |\n"
        "| unknown | future | bad |\n"
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--features", str(features)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "duplicate namespace: account" in result.stderr
    assert "unknown namespace: unknown" in result.stderr
    assert "invalid status for unknown: future" in result.stderr
    assert "missing namespace: auth" in result.stderr


def test_checker_requires_reasons_for_excluded_namespaces(tmp_path):
    features = tmp_path / "FEATURES.md"
    features.write_text(
        "| TL namespace | Status | Notes |\n|---|---|---|\n| auth | excluded | |\n"
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--features", str(features)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "excluded namespace auth needs a reason" in result.stderr


def test_raw_denied_matrix_status_matches_the_api_write_policy(tmp_path):
    features = tmp_path / "FEATURES.md"
    features.write_text((ROOT / "docs" / "FEATURES.md").read_text())

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--features", str(features)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_checker_rejects_a_write_policy_namespace_without_raw_denied_status(tmp_path):
    features = tmp_path / "FEATURES.md"
    features.write_text(
        (ROOT / "docs" / "FEATURES.md")
        .read_text()
        .replace("| account | raw-denied |", "| account | excluded |", 1)
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--features", str(features)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "write policy denies account but matrix status is excluded" in result.stderr


def test_checker_rejects_raw_denied_status_without_write_policy(tmp_path):
    features = tmp_path / "FEATURES.md"
    features.write_text(
        (ROOT / "docs" / "FEATURES.md")
        .read_text()
        .replace("| phone | excluded |", "| phone | raw-denied |", 1)
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--features", str(features)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "matrix marks phone raw-denied but write policy allows it" in result.stderr
