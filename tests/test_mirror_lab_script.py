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


def test_verdict_rejects_partial_matrix_and_needs_no_session(tmp_path, capsys):
    source = tmp_path / "source.json"
    dest = tmp_path / "dest.json"
    row = {
        "kind": "video", "sample_count": 1, "coverage": "complete",
        "telethon_bytes": "pass",
        "samples": [{"kind": "video", "decode": "pass",
                     "telethon_bytes": "pass", "sha256": "v1",
                     "bytes": 1, "error": None}],
    }
    source.write_text(json.dumps({"probe_version": 2, "capabilities": [row]}))
    dest.write_text(json.dumps({"probe_version": 2, "capabilities": [row]}))
    copy = tmp_path / "copy.json"
    copy.write_text(json.dumps({
        "transport": "native",
        "restricted_check": "confirmed",
        "results": {"video": "forwarded"},
    }))
    manifest = script.mirror_lab.new_manifest(7)
    script.mirror_lab.record_seed(manifest, "open_source", "video", [1])
    manifest_path = tmp_path / "lab.json"
    script.mirror_lab.save_manifest(manifest_path, manifest)
    code = script.main([
        "verdict", "--manifest", str(manifest_path),
        "--source-report", str(source), "--dest-report", str(dest),
        "--transport", "native", "--copy-report", str(copy),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "red"
    assert payload["copy_gate"] == "fail"

    copy.write_text(json.dumps({
        "transport": "native",
        "restricted_check": "unexpected_success",
        "results": {"video": "forwarded"},
    }))
    code = script.main([
        "verdict", "--manifest", str(manifest_path),
        "--source-report", str(source), "--dest-report", str(dest),
        "--transport", "native", "--copy-report", str(copy),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "red"
    assert payload["copy_gate"] == "fail"


def test_verdict_accepts_complete_native_matrix(tmp_path):
    capabilities = []
    for kind in script.mirror_lab.planned_kinds():
        if kind == "album":
            continue
        byte_kind = kind in script.mirror_lab.BYTE_FIXTURES
        state = "pass" if byte_kind else "not_applicable"
        sha = f"{kind}-sha" if byte_kind else None
        capabilities.append({
            "kind": kind,
            "sample_count": 1,
            "coverage": "complete",
            "telethon_bytes": state,
            "samples": [{
                "kind": kind,
                "decode": "pass",
                "telethon_bytes": state,
                "sha256": sha,
                "bytes": 1 if sha else None,
                "error": None,
            }],
        })
    report = {
        "probe_version": 2,
        "capabilities": capabilities,
        "album_groups": [{
            "count": 2,
            "sha256": ["album-a", "album-b"],
            "telethon_bytes": ["pass", "pass"],
        }],
    }
    source = tmp_path / "source.json"
    dest = tmp_path / "dest.json"
    copy = tmp_path / "copy.json"
    source.write_text(json.dumps(report))
    dest.write_text(json.dumps(report))
    copy.write_text(json.dumps({
        "transport": "native",
        "restricted_check": "confirmed",
        "results": {
            kind: "forwarded" for kind in script.mirror_lab.planned_kinds()
        },
    }))
    manifest = script.mirror_lab.new_manifest(7)
    for message_id, kind in enumerate(script.mirror_lab.planned_kinds(), 1):
        script.mirror_lab.record_seed(manifest, "open_source", kind, [message_id])
    manifest_path = tmp_path / "lab.json"
    script.mirror_lab.save_manifest(manifest_path, manifest)

    result = script.run_verdict(NS(
        source_report=str(source),
        dest_report=str(dest),
        copy_report=str(copy),
        transport="native",
        manifest=str(manifest_path),
        role=None,
    ))

    assert result["verdict"] == "green"
    assert result["copy_gate"] == "pass"


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
