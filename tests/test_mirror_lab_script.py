import json
import sys
from copy import deepcopy
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


def test_expanded_canary_retains_ambiguous_checkpoint_after_cleanup(
    tmp_path, monkeypatch
):
    scenario_key = "channel_plain.open_open"
    manifest_path = tmp_path / "expanded.json"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_me(self):
            return NS(id=7)

    account = NS(alias="labacct")
    monkeypatch.setattr(script.config, "load_config", lambda: {})
    monkeypatch.setattr(script.config, "resolve_account", lambda *_: account)
    monkeypatch.setattr(script.session, "client", lambda _account: FakeClient())

    fingerprint = script.mirror_lab.new_compatibility_fingerprint(
        scenario_key,
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=227,
        account_role_binding={
            "operator": {"alias": "labacct", "user_id": 7},
        },
        config_digest="c" * 64,
    )
    monkeypatch.setattr(
        script, "live_scenario_fingerprint", lambda *_: fingerprint
    )

    async def ambiguous_provision(
        _tg, checkpoint, _fingerprint, *, persist, **_kwargs
    ):
        intent = script.mirror_lab.build_scenario_provisioning_intents(
            scenario_key, fingerprint
        )[0]
        current = script.mirror_lab.prepare_scenario_intent(checkpoint, intent)
        current = script.mirror_lab.mark_scenario_intent_dispatched(
            current, intent["intent_key"]
        )
        current = script.mirror_lab.record_scenario_intent_outcome(
            current, intent["intent_key"], "ambiguous"
        )
        persist(current)
        raise ConnectionError("response lost")

    async def no_peer_teardown(_tg, checkpoint, _fingerprint, **_kwargs):
        return deepcopy(checkpoint)

    monkeypatch.setattr(
        script.mirror_lab, "provision_scenario_live", ambiguous_provision
    )
    monkeypatch.setattr(
        script.mirror_lab, "teardown_scenario_peers", no_peer_teardown
    )

    assert script.main(
        [
            "expanded-provision-canary",
            "--manifest",
            str(manifest_path),
            "--scenario",
            scenario_key,
        ]
    ) == 1
    persisted = script.mirror_lab.load_manifest(manifest_path)
    assert scenario_key in persisted["scenarios"]
    states = {
        operation["state"]
        for operation in persisted["scenarios"][scenario_key][
            "outbound_operations"
        ].values()
    }
    assert states == {"ambiguous"}


def test_expanded_canary_blocks_known_forum_topology_before_session(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        script.config,
        "load_config",
        lambda: (_ for _ in ()).throw(AssertionError("config touched")),
    )

    code = script.main(
        [
            "expanded-provision-canary",
            "--manifest",
            str(tmp_path / "expanded.json"),
            "--scenario",
            "channel_forum.open_open",
        ]
    )

    assert code == 2
    assert "telegram_forum_discussion_incompatible" in capsys.readouterr().err


def test_expanded_cleanup_removes_confirmed_checkpoint(tmp_path, monkeypatch):
    scenario_key = "channel_plain.open_open"
    manifest_path = tmp_path / "expanded.json"
    manifest = script.mirror_lab.new_manifest(7)
    fingerprint = script.mirror_lab.new_compatibility_fingerprint(
        scenario_key,
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=227,
        account_role_binding={
            "operator": {"alias": "labacct", "user_id": 7},
        },
        config_digest="c" * 64,
    )
    checkpoint = script.mirror_lab.new_scenario_checkpoint(
        scenario_key, fingerprint
    )
    checkpoint["phase"] = "seed"
    checkpoint["created_peers"] = {
        "source": {"peer_id": 701, "title_marker_verified": True},
    }
    checkpoint["cleanup_obligations"] = [{"peer_role": "source"}]
    for intent in script.mirror_lab.build_scenario_provisioning_intents(
        scenario_key, fingerprint
    ):
        checkpoint["outbound_operations"][intent["intent_key"]] = {
            "method": intent["method"],
            "target_role": intent["target_role"],
            "parameters": deepcopy(intent["parameters"]),
            "state": "confirmed",
        }
    manifest["scenarios"][scenario_key] = checkpoint
    script.mirror_lab.save_manifest(manifest_path, manifest)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_me(self):
            return NS(id=7)

    account = NS(alias="labacct")
    monkeypatch.setattr(script.config, "load_config", lambda: {})
    monkeypatch.setattr(script.config, "resolve_account", lambda *_: account)
    monkeypatch.setattr(script.session, "client", lambda _account: FakeClient())

    async def clean(_tg, current, _fingerprint, *, persist, **_kwargs):
        cleaned = deepcopy(current)
        cleaned["created_peers"] = {}
        cleaned["cleanup_obligations"] = []
        persist(cleaned)
        return cleaned

    monkeypatch.setattr(script.mirror_lab, "teardown_scenario_peers", clean)

    assert script.main(
        [
            "expanded-cleanup",
            "--manifest",
            str(manifest_path),
            "--scenario",
            scenario_key,
        ]
    ) == 0
    persisted = script.mirror_lab.load_manifest(manifest_path)
    assert scenario_key not in persisted["scenarios"]


@pytest.mark.parametrize(
    ("phase", "scenario_key", "verify_attr", "result_field"),
    (
        (
            "expanded-comments-canary",
            "channel_plain.open_open",
            "verify_channel_comment_thread_live",
            "comment_threads",
        ),
        (
            "expanded-forum-canary",
            "forum.open",
            "verify_forum_topics_live",
            "forum_peers",
        ),
    ),
)
def test_expanded_content_canary_verifies_both_sides_and_cleans(
    tmp_path,
    monkeypatch,
    capsys,
    phase,
    scenario_key,
    verify_attr,
    result_field,
):
    manifest_path = tmp_path / "expanded.json"
    fingerprint = script.mirror_lab.new_compatibility_fingerprint(
        scenario_key,
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=227,
        account_role_binding={
            "operator": {"alias": "labacct", "user_id": 7},
        },
        config_digest="c" * 64,
    )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_me(self):
            return NS(id=7)

    account = NS(alias="labacct")
    monkeypatch.setattr(script.config, "load_config", lambda: {})
    monkeypatch.setattr(script.config, "resolve_account", lambda *_: account)
    monkeypatch.setattr(script.session, "client", lambda _account: FakeClient())
    monkeypatch.setattr(
        script, "live_scenario_fingerprint", lambda *_: fingerprint
    )

    async def provision(_tg, checkpoint, _fingerprint, *, persist, **_kwargs):
        current = deepcopy(checkpoint)
        current["phase"] = "seed"
        roles = tuple(
            intent["target_role"]
            for intent in script.mirror_lab.build_scenario_provisioning_intents(
                scenario_key, fingerprint
            )
            if intent["method"]
            in {"messages.createChat", "channels.createChannel"}
        )
        current["created_peers"] = {
            role: {"peer_id": 701 + index, "title_marker_verified": True}
            for index, role in enumerate(roles)
        }
        current["cleanup_obligations"] = [
            {"peer_role": role} for role in roles
        ]
        for intent in script.mirror_lab.build_scenario_provisioning_intents(
            scenario_key, fingerprint
        ):
            current["outbound_operations"][intent["intent_key"]] = {
                "method": intent["method"],
                "target_role": intent["target_role"],
                "parameters": deepcopy(intent["parameters"]),
                "state": "confirmed",
            }
        persist(current)
        return current

    verified = []

    async def verify(_tg, _checkpoint, _fingerprint, *, side, record, **_kwargs):
        verified.append(side)
        for operation in ("post", "comment", "nested_reply"):
            key = f"{side}:{operation}"
            record(key, "prepared")
            record(key, "dispatched")
            record(key, "confirmed")
        return {"side": side, "nested_reply": "confirmed"}

    async def cleanup(_tg, checkpoint, _fingerprint, *, persist, **_kwargs):
        cleaned = deepcopy(checkpoint)
        cleaned["created_peers"] = {}
        cleaned["cleanup_obligations"] = []
        persist(cleaned)
        return cleaned

    monkeypatch.setattr(script.mirror_lab, "provision_scenario_live", provision)
    monkeypatch.setattr(script.mirror_lab, verify_attr, verify)
    monkeypatch.setattr(script.mirror_lab, "teardown_scenario_peers", cleanup)

    assert script.main(
        [
            phase,
            "--manifest",
            str(manifest_path),
            "--scenario",
            scenario_key,
        ]
    ) == 0
    assert verified == ["source", "destination"]
    payload = json.loads(capsys.readouterr().out)
    assert payload[result_field] == ["source", "destination"]
    persisted = script.mirror_lab.load_manifest(manifest_path)
    assert scenario_key not in persisted["scenarios"]


def test_interrupted_content_canary_is_cleanup_only(
    tmp_path, monkeypatch, capsys
):
    scenario_key = "channel_plain.open_open"
    manifest_path = tmp_path / "expanded.json"
    fingerprint = script.mirror_lab.new_compatibility_fingerprint(
        scenario_key,
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=227,
        account_role_binding={
            "operator": {"alias": "labacct", "user_id": 7},
        },
        config_digest="c" * 64,
    )
    manifest = script.mirror_lab.new_manifest(7)
    checkpoint = script.mirror_lab.new_scenario_checkpoint(
        scenario_key, fingerprint
    )
    checkpoint["phase"] = "seed"
    checkpoint["verdicts"] = {
        "canary_operations": {"source:post": {"state": "confirmed"}}
    }
    manifest["scenarios"][scenario_key] = checkpoint
    script.mirror_lab.save_manifest(manifest_path, manifest)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_me(self):
            return NS(id=7)

    account = NS(alias="labacct")
    monkeypatch.setattr(script.config, "load_config", lambda: {})
    monkeypatch.setattr(script.config, "resolve_account", lambda *_: account)
    monkeypatch.setattr(script.session, "client", lambda _account: FakeClient())
    monkeypatch.setattr(
        script, "live_scenario_fingerprint", lambda *_: fingerprint
    )
    monkeypatch.setattr(
        script.mirror_lab,
        "provision_scenario_live",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provision called")
        ),
    )

    assert script.main(
        [
            "expanded-comments-canary",
            "--manifest",
            str(manifest_path),
            "--scenario",
            scenario_key,
        ]
    ) == 2
    assert "cleanup-only" in capsys.readouterr().err
