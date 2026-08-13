"""JobKind registry invariants for thermos T33."""

import pytest

from tgcli.errors import PolicyError
from tgcli.jobs import model


def test_require_kind_maps_each_workload_to_its_lane():
    assert model.require_kind("archive-transcribe").lane == "local"
    assert model.require_kind("archive-backfill").lane == "telegram"
    assert model.require_kind("archive-sync").lane == "telegram"
    assert model.require_kind("clone-sync").lane == "telegram"


def test_require_kind_rejects_unknown_values():
    with pytest.raises(PolicyError, match="unsupported job kind"):
        model.require_kind("shell-script")


def test_job_kinds_registry_is_the_single_kind_source():
    assert set(model.JOB_KINDS) == {
        "archive-transcribe",
        "archive-backfill",
        "archive-sync",
        "clone-sync",
    }


def test_job_kind_carries_only_name_and_lane():
    kind = model.require_kind("archive-backfill")
    assert kind.name == "archive-backfill"
    assert kind.lane == "telegram"
    assert not hasattr(kind, "tracks_progress")
