import json

from tgcli.clone import state


def _seed(
    account_user_id,
    source_peer_id,
    title,
    *,
    kind="broadcast",
    dest=None,
    cursor=0,
    mapped=(),
):
    s = state.CloneState.new(
        account_user_id=account_user_id,
        source_peer_id=source_peer_id,
        source_title=title,
    )
    s.source_kind = kind
    s.destination_peer_id = dest
    s.cursor = cursor
    for src_id, dst_id in mapped:
        s.record_mapping(src_id, dst_id)
    state.save(s)
    return s


def _run(capsys, argv):
    from tgcli.cli import main

    code = main(argv)
    out = capsys.readouterr().out
    return code, out


def test_status_lists_all_clones_as_json(capsys):
    _seed(100000001, 111, "Alpha", dest=222, cursor=5, mapped=[(2, 3), (4, 5)])
    _seed(100000001, 333, "Beta", kind="megagroup")

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    titles = {c["source"]["title"] for c in payload["clones"]}
    assert titles == {"Alpha", "Beta"}
    alpha = next(c for c in payload["clones"] if c["source"]["title"] == "Alpha")
    assert alpha["destination_id"] == 222
    assert alpha["cursor"] == 5
    assert alpha["copied"] == 2
    beta = next(c for c in payload["clones"] if c["source"]["title"] == "Beta")
    assert alpha["source"]["kind"] == "broadcast"
    assert beta["source"]["kind"] == "megagroup"


def test_status_ignores_account_flood_sidecar_and_survives_cooldown(capsys):
    from datetime import UTC, datetime, timedelta

    from tgcli.clone import flood

    _seed(42, 111, "Alpha", dest=222)
    flood.arm_cooldown(42, datetime.now(UTC) + timedelta(minutes=10))

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]
    assert not any(c.get("unreadable") for c in payload["clones"])


def test_status_empty_when_no_clones(capsys):
    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    assert json.loads(out) == {"clones": []}


def test_status_filters_by_source_id(capsys):
    _seed(100000001, 111, "Alpha")
    _seed(100000001, 333, "Beta")

    code, out = _run(capsys, ["clone", "status", "333", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Beta"]


def test_status_plain_output(capsys):
    _seed(100000001, 111, "Alpha", kind="megagroup", dest=222, cursor=5)

    code, out = _run(capsys, ["clone", "status", "--plain"])
    assert code == 0
    assert "Alpha" in out
    assert "megagroup" in out
    assert "222" in out


def test_status_reports_comments_state(capsys):
    alpha = _seed(100000001, 111, "Alpha", dest=222)
    alpha.comments = "enabled"
    alpha.discussion_source_peer_id = 777
    state.save(alpha)
    _seed(100000001, 333, "Beta")

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    by_title = {c["source"]["title"]: c["comments"] for c in payload["clones"]}
    assert by_title == {"Alpha": "enabled", "Beta": "none"}


def test_status_plain_output_includes_comments(capsys):
    seeded = _seed(100000001, 111, "Alpha", dest=222)
    seeded.comments = "unavailable"
    seeded.discussion_source_peer_id = 777
    state.save(seeded)

    code, out = _run(capsys, ["clone", "status", "--plain"])
    assert code == 0
    assert "unavailable" in out


def _write_raw(clone_id, payload):
    directory = state.clones_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{clone_id}.json").write_text(json.dumps(payload))


def test_status_marks_unreadable_state_instead_of_crashing(capsys):
    _seed(100000001, 111, "Alpha", dest=222)
    legacy = "a" * 64
    _write_raw(legacy, {"version": 1, "account_user_id": 1, "source_peer_id": 9})

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    by_id = {c["clone_id"]: c for c in payload["clones"]}
    assert by_id[legacy]["unreadable"] is True
    alpha = next(c for c in payload["clones"] if c["source"]["title"] == "Alpha")
    assert alpha.get("unreadable", False) is False


def test_status_marks_corrupt_state_instead_of_crashing(capsys):
    directory = state.clones_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{'b' * 64}.json").write_text("{not json")

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["clones"][0]["unreadable"] is True


def test_status_plain_output_flags_unreadable(capsys):
    legacy = "c" * 64
    _write_raw(legacy, {"version": 1})

    code, out = _run(capsys, ["clone", "status", "--plain"])
    assert code == 0
    assert "unreadable" in out
    assert legacy[:12] in out


def test_status_filter_excludes_unreadable(capsys):
    _seed(100000001, 111, "Alpha")
    _write_raw("d" * 64, {"version": 1})

    code, out = _run(capsys, ["clone", "status", "111", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]


def test_clone_replaces_legacy_mirror_command(capsys):
    from tgcli.cli import main

    code = main(["mirror", "--help"])
    captured = capsys.readouterr()

    assert code == 1
    assert "invalid choice: 'mirror'" in captured.err
