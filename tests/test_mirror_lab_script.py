import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mirror_lab as script  # noqa: E402


def test_parse_args_requires_subcommand_and_manifest():
    with pytest.raises(SystemExit):
        script.parse_args([])
    args = script.parse_args(["create", "--manifest", "/tmp/lab.json"])
    assert args.phase == "create"
    assert args.manifest == "/tmp/lab.json"


def test_probe_requires_role_and_output():
    with pytest.raises(SystemExit):
        script.parse_args(["probe", "--manifest", "m.json"])
    args = script.parse_args(
        ["probe", "--manifest", "m.json", "--role", "dest_native",
         "--output", "out.json"]
    )
    assert args.role == "dest_native"


def test_verdict_is_pure_and_needs_no_session(tmp_path, capsys):
    source = tmp_path / "source.json"
    dest = tmp_path / "dest.json"
    row = {
        "kind": "video", "sample_count": 1, "coverage": "complete",
        "telethon_bytes": "pass",
        "samples": [{"kind": "video", "decode": "pass",
                     "telethon_bytes": "pass", "sha256": "v1",
                     "bytes": 1, "error": None}],
    }
    source.write_text(json.dumps({"capabilities": [row]}))
    dest.write_text(json.dumps({"capabilities": [row]}))
    manifest = script.mirror_lab.new_manifest(7)
    script.mirror_lab.record_seed(manifest, "open_source", "video", [1])
    manifest_path = tmp_path / "lab.json"
    script.mirror_lab.save_manifest(manifest_path, manifest)
    code = script.main([
        "verdict", "--manifest", str(manifest_path),
        "--source-report", str(source), "--dest-report", str(dest),
        "--transport", "native",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "green"


def test_policy_error_maps_to_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_READONLY", "1")

    class FakeAccount(NS):
        pass

    def fake_resolve(config, alias):
        return FakeAccount(alias="labacct")

    class FakeClient:
        async def __aenter__(self):
            return NS(get_me=self._me)

        async def __aexit__(self, *exc):
            return False

        @staticmethod
        async def _me():
            return NS(id=7)

    monkeypatch.setattr(script.config, "load_config", lambda: {})
    monkeypatch.setattr(script.config, "resolve_account", fake_resolve)
    monkeypatch.setattr(script.session, "client", lambda account: FakeClient())

    code = script.main(["create", "--manifest", str(tmp_path / "lab.json")])
    assert code == 2
    assert "policy" in capsys.readouterr().err.lower()


def test_mutation_kill_switch_precedes_config_and_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    monkeypatch.setattr(
        script.config,
        "load_config",
        lambda: (_ for _ in ()).throw(AssertionError("config touched")),
    )

    code = script.main(["create", "--manifest", str(tmp_path / "lab.json")])

    assert code == 2
    assert "policy" in capsys.readouterr().err.lower()


def test_seed_fixture_preflight_precedes_config_and_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        script.mirror_lab,
        "preflight_fixture_tools",
        lambda: (_ for _ in ()).throw(ValueError("ffmpeg unavailable")),
        raising=False,
    )
    monkeypatch.setattr(
        script.config,
        "load_config",
        lambda: (_ for _ in ()).throw(AssertionError("config touched")),
    )

    code = script.main(["seed", "--manifest", str(tmp_path / "lab.json")])

    assert code == 4
    assert "ffmpeg unavailable" in capsys.readouterr().err
