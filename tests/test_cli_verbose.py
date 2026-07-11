import json
from tgcli.cli import main


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


def test_verbose_diagnostics_do_not_leak_into_the_next_invocation(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    assert main(["accounts", "list", "--verbose"]) == 0
    assert "DEBUG tgcli.cli: completed command=accounts exit_code=0 duration_ms=" in capsys.readouterr().err

    assert main(["--json", "accounts", "list"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["default_account"] == "main"
    assert captured.err == ""
