import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_bench():
    spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "bench.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_find_media_message_returns_newest_message_with_media():
    bench = load_bench()
    messages = [
        {"id": 30, "media": None},
        {"id": 20, "media": "MessageMediaPhoto"},
        {"id": 10, "media": "MessageMediaDocument"},
    ]

    assert bench.find_media_message(messages) == 20


def test_find_media_message_returns_none_without_media():
    bench = load_bench()

    assert bench.find_media_message([{"id": 1, "media": None}]) is None
    assert bench.find_media_message([]) is None


def test_classify_pass_fail_and_rate_limit_skip():
    bench = load_bench()

    assert bench.classify(0, allow_rate_limit_skip=False) == "PASS"
    assert bench.classify(1, allow_rate_limit_skip=False) == "FAIL"
    assert bench.classify(5, allow_rate_limit_skip=True) == "SKIP"
    assert bench.classify(5, allow_rate_limit_skip=False) == "FAIL"


def test_format_table_shows_step_status_and_duration():
    bench = load_bench()
    results = [
        {"step": "dialogs", "status": "PASS", "duration_ms": 1234, "detail": ""},
        {"step": "export-subscribers", "status": "SKIP", "duration_ms": 5,
         "detail": "takeout delay"},
    ]

    lines = bench.format_table(results).splitlines()

    assert any("dialogs" in line and "PASS" in line and "1234" in line for line in lines)
    assert any("export-subscribers" in line and "SKIP" in line and "takeout delay" in line
               for line in lines)


def test_build_report_counts_statuses():
    bench = load_bench()
    results = [
        {"step": "a", "status": "PASS", "duration_ms": 1, "detail": ""},
        {"step": "b", "status": "SKIP", "duration_ms": 2, "detail": "no media"},
        {"step": "c", "status": "FAIL", "duration_ms": 3, "detail": "exit 1"},
    ]

    report = bench.build_report("main", results)

    assert report["bench"]["account"] == "main"
    assert report["bench"]["passed"] == 1
    assert report["bench"]["skipped"] == 1
    assert report["bench"]["failed"] == 1
    assert report["bench"]["total_ms"] == 6
    assert report["bench"]["results"] == results
