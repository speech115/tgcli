import json

import pytest

from tests.fakes.clone import save_under
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


def test_status_plain_shows_the_destination_title(capsys):
    _seed(100000001, 111, "Alpha", dest=222, dest_title="[Clone] Alpha")

    code, out = _run(capsys, ["clone", "status", "--plain"])
    assert code == 0
    assert "[Clone] Alpha" in out


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


def test_status_filter_survives_digit_shaped_non_integers():
    """`--123` and superscript digits pass isdigit() but are not int()-able."""
    from tgcli.commands import clone as clone_cmd

    _seed(100000001, 111, "Alpha")

    assert clone_cmd.list_clones("--123")["clones"] == []
    assert clone_cmd.list_clones("²³")["clones"] == []


def _write_corrupt(clone_id):
    directory = state.clones_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{clone_id}.db").write_bytes(b"not a sqlite database")


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


def test_status_unreadable_entry_reports_null_created_at(capsys):
    """the CLI contract: an unreadable entry carries the clone_id and nulls — no
    field of it is an empty string. It still sorts beside readable entries."""
    _seed(100000001, 111, "Alpha", dest=222)
    _write_corrupt("e" * 64)

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    payload = json.loads(out)
    unreadable = next(c for c in payload["clones"] if c.get("unreadable"))
    assert unreadable["created_at"] is None
    assert [key for key, value in unreadable.items() if value == ""] == []


HOSTILE_TITLE = "Al\x1bpha\rX\x08Y\nZ\tW"


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


def test_status_probes_a_pre_adr_0103_slot_under_its_own_file_name(capsys):
    """A slot still under its kind-blind id (migrated only by the next
    init/sync) is reported under that file and probed there,
    not as `missing` under the kind-aware id it has not moved to yet."""
    saved = state.CloneState.new(
        account_user_id=100000001, source_peer_id=111, source_title="Alpha"
    )
    legacy_id = state._kind_blind_clone_id(100000001, 111)
    save_under(saved, legacy_id)

    code, out = _run(capsys, ["clone", "status", "--json"])
    assert code == 0
    (entry,) = json.loads(out)["clones"]
    assert entry["clone_id"] == legacy_id
    assert entry["integrity"] == "ok"
    assert entry["schema_version"] == state.statedb.SCHEMA_VERSION
