import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
from tests.fakes.messages import message
from tgcli.cli import main

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"

[accounts.work]
api_id = 12345
api_hash = "abcdef0123456789"
"""

CHANNEL = ns(id=-1001234, title="Channel", username=None)
FRIEND = ns(id=42, first_name="Friend", username=None)


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


class InboxClient(FakeClient):
    """Each dialog has its own history; the base fake has one shared list."""

    def __init__(self, dialogs, histories):
        super().__init__(dialogs=dialogs)
        self.histories = histories
        self.reads = []

    async def iter_messages(
        self,
        entity,
        search=None,
        limit=None,
        reverse=False,
        min_id=0,
        offset_id=0,
        offset_date=None,
        reply_to=None,
        from_user=None,
    ):
        self.reads.append((entity.id, min_id, reverse))
        items = sorted(
            (item for item in self.histories[entity.id] if item.id > min_id),
            key=lambda item: item.id,
            reverse=not reverse,
        )
        for item in items[:limit]:
            yield item


def dialog(entity, history, *, unread, read_marker, kind_channel):
    return ns(
        id=entity.id,
        name=getattr(entity, "title", None) or entity.first_name,
        entity=entity,
        is_channel=kind_channel,
        is_group=False,
        unread_count=unread,
        dialog=ns(read_inbox_max_id=read_marker),
        message=max(history, key=lambda item: item.id, default=None),
    )


def client(channel_ids, *, friend_unread=0, channel_unread=1, read_marker=10):
    histories = {
        CHANNEL.id: [message(i, CHANNEL.id) for i in channel_ids],
        FRIEND.id: [message(i, FRIEND) for i in range(1, 7)],
    }
    dialogs = [
        dialog(
            CHANNEL,
            histories[CHANNEL.id],
            unread=channel_unread,
            read_marker=read_marker,
            kind_channel=True,
        ),
        dialog(
            FRIEND,
            histories[FRIEND.id],
            unread=friend_unread,
            read_marker=5,
            kind_channel=False,
        ),
    ]
    return InboxClient(dialogs, histories)


def run(monkeypatch, capsys, fake, *argv):
    make_session_fake(monkeypatch, fake)
    code = main(["--json", "inbox", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out) if out else {}


def ids(data):
    return {d["id"]: [m["id"] for m in d["messages"]] for d in data["dialogs"]}


def test_first_read_starts_after_read_marker_oldest_first(
    config_env, monkeypatch, capsys, tmp_path
):
    fake = client(range(8, 14))
    code, data = run(monkeypatch, capsys, fake, "read")
    assert code == 0
    assert ids(data) == {CHANNEL.id: [11, 12, 13]}
    assert data["dialogs"][0]["after_id"] == 10
    assert data["dialogs"][0]["kind"] == "channel"
    assert data["more"] is False
    # read dialogs are not walked; history is read upward from the marker
    assert fake.reads == [(CHANNEL.id, 10, True)]
    # read never moves a checkpoint
    assert not (tmp_path / "state" / "inbox").exists()


def test_ack_moves_checkpoint_past_read_marker(config_env, monkeypatch, capsys):
    _, first = run(monkeypatch, capsys, client(range(8, 14)), "read")
    code, acked = run(monkeypatch, capsys, client(()), "ack", first["ack"])
    assert code == 0
    assert acked == {"acked": [{"id": CHANNEL.id, "checkpoint": 13}]}

    # Telegram's marker is still 10: only the checkpoint knows 11-13 were seen
    _, second = run(monkeypatch, capsys, client(range(8, 15)), "read")
    assert ids(second) == {CHANNEL.id: [14]}
    assert second["dialogs"][0]["after_id"] == 13


def test_capped_page_is_the_oldest_and_resumes_after_ack(
    config_env, monkeypatch, capsys
):
    _, page = run(monkeypatch, capsys, client(range(11, 16)), "read", "--limit", "2")
    assert ids(page) == {CHANNEL.id: [11, 12]}
    assert page["more"] is True
    run(monkeypatch, capsys, client(()), "ack", page["ack"])
    _, rest = run(monkeypatch, capsys, client(range(11, 16)), "read", "--limit", "2")
    assert ids(rest) == {CHANNEL.id: [13, 14]}


def test_ack_never_moves_a_checkpoint_back(config_env, monkeypatch, capsys):
    _, old = run(monkeypatch, capsys, client(range(11, 13)), "read")
    _, new = run(monkeypatch, capsys, client(range(11, 16)), "read")
    run(monkeypatch, capsys, client(()), "ack", new["ack"])
    _, acked = run(monkeypatch, capsys, client(()), "ack", old["ack"])
    assert acked == {"acked": [{"id": CHANNEL.id, "checkpoint": 15}]}


def test_each_dialog_keeps_its_own_checkpoint(config_env, monkeypatch, capsys):
    _, data = run(monkeypatch, capsys, client(range(11, 13), friend_unread=1), "read")
    assert ids(data) == {CHANNEL.id: [11, 12], FRIEND.id: [6]}
    _, acked = run(monkeypatch, capsys, client(()), "ack", data["ack"])
    assert acked["acked"] == [
        {"id": CHANNEL.id, "checkpoint": 12},
        {"id": FRIEND.id, "checkpoint": 6},
    ]


def test_acked_dialog_with_nothing_newer_skips_the_history_read(
    config_env, monkeypatch, capsys
):
    _, data = run(monkeypatch, capsys, client(range(11, 13)), "read")
    run(monkeypatch, capsys, client(()), "ack", data["ack"])
    fake = client(range(11, 13))
    _, again = run(monkeypatch, capsys, fake, "read")
    assert again["dialogs"] == []
    assert fake.reads == []


def test_acked_dialog_is_walked_even_when_read_elsewhere(
    config_env, monkeypatch, capsys
):
    _, data = run(monkeypatch, capsys, client(range(11, 16), friend_unread=1), "read")
    run(monkeypatch, capsys, client(()), "ack", data["ack"])
    # the channel got 16-17 and was read in another client; the friend has
    # nothing after its checkpoint
    fake = client(range(11, 18), channel_unread=0, read_marker=17)
    _, again = run(monkeypatch, capsys, fake, "read")
    assert ids(again) == {CHANNEL.id: [16, 17]}
    assert again["dialogs"][0]["after_id"] == 15
    assert fake.reads == [(CHANNEL.id, 15, True)]


def test_nothing_new_has_no_ack_token(config_env, monkeypatch, capsys):
    code, data = run(monkeypatch, capsys, client(range(1, 11)), "read")
    assert code == 0
    assert data == {"dialogs": [], "more": False, "ack": None}


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        "v1:!!!",
        "v1:WzFd",
        "v1:eyJhY2NvdW50IjogIm1haW4iLCAiZGlhbG9ncyI6IHsiMSI6IC0xfX0",
    ],
)
def test_corrupt_token_is_refused(config_env, monkeypatch, capsys, tmp_path, token):
    # WzFd is `[1]`; the last one carries a negative id
    code, _ = run(monkeypatch, capsys, client(()), "ack", token)
    assert code == 2
    assert not (tmp_path / "state" / "inbox").exists()


def test_token_from_another_account_is_refused(
    config_env, monkeypatch, capsys, tmp_path
):
    _, data = run(
        monkeypatch, capsys, client(range(11, 13)), "read", "--account", "work"
    )
    code, _ = run(monkeypatch, capsys, client(()), "ack", data["ack"])
    assert code == 2
    assert not (tmp_path / "state" / "inbox" / "main.json").exists()


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_non_positive_limit_is_refused(config_env, monkeypatch, capsys, limit):
    fake = client(range(11, 13))
    code, _ = run(monkeypatch, capsys, fake, "read", "--limit", limit)
    assert code == 2
    assert fake.reads == []


def test_ack_is_refused_under_readonly(config_env, monkeypatch, capsys, tmp_path):
    _, data = run(monkeypatch, capsys, client(range(11, 13)), "read")
    monkeypatch.setenv("TGCLI_READONLY", "1")
    code, _ = run(monkeypatch, capsys, client(()), "ack", data["ack"])
    assert code == 2
    assert not (tmp_path / "state" / "inbox").exists()


def test_corrupt_state_is_refused(config_env, monkeypatch, capsys, tmp_path):
    state = tmp_path / "state" / "inbox"
    state.mkdir(parents=True)
    (state / "main.json").write_text("{not json")
    code, _ = run(monkeypatch, capsys, client(range(11, 13)), "read")
    assert code == 2


def test_plain_rows_list_each_message(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, client(range(11, 13)))
    assert main(["--plain", "inbox", "read"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split("\t")[:2] for line in lines] == [
        [str(CHANNEL.id), "11"],
        [str(CHANNEL.id), "12"],
    ]
