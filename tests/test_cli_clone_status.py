import json

import pytest

from tgcli.clone import state


def _seed(
    account_user_id,
    source_peer_id,
    title,
    *,
    kind="broadcast",
    dest=None,
    dest_title=None,
    dest_username=None,
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
    s.destination_title = dest_title
    s.destination_username = dest_username
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
    assert alpha["destination"] == {"id": 222, "title": None, "username": None}
    assert alpha["cursor"] == 5
    assert alpha["copied"] == 2
    beta = next(c for c in payload["clones"] if c["source"]["title"] == "Beta")
    assert alpha["source"]["kind"] == "broadcast"
    assert beta["source"]["kind"] == "megagroup"


def test_status_names_the_destination_it_recorded(capsys):
    """#175: a private clone destination is unrecognisable as a bare id."""
    _seed(
        100000001,
        111,
        "Alpha",
        dest=222,
        dest_title="[Clone] Alpha",
        dest_username="alpha_clone",
    )

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    entry = json.loads(out)["clones"][0]
    assert entry["destination"] == {
        "id": 222,
        "title": "[Clone] Alpha",
        "username": "alpha_clone",
    }


def test_status_plain_shows_the_destination_title(capsys):
    _seed(100000001, 111, "Alpha", dest=222, dest_title="[Clone] Alpha")

    code, out = _run(capsys, ["clone", "status", "--plain"])
    assert code == 0
    assert "[Clone] Alpha" in out


def test_status_plain_falls_back_to_the_destination_id(capsys):
    """A clone initialised before the title was recorded stays legible."""
    _seed(100000001, 111, "Alpha", dest=222)

    code, out = _run(capsys, ["clone", "status", "--plain"])
    assert code == 0
    assert "222" in out


def test_status_survives_an_armed_governor_cooldown(capsys):
    """Status is read-only: an armed ledger cooldown must not block it."""
    from datetime import UTC, datetime, timedelta

    from tgcli.governor.ledger import Ledger

    _seed(42, 111, "Alpha", dest=222)
    with Ledger.open() as ledger:
        ledger.arm_cooldown(
            42,
            "messages.GetHistoryRequest",
            datetime.now(UTC) + timedelta(minutes=10),
        )

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]
    assert not any(c.get("unreadable") for c in payload["clones"])


def test_status_empty_when_no_clones(capsys):
    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    assert json.loads(out) == {"clones": [], "pending_import": 0}


def test_status_filters_by_source_id(capsys):
    _seed(100000001, 111, "Alpha")
    _seed(100000001, 333, "Beta")

    code, out = _run(capsys, ["clone", "status", "333", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Beta"]


def test_status_filters_by_marked_channel_source_id(capsys):
    """CONTRACT-shaped JSON hands out -100 ids; status must accept that form."""
    _seed(100000001, 3890108644, "Alpha")
    _seed(100000001, 333, "Beta")

    code, out = _run(capsys, ["clone", "status", "-1003890108644", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]


def test_status_filters_by_raw_source_id(capsys):
    _seed(100000001, 3890108644, "Alpha")
    _seed(100000001, 333, "Beta")

    code, out = _run(capsys, ["clone", "status", "3890108644", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]


def test_status_filter_survives_digit_shaped_non_integers():
    """`--123` and superscript digits pass isdigit() but are not int()-able."""
    from tgcli.commands import clone as clone_cmd

    _seed(100000001, 111, "Alpha")

    assert clone_cmd.list_clones("--123")["clones"] == []
    assert clone_cmd.list_clones("²³")["clones"] == []


def test_status_filter_keeps_int_shaped_titles_on_the_substring_path():
    """int() accepts `+123`, `1_000`, and padding; isdigit() is the gate, so
    those keep searching titles instead of silently matching nothing."""
    from tgcli.commands import clone as clone_cmd

    _seed(100000001, 123, "Channel_1_000_subs backup")

    assert clone_cmd.list_clones("1_000")["clones"] != []
    assert clone_cmd.list_clones("+123")["clones"] == []
    assert clone_cmd.list_clones(" 123 ")["clones"] == []


def test_status_filters_by_title_substring(capsys):
    _seed(100000001, 111, "Alpha")
    _seed(100000001, 333, "Beta")

    code, out = _run(capsys, ["clone", "status", "lph", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]


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


def test_status_hides_unimportable_json_slots_behind_a_count(capsys):
    """#173: a slot v2 cannot import carries no field worth a row."""
    _seed(100000001, 111, "Alpha", dest=222)
    _write_raw("a" * 64, {"version": 1})
    _write_raw("b" * 64, {"version": 1})

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]
    assert payload["pending_import"] == 2


def test_status_points_at_all_when_slots_are_hidden(capsys):
    _write_raw("a" * 64, {"version": 1})

    from tgcli.cli import main

    assert main(["clone", "status", "--json"]) == 0
    assert "clone status --all" in capsys.readouterr().err


def test_status_all_lists_the_hidden_slots_and_still_counts_them(capsys):
    _seed(100000001, 111, "Alpha", dest=222)
    _write_raw("a" * 64, {"version": 1})

    code, out = _run(capsys, ["clone", "status", "--all", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert {c["clone_id"] for c in payload["clones"]} == {
        "a" * 64,
        state.clone_id(100000001, 111),
    }
    assert payload["pending_import"] == 1


def test_status_counts_hidden_slots_even_under_a_source_filter(capsys):
    """The slots exist whether or not the filter could ever match them."""
    _seed(100000001, 111, "Alpha", dest=222)
    _write_raw("a" * 64, {"version": 1})

    code, out = _run(capsys, ["clone", "status", "111", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]
    assert payload["pending_import"] == 1


def test_status_keeps_an_ambiguous_slot_visible_by_default(capsys):
    """A .db+.json collision is actionable, unlike an unimportable .json."""
    seeded = _seed(100000001, 111, "Alpha", dest=222)
    _write_raw(seeded.clone_id, {"version": 2})

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["clones"][0]["unreadable"] is True
    assert payload["pending_import"] == 0


def test_status_marks_unreadable_state_instead_of_crashing(capsys):
    _seed(100000001, 111, "Alpha", dest=222)
    legacy = "a" * 64
    _write_raw(legacy, {"version": 1, "account_user_id": 1, "source_peer_id": 9})

    code, out = _run(capsys, ["clone", "status", "--all", "--json"])
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

    code, out = _run(capsys, ["clone", "status", "--all", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["clones"][0]["unreadable"] is True


@pytest.mark.parametrize("payload", [[], "x", 42, None, True])
def test_status_marks_non_dict_state_instead_of_crashing(capsys, payload):
    _seed(100000001, 111, "Alpha", dest=222)
    broken = "e" * 64
    _write_raw(broken, payload)

    code, out = _run(capsys, ["clone", "status", "--all", "--json"])
    assert code == 0
    listed = json.loads(out)["clones"]
    by_id = {c["clone_id"]: c for c in listed}
    assert by_id[broken]["unreadable"] is True
    assert "Alpha" in {c["source"]["title"] for c in listed}


def test_status_plain_output_flags_unreadable(capsys):
    legacy = "c" * 64
    _write_raw(legacy, {"version": 1})

    code, out = _run(capsys, ["clone", "status", "--all", "--plain"])
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


def test_status_unreadable_entry_reports_null_created_at(capsys):
    """CONTRACT §11: an unreadable entry carries the clone_id and nulls — no
    field of it is an empty string. It still sorts beside readable entries."""
    _seed(100000001, 111, "Alpha", dest=222)
    _write_raw("e" * 64, {"version": 1})

    code, out = _run(capsys, ["clone", "status", "--all", "--json"])
    assert code == 0
    payload = json.loads(out)
    unreadable = next(c for c in payload["clones"] if c.get("unreadable"))
    assert unreadable["created_at"] is None
    assert [key for key, value in unreadable.items() if value == ""] == []


def test_status_marks_ambiguous_db_and_json_as_unreadable(capsys):
    """CONTRACT §11: both .db and .json present for one id is ambiguous
    (manual resolution required); status must report it via the documented
    unreadable shape, never the real (misleadingly healthy) .db diagnostics."""
    seeded = _seed(100000001, 111, "Alpha", dest=222)
    _write_raw(seeded.clone_id, {"version": 2})

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    entry = next(c for c in payload["clones"] if c["clone_id"] == seeded.clone_id)
    assert entry["unreadable"] is True
    assert entry["schema_version"] is None
    assert entry["integrity"] != "ok"
    assert entry["source"]["title"] is None


def test_clone_replaces_legacy_mirror_command(capsys):
    from tgcli.cli import main

    code = main(["mirror", "--help"])
    captured = capsys.readouterr()

    assert code == 1
    assert "invalid choice: 'mirror'" in captured.err


HOSTILE_TITLE = "Al\x1bpha\rX\x08Y\nZ\tW"


def test_status_plain_strips_control_characters_from_the_title(capsys):
    _seed(100000001, 111, HOSTILE_TITLE, dest=222, cursor=5)

    code, out = _run(capsys, ["clone", "status", "--plain"])

    assert code == 0
    assert "AlphaXYZW" in out
    assert not any(ch in out[:-1] for ch in "\x1b\r\x08\n")


def test_status_human_strips_control_characters_from_the_title(capsys):
    _seed(100000001, 111, HOSTILE_TITLE, dest=222, cursor=5)

    code, out = _run(capsys, ["clone", "status"])

    assert code == 0
    assert "AlphaXYZW" in out
    assert not any(ch in out[:-1] for ch in "\x1b\r\x08\n\t")


def test_status_json_passes_control_characters_through(capsys):
    _seed(100000001, 111, HOSTILE_TITLE, dest=222, cursor=5)

    code, out = _run(capsys, ["clone", "status", "--json"])

    assert code == 0
    assert json.loads(out)["clones"][0]["source"]["title"] == HOSTILE_TITLE
