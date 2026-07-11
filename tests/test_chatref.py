from tgcli.chatref import parse


def test_numeric_dialog_ids_become_ints():
    assert parse("-1003890108644") == -1003890108644
    assert parse("12345") == 12345


def test_usernames_links_and_aliases_stay_strings():
    assert parse("@chan") == "@chan"
    assert parse("me") == "me"
    assert parse("t.me/chan") == "t.me/chan"
    assert parse("-not-a-number") == "-not-a-number"
