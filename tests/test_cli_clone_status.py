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
        source_kind=kind,
    )
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


def test_status_reports_the_accounts_governor_cooldown(capsys):
    """`cooldown_until` is the latest live ledger deadline for that account."""
    from datetime import UTC, datetime, timedelta

    from tgcli.governor.ledger import Ledger

    _seed(42, 111, "Alpha", dest=222)
    _seed(43, 333, "Beta", dest=444)
    later = datetime.now(UTC) + timedelta(minutes=20)
    with Ledger.open() as ledger:
        ledger.arm_cooldown(
            42, "messages.GetHistoryRequest", later - timedelta(minutes=10)
        )
        ledger.arm_cooldown(42, "messages.SendMessageRequest", later)
        ledger.arm_cooldown(
            43, "messages.GetHistoryRequest", datetime.now(UTC) - timedelta(minutes=1)
        )

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    clones = {c["source"]["title"]: c for c in json.loads(out)["clones"]}
    assert datetime.fromisoformat(clones["Alpha"]["cooldown_until"]) == later
    assert clones["Beta"]["cooldown_until"] is None  # expired, not cooling


def test_status_does_not_create_the_governor_ledger(capsys):
    from tgcli.governor.ledger import default_path

    _seed(42, 111, "Alpha", dest=222)

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    assert json.loads(out)["clones"][0]["cooldown_until"] is None
    assert not default_path().exists()


def test_status_empty_when_no_clones(capsys):
    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    assert json.loads(out) == {"clones": []}


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        ("broadcast", "-1001234567890"),
        ("megagroup", "-1001234567890"),
        ("forum", "-1001234567890"),
        ("basic", "-1234567890"),
        ("dialog", "1234567890"),
    ],
)
def test_status_filters_by_kind_aware_source_token(capsys, kind, source):
    _seed(100000001, 1234567890, "Alpha", kind=kind)

    code, out = _run(capsys, ["clone", "status", source, "--json"])

    assert code == 0
    assert [c["source"]["title"] for c in json.loads(out)["clones"]] == ["Alpha"]


@pytest.mark.parametrize(
    ("kind", "wrong_sources"),
    [
        ("broadcast", ("1234567890", "-1234567890")),
        ("megagroup", ("1234567890", "-1234567890")),
        ("forum", ("1234567890", "-1234567890")),
        ("basic", ("1234567890", "-1001234567890")),
        ("dialog", ("-1234567890", "-1001234567890")),
    ],
)
def test_status_filter_rejects_other_peer_class_tokens(capsys, kind, wrong_sources):
    _seed(100000001, 1234567890, "Alpha", kind=kind)

    for source in wrong_sources:
        code, out = _run(capsys, ["clone", "status", source, "--json"])
        assert code == 0
        assert json.loads(out)["clones"] == []


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


def _write_corrupt(clone_id):
    directory = state.clones_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{clone_id}.db").write_bytes(b"not a sqlite database")


def test_status_ignores_a_legacy_json_document(capsys):
    """Pre-SQLite JSON state is no longer read or imported (ADR-0060)."""
    _seed(100000001, 111, "Alpha", dest=222)
    directory = state.clones_dir()
    (directory / f"{'a' * 64}.json").write_text(json.dumps({"version": 2}))
    (directory / f"{'b' * 64}.json.importing").write_text("{}")

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]
    assert not (directory / f"{'a' * 64}.db").exists()


def test_status_marks_unreadable_state_instead_of_crashing(capsys):
    _seed(100000001, 111, "Alpha", dest=222)
    legacy = "a" * 64
    _write_corrupt(legacy)

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    by_id = {c["clone_id"]: c for c in payload["clones"]}
    assert by_id[legacy]["unreadable"] is True
    alpha = next(c for c in payload["clones"] if c["source"]["title"] == "Alpha")
    assert alpha.get("unreadable", False) is False


def test_status_marks_corrupt_state_instead_of_crashing(capsys):
    _write_corrupt("b" * 64)

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["clones"][0]["unreadable"] is True


def test_status_plain_output_flags_unreadable(capsys):
    legacy = "c" * 64
    _write_corrupt(legacy)

    code, out = _run(capsys, ["clone", "status", "--plain"])
    assert code == 0
    assert "unreadable" in out
    assert legacy[:12] in out


def test_status_filter_excludes_unreadable(capsys):
    _seed(100000001, 111, "Alpha")
    _write_corrupt("d" * 64)

    code, out = _run(capsys, ["clone", "status", "-1000000000111", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert [c["source"]["title"] for c in payload["clones"]] == ["Alpha"]


def test_status_unreadable_entry_reports_null_created_at(capsys):
    """CONTRACT §11: an unreadable entry carries the clone_id and nulls — no
    field of it is an empty string. It still sorts beside readable entries."""
    _seed(100000001, 111, "Alpha", dest=222)
    _write_corrupt("e" * 64)

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    unreadable = next(c for c in payload["clones"] if c.get("unreadable"))
    assert unreadable["created_at"] is None
    assert [key for key, value in unreadable.items() if value == ""] == []


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


def test_status_flags_a_clone_whose_discussion_never_linked(capsys):
    """#170: a commit that flooded on the link leaves the clone half-built."""
    half = _seed(100000001, 111, "Alpha", dest=222)
    half.comments = "enabled"
    half.discussion_source_peer_id = 777
    state.save(half)

    from tgcli.cli import main

    assert main(["clone", "status", "--json"]) == 0
    captured = capsys.readouterr()
    entry = json.loads(captured.out)["clones"][0]
    assert entry["discussion_linked"] is False
    assert "tg clone init 111" in captured.err


def test_status_says_nothing_about_a_fully_linked_clone(capsys):
    linked = _seed(100000001, 111, "Alpha", dest=222)
    linked.comments = "enabled"
    linked.discussion_source_peer_id = 777
    linked.discussion_destination_peer_id = 888
    linked.discussion_linked = True
    state.save(linked)

    from tgcli.cli import main

    assert main(["clone", "status", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["clones"][0]["discussion_linked"] is True
    assert "clone init" not in captured.err


def test_status_reports_an_empty_slot_without_writing_a_schema_into_it(capsys):
    """A slot file tgcli never wrote (0 bytes, e.g. left by another tool) is
    reported as empty; a read does not initialize it into a healthy-looking
    schema-3 database with no state in it."""
    directory = state.clones_dir()
    directory.mkdir(parents=True, exist_ok=True)
    empty = directory / f"{'f' * 64}.db"
    empty.write_bytes(b"")

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    (entry,) = json.loads(out)["clones"]
    assert entry["unreadable"] is True
    assert entry["schema_version"] is None
    assert "empty" in entry["integrity"]
    assert empty.read_bytes() == b""
