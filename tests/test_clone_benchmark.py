"""The clone sync benchmark: a fixed channel, its requests and its copies.

`tests/fakes/clone_bench.py` builds the channel and measures a run;
`scripts/bench-clone.py` prints the full report and rewrites the baseline.
"""

import pytest
from telethon.tl import types

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


def test_a_skipped_post_is_reported_missing():
    """Completeness reads what the destination received, so a post the sync
    passes over without sending shows up even though the run exits 0."""

    def plant_geo(source: clone_bench.Source) -> None:
        post = next(item for item in source.posts if item.id == 11)
        post.media = types.MessageMediaGeo(geo=types.GeoPointEmpty())

    [row] = clone_bench.run(clone_bench.SCENARIOS[1], edit=plant_geo)["phases"]

    assert row["exit"] == 0
    assert row["sync"]["copied"] == row["completeness"]["expected"] - 1
    assert row["completeness"]["missing"] == ["post:11"]


def test_a_longer_history_shows_as_drift(baseline):
    """More history costs more pages; the benchmark names the request types
    that moved against the baseline."""
    longer = clone_bench.Scenario(
        "protected-reupload",
        protected=True,
        comments=False,
        phases=(clone_bench.Phase("initial", 220),),
    )
    report = clone_bench.run(longer)
    drift = clone_bench.drift(
        [baseline[longer.name]], [clone_bench.deterministic(report)]
    )

    assert any(
        line.startswith("protected-reupload/initial requests.GetHistory")
        for line in drift
    ), drift


def test_runs_are_deterministic():
    scenario = clone_bench.SCENARIOS[1]
    first, second = clone_bench.run(scenario), clone_bench.run(scenario)

    assert clone_bench.deterministic(first) == clone_bench.deterministic(second)
