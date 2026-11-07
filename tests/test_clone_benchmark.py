"""The clone sync benchmark: a fixed channel, its requests and its copies.

`tests/fakes/clone_bench.py` builds the channel and measures a run;
`scripts/bench-clone.py` prints the full report and rewrites the baseline.
"""

import pytest

from tests.fakes import clone_bench

# Wall time is machine-dependent, so it is held only to a ceiling that a
# healthy run clears many times over (about a second on a laptop); request
# counts and state writes are the exact, deterministic cost.
SECONDS_CEILING = 30.0


@pytest.fixture(scope="module")
def baseline():
    return {item["scenario"]: item for item in clone_bench.load_baseline()}


@pytest.mark.parametrize("scenario", clone_bench.SCENARIOS, ids=lambda item: item.name)
def test_clone_sync_benchmark_matches_baseline(scenario, baseline):
    report = clone_bench.run(scenario)

    for row in report["phases"]:
        completeness = row["completeness"]
        assert row["exit"] == 0, row
        assert completeness["missing"] == [], row["phase"]
        assert completeness["duplicated"] == [], row["phase"]
        assert completeness["unexpected"] == [], row["phase"]
        assert completeness["copied"] == completeness["expected"], row["phase"]
        assert row["seconds"] < SECONDS_CEILING, row["phase"]
    drift = clone_bench.drift(
        [baseline[scenario.name]], [clone_bench.deterministic(report)]
    )
    assert drift == [], (
        "clone sync cost moved; if intended, run "
        "`uv run python scripts/bench-clone.py --update` and commit the baseline:\n"
        + "\n".join(drift)
    )


def test_drift_names_each_changed_value():
    old = [
        {
            "scenario": "s",
            "phases": [{"phase": "p", "requests": {"A": 1, "B": 2}, "exit": 0}],
        }
    ]
    new = [
        {
            "scenario": "s",
            "phases": [{"phase": "p", "requests": {"A": 1, "C": 3}, "exit": 0}],
        }
    ]

    assert clone_bench.drift(old, new) == [
        "s/p requests.B: 2 -> (absent)",
        "s/p requests.C: (absent) -> 3",
    ]
    assert clone_bench.drift(old, old) == []


def test_a_skipped_post_is_reported_missing(monkeypatch):
    """Completeness reads what the destination received, so a post the sync
    passes over without sending shows up even though the run exits 0."""
    from tgcli.clone import fidelity

    real = fidelity.unsupported_kind
    monkeypatch.setattr(
        fidelity,
        "unsupported_kind",
        lambda message: "bench" if message.id == 11 else real(message),
    )
    [row] = clone_bench.run(clone_bench.SCENARIOS[1])["phases"]

    assert row["exit"] == 0
    assert row["completeness"]["missing"] == ["post:11"]


def test_more_round_trips_show_as_drift(monkeypatch, baseline):
    """A smaller comments window costs more history reads; the benchmark
    names the request types that moved."""
    from tgcli.clone import legs

    monkeypatch.setattr(legs, "WINDOW", 10)
    report = clone_bench.run(clone_bench.SCENARIOS[0])
    drift = clone_bench.drift(
        [baseline[report["scenario"]]], [clone_bench.deterministic(report)]
    )

    assert any("GetHistoryRequest" in line for line in drift), drift


def test_runs_are_deterministic():
    scenario = clone_bench.SCENARIOS[1]
    first, second = clone_bench.run(scenario), clone_bench.run(scenario)

    assert clone_bench.deterministic(first) == clone_bench.deterministic(second)
