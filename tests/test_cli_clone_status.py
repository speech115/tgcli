import json

from tgcli.clone import state


def _seed(account_user_id, source_peer_id, title, *, kind="broadcast",
          dest=None, cursor=0, mapped=()):
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


def test_clone_replaces_legacy_mirror_command(capsys):
    from tgcli.cli import main

    code = main(["mirror", "--help"])
    captured = capsys.readouterr()

    assert code == 1
    assert "invalid choice: 'mirror'" in captured.err
