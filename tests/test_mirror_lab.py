import json
import hashlib
import os
import subprocess
import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from tgcli import mirror_lab as lab_module

REAL_MATERIALIZE_FIXTURE = getattr(lab_module, "materialize_fixture", None)


@pytest.fixture(autouse=True)
def lightweight_fixture_materializer(monkeypatch):
    def materialize(kind, directory, *, color=(0, 0, 255)):
        fixture = lab_module.BYTE_FIXTURES[kind]
        path = Path(directory) / fixture.filename
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = build_png(color) if kind == "photo" else fixture.payload()
        path.write_bytes(payload)
        return path

    monkeypatch.setattr(lab_module, "materialize_fixture", materialize)

from tgcli.errors import PolicyError
from tgcli.mirror_lab import (
    CHANNEL_ROLES,
    LAB_MARKER,
    assert_lab_peer,
    lab_title,
    lab_peer_ids,
    load_manifest,
    new_manifest,
    record_channel,
    record_seed,
    save_manifest,
    seeded_ids,
)


def classification_fingerprint(scenario_key="basic.open", **overrides):
    values = {
        "fixture_schema_version": 1,
        "lab_code_digest": "a" * 64,
        "mirror_code_digest": "b" * 64,
        "telethon_version": "1.44.0",
        "telegram_schema_layer": 216,
        "account_role_binding": {
            "operator": {"alias": "main", "user_id": 101},
        },
        "config_digest": "c" * 64,
    }
    values.update(overrides)
    return lab_module.new_compatibility_fingerprint(scenario_key, **values)


def classification_checkpoint(
    scenario_key="basic.open",
    *,
    fingerprint=None,
    completed_at=None,
    domain_status="green",
):
    fingerprint = fingerprint or classification_fingerprint(scenario_key)
    checkpoint = lab_module.new_scenario_checkpoint(scenario_key, fingerprint)
    checkpoint["phase"] = "complete"
    spec = next(spec for spec in lab_module.required_scenarios() if spec.key == scenario_key)
    checkpoint["verdicts"] = {
        "completed_at": completed_at or datetime(2026, 7, 1, tzinfo=UTC),
        "domains": {
            domain: domain_status
            for domain in lab_module.required_scenario_domains(spec)
        },
    }
    return checkpoint


def classification_fingerprint_matrix():
    return {
        spec.key: classification_fingerprint(spec.key)
        for spec in lab_module.required_scenarios()
    }


def test_new_manifest_shape():
    manifest = new_manifest(account_user_id=42)
    assert manifest["manifest_version"] == 3
    assert len(manifest["lab_id"]) == 24
    assert manifest["account_user_id"] == 42
    assert manifest["channels"] == {}
    assert manifest["seeded"] == {}
    assert manifest["scenarios"] == {}
    assert manifest["created_at"]


def test_manifest_round_trip(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_valid_v2_manifest_migrates_in_memory_without_rewrite_or_evidence(tmp_path):
    legacy = new_manifest(7)
    legacy["manifest_version"] = 2
    legacy.pop("scenarios")
    legacy["seeded"] = {"open_source": {"photo": [11]}}
    legacy["blocked"] = {"open_source": {"poll": "blocked"}}
    path = tmp_path / "lab.json"
    save_manifest(path, legacy)
    before = path.read_bytes()

    loaded = load_manifest(path)

    assert loaded["manifest_version"] == 3
    assert loaded["scenarios"] == {}
    assert loaded["seeded"] == legacy["seeded"]
    assert loaded["blocked"] == legacy["blocked"]
    assert path.read_bytes() == before


def test_load_manifest_rejects_unknown_version(tmp_path):
    path = tmp_path / "lab.json"
    path.write_text('{"manifest_version": 99}')
    with pytest.raises(ValueError):
        load_manifest(path)


def test_v2_manifest_rejects_scenario_payload(tmp_path):
    legacy = new_manifest(7)
    legacy["manifest_version"] = 2
    legacy["scenarios"] = {}
    path = tmp_path / "lab.json"
    save_manifest(path, legacy)

    with pytest.raises(ValueError, match="v2.*scenarios"):
        load_manifest(path)


def test_manifest_version_requires_an_exact_integer(tmp_path):
    manifest = new_manifest(7)
    manifest["manifest_version"] = 3.0
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="unsupported lab manifest"):
        load_manifest(path)


def test_compatibility_fingerprint_shape_and_defensive_role_copy():
    roles = {
        "operator": {"alias": "main", "user_id": 101},
        "lab_peer": {"alias": "lab-peer", "user_id": 202},
    }
    fingerprint = lab_module.new_compatibility_fingerprint(
        "basic.open",
        fixture_schema_version=4,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=216,
        account_role_binding=roles,
        config_digest="c" * 64,
    )
    assert fingerprint == {
        "fingerprint_version": 1,
        "scenario_key": "basic.open",
        "fixture_schema_version": 4,
        "lab_code_digest": "a" * 64,
        "mirror_code_digest": "b" * 64,
        "telethon_version": "1.44.0",
        "telegram_schema_layer": 216,
        "account_role_binding": {
            "operator": {"alias": "main", "user_id": 101},
            "lab_peer": {"alias": "lab-peer", "user_id": 202},
        },
        "config_digest": "c" * 64,
    }
    roles["operator"]["alias"] = "mutated"
    assert fingerprint["account_role_binding"]["operator"]["alias"] == "main"


def test_scenario_checkpoint_shape_defensive_copy_and_v3_round_trip(tmp_path):
    fingerprint = lab_module.new_compatibility_fingerprint(
        "channel.open",
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=216,
        account_role_binding={"operator": {"alias": "main", "user_id": 101}},
        config_digest="c" * 64,
    )
    checkpoint = lab_module.new_scenario_checkpoint("channel.open", fingerprint)
    assert checkpoint == {
        "checkpoint_version": 1,
        "phase": "preflight",
        "created_peers": {},
        "created_topics": {},
        "outbound_operations": {},
        "verdicts": {},
        "cleanup_obligations": [],
        "compatibility_fingerprint": fingerprint,
    }
    fingerprint["account_role_binding"]["operator"]["alias"] = "mutated"
    assert checkpoint["compatibility_fingerprint"]["account_role_binding"]["operator"]["alias"] == "main"

    manifest = new_manifest(7)
    manifest["scenarios"]["channel.open"] = checkpoint
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    assert load_manifest(path) == manifest


def test_scenario_checkpoint_rejects_mismatched_fingerprint_key():
    fingerprint = lab_module.new_compatibility_fingerprint(
        "basic.open",
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=216,
        account_role_binding={"operator": {"alias": "main", "user_id": 101}},
        config_digest="c" * 64,
    )

    with pytest.raises(ValueError, match="scenario key mismatch"):
        lab_module.new_scenario_checkpoint("basic.protected", fingerprint)

    assert fingerprint["scenario_key"] == "basic.open"


def test_select_scenarios_returns_frozen_all_order_or_one_exact_target():
    all_specs = lab_module.select_scenarios()
    assert all_specs == lab_module.required_scenarios()
    assert lab_module.select_scenarios("forum.protected") == (
        next(spec for spec in all_specs if spec.key == "forum.protected"),
    )

    with pytest.raises(ValueError, match="unknown scenario key"):
        lab_module.select_scenarios("forum.missing")


def test_scenario_resume_phase_validates_and_reports_first_unconfirmed_phase():
    fingerprint = classification_fingerprint()
    checkpoint = lab_module.new_scenario_checkpoint("basic.open", fingerprint)
    assert lab_module.scenario_resume_phase(checkpoint) == "preflight"

    malformed = deepcopy(checkpoint)
    malformed["checkpoint_version"] = 2
    with pytest.raises(ValueError, match="checkpoint version"):
        lab_module.scenario_resume_phase(malformed)


def test_advance_scenario_phase_is_sequential_pure_and_cleanup_gated():
    fingerprint = classification_fingerprint()
    checkpoint = lab_module.new_scenario_checkpoint("basic.open", fingerprint)

    advanced = lab_module.advance_scenario_phase(checkpoint, "preflight")
    assert checkpoint["phase"] == "preflight"
    assert advanced["phase"] == "create"
    assert advanced is not checkpoint
    assert (
        advanced["compatibility_fingerprint"]
        is not checkpoint["compatibility_fingerprint"]
    )

    with pytest.raises(ValueError, match="phase confirmation mismatch"):
        lab_module.advance_scenario_phase(advanced, "seed")

    advanced["phase"] = "teardown"
    advanced["cleanup_obligations"] = [{"peer": "pending"}]
    advanced["verdicts"] = {"domains": {"cleanup": "blocked"}}
    with pytest.raises(ValueError, match="cleanup is not green"):
        lab_module.advance_scenario_phase(advanced, "teardown")

    advanced["cleanup_obligations"] = []
    advanced["verdicts"]["domains"]["cleanup"] = "green"
    complete = lab_module.advance_scenario_phase(advanced, "teardown")
    assert complete["phase"] == "complete"
    assert lab_module.advance_scenario_phase(complete, "complete") == complete


def test_basic_preflight_requires_lab_peer_and_normalizes_destination():
    fingerprint = classification_fingerprint(
        "basic.open",
        account_role_binding={
            "operator": {"alias": "main", "user_id": 101},
            "lab_peer": {"alias": "lab-peer", "user_id": 202},
        },
    )
    assert lab_module.build_scenario_preflight_plan(
        "basic.open", fingerprint
    ) == {
        "scenario_key": "basic.open",
        "source_family": "basic",
        "source_protected": False,
        "destination_family": "supergroup",
        "destination_private": True,
        "destination_owner_only": True,
        "discussion_kind": "none",
        "discussion_protected": None,
        "required_account_roles": ("operator", "lab_peer"),
        "basic_group_normalization": True,
        "content_profile": "full",
        "required_domains": lab_module.required_scenario_domains(
            lab_module.select_scenarios("basic.open")[0]
        ),
    }

    missing_peer = classification_fingerprint("basic.open")
    with pytest.raises(ValueError, match="missing required account role: lab_peer"):
        lab_module.build_scenario_preflight_plan("basic.open", missing_peer)


@pytest.mark.parametrize(
    ("scenario_key", "source_family", "protected", "discussion", "discussion_protected"),
    (
        ("forum.protected", "forum", True, "none", None),
        ("channel.open", "channel", False, "none", None),
        ("channel_plain.open_protected", "channel", False, "plain", True),
        ("channel_forum.protected_open", "channel", True, "forum", False),
    ),
)
def test_preflight_preserves_exact_topology_and_protection_cell(
    scenario_key, source_family, protected, discussion, discussion_protected
):
    fingerprint = classification_fingerprint(
        scenario_key,
        account_role_binding={
            "operator": {"alias": "operator-secret", "user_id": 987654321},
        },
    )
    plan = lab_module.build_scenario_preflight_plan(scenario_key, fingerprint)

    assert plan["source_family"] == source_family
    assert plan["source_protected"] is protected
    assert plan["discussion_kind"] == discussion
    assert plan["discussion_protected"] is discussion_protected
    assert plan["required_account_roles"] == ("operator",)
    assert plan["destination_private"] is True
    assert plan["destination_owner_only"] is True
    assert "operator-secret" not in repr(plan)
    assert "987654321" not in repr(plan)


def test_preflight_rejects_cross_cell_fingerprint():
    fingerprint = classification_fingerprint("forum.open")
    with pytest.raises(ValueError, match="fingerprint scenario key mismatch"):
        lab_module.build_scenario_preflight_plan("forum.protected", fingerprint)


def test_basic_provisioning_intents_use_create_chat_and_supergroup_destination():
    fingerprint = classification_fingerprint(
        "basic.open",
        account_role_binding={
            "operator": {"alias": "operator-secret", "user_id": 101},
            "lab_peer": {"alias": "peer-secret", "user_id": 202},
        },
    )
    intents = lab_module.build_scenario_provisioning_intents(
        "basic.open", fingerprint
    )

    assert [intent["method"] for intent in intents] == [
        "messages.createChat",
        "channels.createChannel",
    ]
    assert intents[0]["target_role"] == "source"
    assert intents[0]["parameters"]["participant_roles"] == ("lab_peer",)
    assert intents[0]["parameters"]["owner_only"] is False
    assert intents[1]["target_role"] == "destination"
    assert intents[1]["parameters"]["megagroup"] is True
    assert intents[1]["parameters"]["forum"] is False
    assert intents[1]["parameters"]["owner_only"] is True
    assert "operator-secret" not in repr(intents)
    assert "peer-secret" not in repr(intents)
    assert "101" not in repr(intents)
    assert "202" not in repr(intents)


def test_forum_provisioning_intents_preserve_forum_and_source_protection():
    fingerprint = classification_fingerprint("forum.protected")
    intents = lab_module.build_scenario_provisioning_intents(
        "forum.protected", fingerprint
    )

    assert [intent["method"] for intent in intents] == [
        "channels.createChannel",
        "messages.toggleNoForwards",
        "channels.createChannel",
    ]
    assert intents[0]["parameters"]["forum"] is True
    assert intents[1]["target_role"] == "source"
    assert intents[1]["parameters"] == {"enabled": True}
    assert intents[2]["parameters"]["forum"] is True


@pytest.mark.parametrize("discussion_kind", ("plain", "forum"))
def test_linked_channel_intents_create_and_link_matching_discussions(
    discussion_kind,
):
    scenario_key = f"channel_{discussion_kind}.protected_protected"
    fingerprint = classification_fingerprint(scenario_key)
    intents = lab_module.build_scenario_provisioning_intents(
        scenario_key, fingerprint
    )
    by_key = {intent["intent_key"]: intent for intent in intents}

    assert len(by_key) == len(intents)
    assert by_key[f"{scenario_key}:create:source"]["parameters"]["broadcast"] is True
    assert by_key[f"{scenario_key}:create:destination"]["parameters"]["broadcast"] is True
    for side in ("source", "destination"):
        create = by_key[f"{scenario_key}:create:{side}_discussion"]
        assert create["parameters"]["megagroup"] is True
        assert create["parameters"]["forum"] is (discussion_kind == "forum")
        eligibility = by_key[f"{scenario_key}:verify:{side}_discussion_eligibility"]
        assert eligibility["method"] == "channels.getGroupsForDiscussion"
        assert eligibility["parameters"] == {
            "candidate_group_role": f"{side}_discussion"
        }
        link = by_key[f"{scenario_key}:link:{side}_discussion"]
        assert link["method"] == "channels.setDiscussionGroup"
        assert link["parameters"] == {
            "broadcast_role": side,
            "group_role": f"{side}_discussion",
        }
        assert intents.index(eligibility) < intents.index(link)
    protected_roles = {
        intent["target_role"]
        for intent in intents
        if intent["method"] == "messages.toggleNoForwards"
    }
    assert protected_roles == {"source", "source_discussion"}


def test_all_provisioning_intent_keys_are_stable_and_unique_per_cell():
    for spec in lab_module.required_scenarios():
        bindings = {"operator": {"alias": "main", "user_id": 101}}
        if spec.source_family == "basic":
            bindings["lab_peer"] = {"alias": "lab-peer", "user_id": 202}
        fingerprint = classification_fingerprint(
            spec.key, account_role_binding=bindings
        )
        intents = lab_module.build_scenario_provisioning_intents(
            spec.key, fingerprint
        )
        keys = [intent["intent_key"] for intent in intents]
        assert len(keys) == len(set(keys))
        assert all(key.startswith(f"{spec.key}:") for key in keys)


def test_channel_forum_live_gate_is_explicitly_blocked_by_controlled_evidence():
    blocker = lab_module.scenario_live_blocker("channel_forum.open_open")

    assert blocker == {
        "reason_code": "telegram_forum_discussion_incompatible",
        "evidence": (
            "created_forum_not_discussion_eligible",
            "linked_plain_group_forum_toggle_rejected",
        ),
    }
    assert lab_module.scenario_live_blocker("channel_plain.open_open") is None
    assert lab_module.scenario_live_blocker("forum.open") is None


def provisioning_checkpoint(scenario_key="forum.protected"):
    fingerprint = classification_fingerprint(scenario_key)
    checkpoint = lab_module.new_scenario_checkpoint(scenario_key, fingerprint)
    checkpoint["phase"] = "create"
    intent = lab_module.build_scenario_provisioning_intents(
        scenario_key, fingerprint
    )[0]
    return checkpoint, intent


def test_prepare_scenario_intent_is_pure_idempotent_and_exact():
    checkpoint, intent = provisioning_checkpoint()
    prepared = lab_module.prepare_scenario_intent(checkpoint, intent)

    assert checkpoint["outbound_operations"] == {}
    assert prepared is not checkpoint
    assert prepared["outbound_operations"][intent["intent_key"]] == {
        "method": intent["method"],
        "target_role": intent["target_role"],
        "parameters": intent["parameters"],
        "state": "prepared",
    }
    assert lab_module.prepare_scenario_intent(prepared, intent) == prepared

    forged = deepcopy(intent)
    forged["method"] = "messages.sendMessage"
    with pytest.raises(ValueError, match="intent does not match scenario plan"):
        lab_module.prepare_scenario_intent(checkpoint, forged)


def test_prepare_scenario_intent_enforces_serial_plan_order():
    scenario_key = "channel_forum.protected_protected"
    fingerprint = classification_fingerprint(scenario_key)
    checkpoint = lab_module.new_scenario_checkpoint(scenario_key, fingerprint)
    checkpoint["phase"] = "create"
    intents = lab_module.build_scenario_provisioning_intents(
        scenario_key, fingerprint
    )

    with pytest.raises(ValueError, match="prior provisioning intent is incomplete"):
        lab_module.prepare_scenario_intent(checkpoint, intents[-1])


def test_intent_dispatch_and_outcome_transitions_are_fail_closed():
    checkpoint, intent = provisioning_checkpoint()
    prepared = lab_module.prepare_scenario_intent(checkpoint, intent)
    key = intent["intent_key"]

    dispatched = lab_module.mark_scenario_intent_dispatched(prepared, key)
    assert dispatched["outbound_operations"][key]["state"] == "dispatched"
    assert prepared["outbound_operations"][key]["state"] == "prepared"

    ambiguous = lab_module.record_scenario_intent_outcome(
        dispatched, key, "ambiguous"
    )
    assert ambiguous["outbound_operations"][key]["state"] == "ambiguous"
    with pytest.raises(ValueError, match="intent is not prepared"):
        lab_module.mark_scenario_intent_dispatched(ambiguous, key)

    with pytest.raises(ValueError, match="invalid intent outcome"):
        lab_module.record_scenario_intent_outcome(dispatched, key, "success")


def test_ambiguous_intent_requires_evidence_backed_reconciliation():
    checkpoint, intent = provisioning_checkpoint()
    key = intent["intent_key"]
    prepared = lab_module.prepare_scenario_intent(checkpoint, intent)
    dispatched = lab_module.mark_scenario_intent_dispatched(prepared, key)
    ambiguous = lab_module.record_scenario_intent_outcome(
        dispatched, key, "ambiguous"
    )

    still_ambiguous = lab_module.reconcile_scenario_intent(
        ambiguous, key, observed=None
    )
    assert still_ambiguous["outbound_operations"][key]["state"] == "ambiguous"

    confirmed = lab_module.reconcile_scenario_intent(
        ambiguous, key, observed=True
    )
    assert confirmed["outbound_operations"][key]["state"] == "confirmed"

    retryable = lab_module.reconcile_scenario_intent(
        ambiguous, key, observed=False
    )
    assert retryable["outbound_operations"][key]["state"] == "prepared"
    assert (
        lab_module.mark_scenario_intent_dispatched(retryable, key)
        ["outbound_operations"][key]["state"]
        == "dispatched"
    )


def test_create_phase_advances_only_after_every_intent_is_confirmed():
    checkpoint, _ = provisioning_checkpoint("forum.open")
    fingerprint = checkpoint["compatibility_fingerprint"]
    intents = lab_module.build_scenario_provisioning_intents(
        "forum.open", fingerprint
    )

    with pytest.raises(ValueError, match="provisioning intents are incomplete"):
        lab_module.advance_scenario_phase(checkpoint, "create")

    for intent in intents:
        checkpoint = lab_module.prepare_scenario_intent(checkpoint, intent)
        checkpoint = lab_module.mark_scenario_intent_dispatched(
            checkpoint, intent["intent_key"]
        )
        checkpoint = lab_module.record_scenario_intent_outcome(
            checkpoint, intent["intent_key"], "confirmed"
        )

    with pytest.raises(ValueError, match="created peer evidence is incomplete"):
        lab_module.advance_scenario_phase(checkpoint, "create")
    for intent in intents:
        if intent["method"] in {"messages.createChat", "channels.createChannel"}:
            checkpoint["created_peers"][intent["target_role"]] = {
                "peer_id": len(checkpoint["created_peers"]) + 1,
                "title_marker_verified": True,
            }
            checkpoint["cleanup_obligations"].append(
                {"peer_role": intent["target_role"]}
            )
    advanced = lab_module.advance_scenario_phase(checkpoint, "create")
    assert advanced["phase"] == "seed"


@pytest.mark.asyncio
async def test_dispatch_scenario_intent_persists_before_and_after_fake_execute():
    checkpoint, intent = provisioning_checkpoint("channel.open")
    persisted = []
    executed = []

    def persist(value):
        persisted.append(deepcopy(value))

    async def execute(value):
        executed.append(deepcopy(value))
        return {
            "peer_role": "source",
            "peer_id": 501,
            "title_marker_verified": True,
        }

    result = await lab_module.dispatch_scenario_intent(
        checkpoint,
        intent,
        persist=persist,
        execute=execute,
    )
    key = intent["intent_key"]

    assert [item["outbound_operations"][key]["state"] for item in persisted] == [
        "prepared",
        "dispatched",
        "confirmed",
    ]
    assert executed == [intent]
    assert result["outbound_operations"][key]["state"] == "confirmed"
    assert result["created_peers"] == {
        "source": {"peer_id": 501, "title_marker_verified": True}
    }
    assert result["cleanup_obligations"] == [{"peer_role": "source"}]
    assert checkpoint["outbound_operations"] == {}


@pytest.mark.asyncio
async def test_dispatch_does_not_execute_when_pre_dispatch_persistence_fails():
    checkpoint, intent = provisioning_checkpoint("channel.open")
    executed = []

    def persist(_value):
        raise OSError("disk unavailable")

    async def execute(value):
        executed.append(value)

    with pytest.raises(OSError, match="disk unavailable"):
        await lab_module.dispatch_scenario_intent(
            checkpoint,
            intent,
            persist=persist,
            execute=execute,
        )
    assert executed == []


@pytest.mark.asyncio
async def test_dispatch_records_ambiguous_fake_exception_and_requires_reconcile():
    checkpoint, intent = provisioning_checkpoint("channel.open")
    persisted = []

    def persist(value):
        persisted.append(deepcopy(value))

    async def execute(_value):
        raise ConnectionError("response lost")

    with pytest.raises(ConnectionError, match="response lost"):
        await lab_module.dispatch_scenario_intent(
            checkpoint,
            intent,
            persist=persist,
            execute=execute,
        )
    key = intent["intent_key"]
    ambiguous = persisted[-1]
    assert ambiguous["outbound_operations"][key]["state"] == "ambiguous"

    executed = False

    async def must_not_execute(_value):
        nonlocal executed
        executed = True

    with pytest.raises(PolicyError, match="requires reconciliation"):
        await lab_module.dispatch_scenario_intent(
            ambiguous,
            intent,
            persist=persist,
            execute=must_not_execute,
        )
    assert executed is False


@pytest.mark.asyncio
async def test_dispatch_invalid_observation_stays_ambiguous():
    checkpoint, intent = provisioning_checkpoint("channel.open")
    persisted = []

    def persist(value):
        persisted.append(deepcopy(value))

    async def execute(_value):
        return None

    with pytest.raises(ValueError, match="created peer observation"):
        await lab_module.dispatch_scenario_intent(
            checkpoint,
            intent,
            persist=persist,
            execute=execute,
        )
    key = intent["intent_key"]
    assert persisted[-1]["outbound_operations"][key]["state"] == "ambiguous"


@pytest.mark.asyncio
async def test_mocked_live_provisioning_readback_and_teardown_are_complete():
    class ScenarioTG:
        def __init__(self):
            self.next_id = 700
            self.peers = {}
            self.links = {}
            self.deleted = []

        async def __call__(self, request):
            if isinstance(request, lab_module.functions.channels.CreateChannelRequest):
                self.next_id += 1
                peer = NS(
                    id=self.next_id,
                    title=request.title,
                    creator=True,
                    broadcast=bool(request.broadcast),
                    megagroup=bool(request.megagroup),
                    forum=bool(request.forum),
                    noforwards=False,
                )
                self.peers[peer.id] = peer
                return NS(chats=[peer])
            if isinstance(request, lab_module.functions.messages.ToggleNoForwardsRequest):
                request.peer.noforwards = request.enabled
                return NS(updates=[])
            if isinstance(request, lab_module.functions.channels.GetGroupsForDiscussionRequest):
                return NS(chats=[peer for peer in self.peers.values() if peer.megagroup])
            if isinstance(request, lab_module.functions.channels.SetDiscussionGroupRequest):
                self.links[request.broadcast.id] = request.group.id
                return NS(updates=[])
            if isinstance(request, lab_module.functions.channels.GetFullChannelRequest):
                return NS(full_chat=NS(linked_chat_id=self.links.get(request.channel.id)))
            if isinstance(request, lab_module.functions.channels.DeleteChannelRequest):
                self.deleted.append(request.channel.id)
                self.peers.pop(request.channel.id)
                return NS(updates=[])
            raise AssertionError(type(request).__name__)

        async def get_input_entity(self, ref):
            return self.peers[ref.channel_id]

        async def get_entity(self, ref):
            peer_id = getattr(ref, "channel_id", ref)
            return self.peers[peer_id]

    scenario_key = "channel_plain.protected_protected"
    fingerprint = classification_fingerprint(scenario_key)
    checkpoint = lab_module.new_scenario_checkpoint(scenario_key, fingerprint)
    checkpoint["phase"] = "create"
    persisted = []

    def persist(value):
        persisted.append(deepcopy(value))

    tg = ScenarioTG()
    provisioned = await lab_module.provision_scenario_live(
        tg,
        checkpoint,
        fingerprint,
        lab_id="d" * 24,
        account_alias="labacct",
        persist=persist,
    )

    assert provisioned["phase"] == "seed"
    assert set(provisioned["created_peers"]) == {
        "source",
        "source_discussion",
        "destination",
        "destination_discussion",
    }
    assert all(
        operation["state"] == "confirmed"
        for operation in provisioned["outbound_operations"].values()
    )
    assert tg.peers[provisioned["created_peers"]["source"]["peer_id"]].noforwards
    assert tg.peers[
        provisioned["created_peers"]["source_discussion"]["peer_id"]
    ].noforwards
    assert len(tg.links) == 2
    assert not tg.peers[
        provisioned["created_peers"]["source_discussion"]["peer_id"]
    ].forum
    assert not tg.peers[
        provisioned["created_peers"]["destination_discussion"]["peer_id"]
    ].forum

    cleaned = await lab_module.teardown_scenario_peers(
        tg,
        provisioned,
        fingerprint,
        lab_id="d" * 24,
        account_alias="labacct",
        persist=persist,
    )
    assert cleaned["created_peers"] == {}
    assert cleaned["cleanup_obligations"] == []
    assert len(tg.deleted) == 4


@pytest.mark.asyncio
async def test_scenario_teardown_reconciles_persisted_delete_after_flood_wait():
    scenario_key = "channel.open"
    fingerprint = classification_fingerprint(scenario_key)
    checkpoint = lab_module.new_scenario_checkpoint(scenario_key, fingerprint)
    checkpoint["phase"] = "seed"
    checkpoint["created_peers"] = {
        "destination": {"peer_id": 702, "title_marker_verified": True},
    }
    checkpoint["cleanup_obligations"] = [{"peer_role": "destination"}]
    persisted = []

    class FloodingTG:
        async def get_entity(self, _ref):
            return NS(
                id=702,
                title=lab_module.scenario_peer_title(
                    "e" * 24, scenario_key, "destination"
                ),
                creator=True,
            )

        async def __call__(self, request):
            assert isinstance(
                request, lab_module.functions.channels.DeleteChannelRequest
            )
            raise FloodWaitError(request=None, capture=600)

    with pytest.raises(FloodWaitError):
        await lab_module.teardown_scenario_peers(
            FloodingTG(),
            checkpoint,
            fingerprint,
            lab_id="e" * 24,
            account_alias="labacct",
            persist=lambda value: persisted.append(deepcopy(value)),
        )
    assert persisted[-1]["cleanup_obligations"] == [
        {"peer_role": "destination", "state": "deleting"}
    ]

    class AlreadyDeletedTG:
        async def get_entity(self, _ref):
            raise ChannelPrivateError(request=None)

    cleaned = await lab_module.teardown_scenario_peers(
        AlreadyDeletedTG(),
        persisted[-1],
        fingerprint,
        lab_id="e" * 24,
        account_alias="labacct",
        persist=lambda value: persisted.append(deepcopy(value)),
    )
    assert cleaned["created_peers"] == {}
    assert cleaned["cleanup_obligations"] == []


def test_materialize_basic_and_forum_create_requests_without_dispatch():
    lab_id = "a" * 24
    basic_fingerprint = classification_fingerprint(
        "basic.open",
        account_role_binding={
            "operator": {"alias": "main", "user_id": 101},
            "lab_peer": {"alias": "lab-peer", "user_id": 202},
        },
    )
    basic_intents = lab_module.build_scenario_provisioning_intents(
        "basic.open", basic_fingerprint
    )
    lab_peer = object()
    source_request = lab_module.materialize_scenario_intent_request(
        basic_intents[0],
        basic_fingerprint,
        lab_id=lab_id,
        resolved_roles={"lab_peer": lab_peer},
    )
    assert isinstance(source_request, lab_module.functions.messages.CreateChatRequest)
    assert source_request.users == [lab_peer]
    assert source_request.title == f"{LAB_MARKER} {lab_id} basic.open source"

    destination_request = lab_module.materialize_scenario_intent_request(
        basic_intents[1],
        basic_fingerprint,
        lab_id=lab_id,
        resolved_roles={},
    )
    assert isinstance(
        destination_request, lab_module.functions.channels.CreateChannelRequest
    )
    assert destination_request.broadcast is False
    assert destination_request.megagroup is True
    assert destination_request.forum is False

    forum_fingerprint = classification_fingerprint("forum.open")
    forum_intent = lab_module.build_scenario_provisioning_intents(
        "forum.open", forum_fingerprint
    )[0]
    forum_request = lab_module.materialize_scenario_intent_request(
        forum_intent,
        forum_fingerprint,
        lab_id=lab_id,
        resolved_roles={},
    )
    assert forum_request.megagroup is True
    assert forum_request.forum is True


def test_materialize_protection_eligibility_and_link_requests_without_dispatch():
    scenario_key = "channel_forum.protected_protected"
    fingerprint = classification_fingerprint(scenario_key)
    intents = {
        intent["method"]: intent
        for intent in lab_module.build_scenario_provisioning_intents(
            scenario_key, fingerprint
        )
        if intent["method"] != "channels.createChannel"
    }
    source = object()
    source_discussion = object()
    resolved = {
        "source": source,
        "source_discussion": source_discussion,
        "destination": object(),
        "destination_discussion": object(),
    }

    protection = lab_module.materialize_scenario_intent_request(
        intents["messages.toggleNoForwards"],
        fingerprint,
        lab_id="b" * 24,
        resolved_roles=resolved,
    )
    assert isinstance(
        protection, lab_module.functions.messages.ToggleNoForwardsRequest
    )
    assert protection.peer is source_discussion
    assert protection.enabled is True

    eligibility = lab_module.materialize_scenario_intent_request(
        intents["channels.getGroupsForDiscussion"],
        fingerprint,
        lab_id="b" * 24,
        resolved_roles=resolved,
    )
    assert isinstance(
        eligibility,
        lab_module.functions.channels.GetGroupsForDiscussionRequest,
    )

    link = lab_module.materialize_scenario_intent_request(
        intents["channels.setDiscussionGroup"],
        fingerprint,
        lab_id="b" * 24,
        resolved_roles=resolved,
    )
    assert isinstance(link, lab_module.functions.channels.SetDiscussionGroupRequest)
    assert link.broadcast is resolved["destination"]
    assert link.group is resolved["destination_discussion"]

def test_materialize_request_rejects_forged_intent_and_missing_role():
    fingerprint = classification_fingerprint("channel.protected")
    intents = lab_module.build_scenario_provisioning_intents(
        "channel.protected", fingerprint
    )
    forged = deepcopy(intents[0])
    forged["method"] = "messages.sendMessage"
    with pytest.raises(ValueError, match="intent does not match scenario plan"):
        lab_module.materialize_scenario_intent_request(
            forged,
            fingerprint,
            lab_id="c" * 24,
            resolved_roles={},
        )

    with pytest.raises(ValueError, match="missing resolved role: source"):
        lab_module.materialize_scenario_intent_request(
            intents[1],
            fingerprint,
            lab_id="c" * 24,
            resolved_roles={},
        )


def test_compare_compatibility_fingerprints_reports_all_mismatches_in_order():
    expected = classification_fingerprint()
    actual = classification_fingerprint(
        "basic.protected",
        fixture_schema_version=2,
        lab_code_digest="d" * 64,
        mirror_code_digest="e" * 64,
        telethon_version="1.45.0",
        telegram_schema_layer=217,
        account_role_binding={
            "operator": {"alias": "other", "user_id": 202},
        },
        config_digest="f" * 64,
    )
    actual["fingerprint_version"] = 2

    assert lab_module.compare_compatibility_fingerprints(expected, actual) == {
        "compatible": False,
        "reasons": [
            "fingerprint_version_mismatch",
            "scenario_key_mismatch",
            "fixture_schema_version_mismatch",
            "lab_code_digest_mismatch",
            "mirror_code_digest_mismatch",
            "telethon_version_mismatch",
            "telegram_schema_layer_mismatch",
            "account_role_binding_mismatch",
            "config_digest_mismatch",
        ],
    }


def test_compare_compatibility_fingerprints_exact_and_malformed_inputs():
    fingerprint = classification_fingerprint()
    assert lab_module.compare_compatibility_fingerprints(
        fingerprint, deepcopy(fingerprint)
    ) == {"compatible": True, "reasons": []}

    malformed = deepcopy(fingerprint)
    malformed.pop("config_digest")
    with pytest.raises(ValueError):
        lab_module.compare_compatibility_fingerprints(fingerprint, malformed)

    malformed_key = deepcopy(fingerprint)
    malformed_key["scenario_key"] = 7
    with pytest.raises(ValueError):
        lab_module.compare_compatibility_fingerprints(fingerprint, malformed_key)

    unsupported_expected = deepcopy(fingerprint)
    unsupported_expected["fingerprint_version"] = 2
    with pytest.raises(ValueError):
        lab_module.compare_compatibility_fingerprints(
            unsupported_expected, fingerprint
        )


def test_required_scenario_domains_are_topology_specific_and_ordered():
    specs = {spec.key: spec for spec in lab_module.required_scenarios()}
    universal = (
        "content",
        "structure",
        "attribution",
        "transport",
        "audit",
        "cleanup",
    )
    assert lab_module.required_scenario_domains(specs["basic.open"]) == universal
    assert lab_module.required_scenario_domains(specs["channel.open"]) == universal
    assert lab_module.required_scenario_domains(specs["forum.open"]) == (
        *universal,
        "topic",
    )
    assert lab_module.required_scenario_domains(
        specs["channel_plain.open_open"]
    ) == (*universal, "comment_root")
    assert lab_module.required_scenario_domains(
        specs["channel_forum.open_open"]
    ) == (*universal, "topic", "comment_root")


def test_missing_exact_scenario_cell_stays_missing():
    expected = classification_fingerprint("basic.protected")
    assert lab_module.classify_scenario_cell(
        "basic.protected",
        None,
        expected,
        now=datetime(2026, 7, 13, tzinfo=UTC),
    ) == {
        "scenario_key": "basic.protected",
        "status": "missing",
        "freshness": "missing",
        "machine_compatible": False,
        "reasons": ["checkpoint_missing"],
        "fingerprint_reasons": [],
        "completed_at": None,
        "expires_at": None,
        "domains": {},
        "cleanup": "missing",
    }


def test_missing_unknown_scenario_cell_rejects_instead_of_looking_missing():
    expected = classification_fingerprint("unknown.open")

    with pytest.raises(ValueError, match="unknown scenario key"):
        lab_module.classify_scenario_cell(
            "unknown.open",
            None,
            expected,
            now=datetime(2026, 7, 13, tzinfo=UTC),
        )


def test_fingerprint_mismatch_is_immediately_stale_with_exact_reasons():
    expected = classification_fingerprint()
    actual = classification_fingerprint(lab_code_digest="d" * 64)
    checkpoint = classification_checkpoint(fingerprint=actual)
    completed_at = checkpoint["verdicts"]["completed_at"]

    result = lab_module.classify_scenario_cell(
        "basic.open",
        checkpoint,
        expected,
        now=completed_at + timedelta(days=1),
    )

    assert result["status"] == "stale"
    assert result["freshness"] == "fresh"
    assert result["machine_compatible"] is False
    assert result["reasons"] == ["lab_code_digest_mismatch"]
    assert result["fingerprint_reasons"] == ["lab_code_digest_mismatch"]
    assert result["cleanup"] == "green"


def test_evidence_is_fresh_before_ttl_and_stale_at_exact_boundary():
    fingerprint = classification_fingerprint()
    checkpoint = classification_checkpoint(fingerprint=fingerprint)
    completed_at = checkpoint["verdicts"]["completed_at"]

    fresh = lab_module.classify_scenario_cell(
        "basic.open",
        checkpoint,
        fingerprint,
        now=completed_at + timedelta(days=30) - timedelta(microseconds=1),
    )
    assert fresh["status"] == "machine_compatible"
    assert fresh["freshness"] == "fresh"
    assert fresh["expires_at"] == completed_at + timedelta(days=30)

    boundary = lab_module.classify_scenario_cell(
        "basic.open",
        checkpoint,
        fingerprint,
        now=completed_at + timedelta(days=30),
    )
    assert boundary["status"] == "stale"
    assert boundary["freshness"] == "stale"
    assert boundary["machine_compatible"] is False
    assert boundary["reasons"] == ["evidence_expired"]


@pytest.mark.parametrize(
    ("case", "reason"),
    (
        ("unavailable", "clock_unavailable"),
        ("now_type", "clock_invalid"),
        ("now_naive", "clock_invalid"),
        ("completion_type", "clock_invalid"),
        ("completion_naive", "clock_invalid"),
        ("before_completion", "clock_before_completion"),
        ("observed_type", "clock_invalid"),
        ("observed_naive", "clock_invalid"),
        ("moved_backward", "clock_moved_backward"),
    ),
)
def test_invalid_and_backward_clocks_block(case, reason):
    fingerprint = classification_fingerprint()
    completed_at = datetime(2026, 7, 1, tzinfo=UTC)
    checkpoint = classification_checkpoint(
        fingerprint=fingerprint, completed_at=completed_at
    )
    now = completed_at + timedelta(days=1)
    last_observed_at = None
    if case == "unavailable":
        now = None
    elif case == "now_type":
        now = "2026-07-02"
    elif case == "now_naive":
        now = datetime(2026, 7, 2)
    elif case == "completion_type":
        checkpoint["verdicts"]["completed_at"] = "2026-07-01"
    elif case == "completion_naive":
        checkpoint["verdicts"]["completed_at"] = datetime(2026, 7, 1)
    elif case == "before_completion":
        now = completed_at - timedelta(seconds=1)
    elif case == "observed_type":
        last_observed_at = "2026-07-01"
    elif case == "observed_naive":
        last_observed_at = datetime(2026, 7, 1)
    elif case == "moved_backward":
        last_observed_at = now + timedelta(seconds=1)

    result = lab_module.classify_scenario_cell(
        "basic.open",
        checkpoint,
        fingerprint,
        now=now,
        last_observed_at=last_observed_at,
    )

    assert result["status"] == "blocked"
    assert result["freshness"] == "blocked"
    assert result["machine_compatible"] is False
    assert reason in result["reasons"]
    assert result["cleanup"] == "green"


def test_blocked_red_and_cleanup_pending_keep_independent_axes():
    fingerprint = classification_fingerprint()
    now = datetime(2026, 7, 2, tzinfo=UTC)

    blocked_checkpoint = classification_checkpoint(fingerprint=fingerprint)
    blocked_checkpoint["verdicts"]["domains"]["content"] = "blocked"
    blocked_checkpoint["verdicts"]["domains"]["structure"] = "red"
    blocked = lab_module.classify_scenario_cell(
        "basic.open", blocked_checkpoint, fingerprint, now=now
    )
    assert blocked["status"] == "blocked"
    assert blocked["freshness"] == "fresh"
    assert "domain_blocked:content" in blocked["reasons"]
    assert "domain_red:structure" in blocked["reasons"]
    assert blocked["cleanup"] == "green"

    red_checkpoint = classification_checkpoint(fingerprint=fingerprint)
    red_checkpoint["verdicts"]["domains"]["content"] = "red"
    red = lab_module.classify_scenario_cell(
        "basic.open", red_checkpoint, fingerprint, now=now
    )
    assert red["status"] == "red"
    assert red["freshness"] == "fresh"
    assert red["cleanup"] == "green"

    pending_checkpoint = classification_checkpoint(fingerprint=fingerprint)
    pending_checkpoint["cleanup_obligations"] = [{"peer": "disposable"}]
    pending_checkpoint["verdicts"]["domains"]["cleanup"] = "blocked"
    pending = lab_module.classify_scenario_cell(
        "basic.open", pending_checkpoint, fingerprint, now=now
    )
    assert pending["status"] == "cleanup_pending"
    assert pending["freshness"] == "fresh"
    assert pending["cleanup"] == "pending"
    assert "domain_blocked:cleanup" in pending["reasons"]


def test_machine_compatible_requires_complete_all_green_and_empty_cleanup():
    fingerprint = classification_fingerprint()
    checkpoint = classification_checkpoint(fingerprint=fingerprint)
    completed_at = checkpoint["verdicts"]["completed_at"]
    domains = checkpoint["verdicts"]["domains"]

    assert lab_module.classify_scenario_cell(
        "basic.open",
        checkpoint,
        fingerprint,
        now=completed_at + timedelta(days=1),
    ) == {
        "scenario_key": "basic.open",
        "status": "machine_compatible",
        "freshness": "fresh",
        "machine_compatible": True,
        "reasons": [],
        "fingerprint_reasons": [],
        "completed_at": completed_at,
        "expires_at": completed_at + timedelta(days=30),
        "domains": domains,
        "cleanup": "green",
    }

    checkpoint["phase"] = "verify"
    result = lab_module.classify_scenario_cell(
        "basic.open",
        checkpoint,
        fingerprint,
        now=completed_at + timedelta(days=1),
    )
    assert result["status"] == "blocked"
    assert result["machine_compatible"] is False
    assert result["cleanup"] == "blocked"


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda checkpoint: checkpoint.update(checkpoint_version=2), "checkpoint version"),
        (lambda checkpoint: checkpoint.update(phase="paused"), "scenario phase"),
        (lambda checkpoint: checkpoint.update(unexpected={}), "checkpoint fields"),
        (lambda checkpoint: checkpoint.update(created_peers=[]), "created_peers"),
    ),
)
def test_cell_rejects_malformed_checkpoint_envelope(mutate, message):
    fingerprint = classification_fingerprint()
    checkpoint = classification_checkpoint(fingerprint=fingerprint)
    mutate(checkpoint)

    with pytest.raises(ValueError, match=message):
        lab_module.classify_scenario_cell(
            "basic.open",
            checkpoint,
            fingerprint,
            now=datetime(2026, 7, 2, tzinfo=UTC),
        )


def test_one_targeted_green_matrix_cell_leaves_fifteen_missing():
    expected = classification_fingerprint_matrix()
    checkpoints = {
        "basic.open": classification_checkpoint(
            "basic.open", fingerprint=expected["basic.open"]
        )
    }
    result = lab_module.classify_scenario_matrix(
        checkpoints,
        expected,
        now=datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert result["machine_green"] is False
    assert result["counts"] == {
        "missing": 15,
        "stale": 0,
        "blocked": 0,
        "red": 0,
        "cleanup_pending": 0,
        "machine_compatible": 1,
    }
    assert result["cells"]["basic.open"]["status"] == "machine_compatible"
    assert result["cells"]["basic.protected"]["status"] == "missing"
    assert result["non_green_cells"] == [
        spec.key for spec in lab_module.required_scenarios()[1:]
    ]


def test_machine_green_requires_all_sixteen_cells_with_green_cleanup():
    expected = classification_fingerprint_matrix()
    checkpoints = {
        spec.key: classification_checkpoint(
            spec.key, fingerprint=expected[spec.key]
        )
        for spec in lab_module.required_scenarios()
    }
    now = datetime(2026, 7, 2, tzinfo=UTC)

    green = lab_module.classify_scenario_matrix(checkpoints, expected, now=now)
    assert green["machine_green"] is True
    assert green["counts"]["machine_compatible"] == 16
    assert green["non_green_cells"] == []
    assert all(cell["cleanup"] == "green" for cell in green["cells"].values())

    checkpoints["channel.open"]["cleanup_obligations"] = [{"pending": True}]
    pending = lab_module.classify_scenario_matrix(checkpoints, expected, now=now)
    assert pending["machine_green"] is False
    assert pending["counts"]["cleanup_pending"] == 1
    assert pending["non_green_cells"] == ["channel.open"]


def test_matrix_rejects_unknown_incomplete_and_cross_key_inputs():
    expected = classification_fingerprint_matrix()
    now = datetime(2026, 7, 2, tzinfo=UTC)

    with pytest.raises(ValueError):
        lab_module.classify_scenario_matrix(
            {"unknown.open": {}}, expected, now=now
        )

    incomplete = dict(expected)
    incomplete.pop("basic.protected")
    with pytest.raises(ValueError):
        lab_module.classify_scenario_matrix({}, incomplete, now=now)

    borrowed_expected = dict(expected)
    borrowed_expected["basic.open"] = expected["basic.protected"]
    with pytest.raises(ValueError):
        lab_module.classify_scenario_matrix({}, borrowed_expected, now=now)

    borrowed_checkpoint = classification_checkpoint(
        "basic.protected", fingerprint=expected["basic.protected"]
    )
    with pytest.raises(ValueError):
        lab_module.classify_scenario_matrix(
            {"basic.open": borrowed_checkpoint}, expected, now=now
        )


def test_migrated_v2_manifest_classifies_all_sixteen_cells_missing(tmp_path):
    legacy = new_manifest(7)
    legacy["manifest_version"] = 2
    legacy.pop("scenarios")
    legacy["seeded"] = {"open_source": {"photo": [11]}}
    legacy["blocked"] = {"open_source": {"poll": "blocked"}}
    path = tmp_path / "legacy.json"
    save_manifest(path, legacy)
    migrated = load_manifest(path)

    result = lab_module.classify_scenario_matrix(
        migrated["scenarios"],
        classification_fingerprint_matrix(),
        now=datetime(2026, 7, 2, tzinfo=UTC),
    )

    assert result["machine_green"] is False
    assert result["counts"]["missing"] == 16
    assert result["counts"]["machine_compatible"] == 0
    assert result["non_green_cells"] == [
        spec.key for spec in lab_module.required_scenarios()
    ]


def test_cell_rejects_malformed_fingerprint_and_accumulates_clock_reasons():
    fingerprint = classification_fingerprint()
    checkpoint = classification_checkpoint(fingerprint=fingerprint)
    checkpoint.pop("compatibility_fingerprint")
    with pytest.raises(ValueError):
        lab_module.classify_scenario_cell(
            "basic.open", checkpoint, fingerprint, now=None
        )

    checkpoint = classification_checkpoint(fingerprint=fingerprint)
    checkpoint["verdicts"]["completed_at"] = "invalid"
    result = lab_module.classify_scenario_cell(
        "basic.open", checkpoint, fingerprint, now=None
    )
    assert "clock_unavailable" in result["reasons"]
    assert "clock_invalid" in result["reasons"]


def test_domain_envelope_is_fail_closed():
    fingerprint = classification_fingerprint("forum.open")
    checkpoint = classification_checkpoint("forum.open", fingerprint=fingerprint)
    checkpoint["verdicts"]["domains"].pop("topic")
    missing = lab_module.classify_scenario_cell(
        "forum.open",
        checkpoint,
        fingerprint,
        now=datetime(2026, 7, 2, tzinfo=UTC),
    )
    assert missing["status"] == "blocked"
    assert "domain_missing:topic" in missing["reasons"]

    checkpoint["verdicts"]["domains"]["topic"] = "amber"
    with pytest.raises(ValueError):
        lab_module.classify_scenario_cell(
            "forum.open",
            checkpoint,
            fingerprint,
            now=datetime(2026, 7, 2, tzinfo=UTC),
        )

    checkpoint["verdicts"]["domains"]["topic"] = "green"
    checkpoint["verdicts"]["domains"]["borrowed"] = "green"
    with pytest.raises(ValueError):
        lab_module.classify_scenario_cell(
            "forum.open",
            checkpoint,
            fingerprint,
            now=datetime(2026, 7, 2, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("checkpoint_version", 2, "checkpoint version"),
        ("checkpoint_version", True, "checkpoint version"),
        ("phase", "paused", "phase"),
    ),
)
def test_invalid_checkpoint_version_or_phase_rejects(tmp_path, field, value, message):
    fingerprint = lab_module.new_compatibility_fingerprint(
        "channel.open",
        fixture_schema_version=1,
        lab_code_digest="a" * 64,
        mirror_code_digest="b" * 64,
        telethon_version="1.44.0",
        telegram_schema_layer=216,
        account_role_binding={"operator": {"alias": "main", "user_id": 101}},
        config_digest="c" * 64,
    )
    manifest = new_manifest(7)
    checkpoint = lab_module.new_scenario_checkpoint("channel.open", fingerprint)
    checkpoint[field] = value
    manifest["scenarios"]["channel.open"] = checkpoint
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)

    with pytest.raises(ValueError, match=message):
        load_manifest(path)


def test_malformed_compatibility_fingerprints_reject(tmp_path):
    def valid_fingerprint():
        return lab_module.new_compatibility_fingerprint(
            "basic.open",
            fixture_schema_version=1,
            lab_code_digest="a" * 64,
            mirror_code_digest="b" * 64,
            telethon_version="1.44.0",
            telegram_schema_layer=216,
            account_role_binding={
                "operator": {"alias": "main", "user_id": 101},
                "lab_peer": {"alias": "lab-peer", "user_id": 202},
            },
            config_digest="c" * 64,
        )

    cases = (
        lambda fp: fp.update(fingerprint_version=2),
        lambda fp: fp.update(fingerprint_version=True),
        lambda fp: fp.update(scenario_key="basic.protected"),
        lambda fp: fp.update(fixture_schema_version=0),
        lambda fp: fp.update(fixture_schema_version=True),
        lambda fp: fp.update(lab_code_digest="A" * 64),
        lambda fp: fp.update(mirror_code_digest="b" * 63),
        lambda fp: fp.update(config_digest="z" * 64),
        lambda fp: fp.update(telethon_version=""),
        lambda fp: fp.update(telegram_schema_layer=0),
        lambda fp: fp.update(telegram_schema_layer=True),
        lambda fp: fp.update(account_role_binding={}),
        lambda fp: fp["account_role_binding"].update(
            observer={"alias": "other", "user_id": 303}
        ),
        lambda fp: fp["account_role_binding"]["operator"].update(alias=""),
        lambda fp: fp["account_role_binding"]["operator"].update(user_id=0),
        lambda fp: fp["account_role_binding"]["operator"].update(user_id=True),
        lambda fp: fp["account_role_binding"]["lab_peer"].update(alias=""),
        lambda fp: fp["account_role_binding"]["lab_peer"].update(user_id=101),
    )
    path = tmp_path / "lab.json"
    for mutate in cases:
        fingerprint = valid_fingerprint()
        manifest = new_manifest(7)
        manifest["scenarios"]["basic.open"] = lab_module.new_scenario_checkpoint(
            "basic.open", fingerprint
        )
        # Exercise embedded-key mismatch through saved data, not the builder.
        mutate(fingerprint)
        manifest["scenarios"]["basic.open"]["compatibility_fingerprint"] = fingerprint
        save_manifest(path, manifest)
        with pytest.raises(ValueError):
            load_manifest(path)


def test_checkpoint_fields_scenario_keys_and_container_types_are_strict(tmp_path):
    def valid_checkpoint(scenario_key="channel.open"):
        fingerprint = lab_module.new_compatibility_fingerprint(
            scenario_key,
            fixture_schema_version=1,
            lab_code_digest="a" * 64,
            mirror_code_digest="b" * 64,
            telethon_version="1.44.0",
            telegram_schema_layer=216,
            account_role_binding={"operator": {"alias": "main", "user_id": 101}},
            config_digest="c" * 64,
        )
        return lab_module.new_scenario_checkpoint(scenario_key, fingerprint)

    path = tmp_path / "lab.json"
    invalid_manifests = []

    missing_scenarios = new_manifest(7)
    missing_scenarios.pop("scenarios")
    invalid_manifests.append(missing_scenarios)
    wrong_scenarios = new_manifest(7)
    wrong_scenarios["scenarios"] = []
    invalid_manifests.append(wrong_scenarios)
    unknown_scenario = new_manifest(7)
    unknown_scenario["scenarios"]["unknown.open"] = valid_checkpoint("unknown.open")
    invalid_manifests.append(unknown_scenario)
    non_dict_checkpoint = new_manifest(7)
    non_dict_checkpoint["scenarios"]["channel.open"] = []
    invalid_manifests.append(non_dict_checkpoint)

    for field in (
        "checkpoint_version",
        "phase",
        "created_peers",
        "created_topics",
        "outbound_operations",
        "verdicts",
        "cleanup_obligations",
        "compatibility_fingerprint",
    ):
        manifest = new_manifest(7)
        checkpoint = valid_checkpoint()
        checkpoint.pop(field)
        manifest["scenarios"]["channel.open"] = checkpoint
        invalid_manifests.append(manifest)

    unknown_field = new_manifest(7)
    checkpoint = valid_checkpoint()
    checkpoint["extra"] = {}
    unknown_field["scenarios"]["channel.open"] = checkpoint
    invalid_manifests.append(unknown_field)

    for field, wrong_value in (
        ("created_peers", []),
        ("created_topics", []),
        ("outbound_operations", []),
        ("verdicts", []),
        ("cleanup_obligations", {}),
    ):
        manifest = new_manifest(7)
        checkpoint = valid_checkpoint()
        checkpoint[field] = wrong_value
        manifest["scenarios"]["channel.open"] = checkpoint
        invalid_manifests.append(manifest)

    for fingerprint_change in ("missing", "unknown"):
        manifest = new_manifest(7)
        checkpoint = valid_checkpoint()
        fingerprint = checkpoint["compatibility_fingerprint"]
        if fingerprint_change == "missing":
            fingerprint.pop("config_digest")
        else:
            fingerprint["extra"] = "value"
        manifest["scenarios"]["channel.open"] = checkpoint
        invalid_manifests.append(manifest)

    for manifest in invalid_manifests:
        save_manifest(path, manifest)
        with pytest.raises(ValueError):
            load_manifest(path)


def test_v2_migration_rejects_invalid_legacy_provenance_and_creating(tmp_path):
    path = tmp_path / "lab.json"
    legacy = new_manifest(7)
    legacy["manifest_version"] = 2
    legacy.pop("scenarios")
    legacy["channels"] = {
        "open_source": {
            "peer_id": True,
            "title": lab_title(legacy, "open_source"),
        }
    }
    save_manifest(path, legacy)
    with pytest.raises(ValueError, match="peer id"):
        load_manifest(path)

    legacy["channels"] = {}
    legacy["creating"] = {
        "open_source": {
            "title": lab_title(legacy, "open_source"),
            "state": "invented",
        }
    }
    save_manifest(path, legacy)
    with pytest.raises(ValueError, match="creating state"):
        load_manifest(path)


def test_load_manifest_rejects_unmarked_or_duplicate_channel_entries(tmp_path):
    manifest = new_manifest(7)
    manifest["channels"] = {
        "open_source": {"peer_id": 100, "title": "not a lab"},
    }
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="provenance"):
        load_manifest(path)

    manifest["channels"] = {
        "open_source": {"peer_id": 100, "title": lab_title(manifest, "open_source")},
        "dest_native": {"peer_id": 100, "title": lab_title(manifest, "dest_native")},
    }
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(path)


def test_load_manifest_rejects_stale_or_invalid_creating_entries(tmp_path):
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    manifest["creating"] = {
        "protected_source": {
            "title": f"{LAB_MARKER} stale-lab protected_source",
            "state": "ambiguous",
        }
    }
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="creating title"):
        load_manifest(path)

    manifest["creating"]["protected_source"] = {
        "title": lab_title(manifest, "protected_source"),
        "state": "invented",
    }
    save_manifest(path, manifest)
    with pytest.raises(ValueError, match="creating state"):
        load_manifest(path)


def test_record_channel_requires_known_role_and_marker():
    manifest = new_manifest(7)
    with pytest.raises(ValueError):
        record_channel(manifest, "mystery", 100, f"{LAB_MARKER} x")
    with pytest.raises(PolicyError):
        record_channel(manifest, "open_source", 100, "innocent channel")
    with pytest.raises(PolicyError):
        record_channel(manifest, "open_source", 100, f"{LAB_MARKER} forged")


def test_assert_lab_peer_blocks_foreign_peers():
    manifest = new_manifest(7)
    record_channel(manifest, "dest_native", 200, lab_title(manifest, "dest_native"))
    assert lab_peer_ids(manifest) == {200}
    assert_lab_peer(manifest, 200)
    with pytest.raises(PolicyError):
        assert_lab_peer(manifest, 999)


def test_channel_roles_are_frozen():
    assert CHANNEL_ROLES == (
        "protected_source",
        "open_source",
        "dest_native",
        "dest_reupload",
    )


def test_required_scenario_keys_cover_every_topology_protection_cell():
    assert hasattr(lab_module, "required_scenarios")
    assert tuple(spec.key for spec in lab_module.required_scenarios()) == (
        "basic.open",
        "basic.protected",
        "supergroup.open",
        "supergroup.protected",
        "forum.open",
        "forum.protected",
        "channel.open",
        "channel.protected",
        "channel_plain.open_open",
        "channel_plain.protected_open",
        "channel_plain.open_protected",
        "channel_plain.protected_protected",
        "channel_forum.open_open",
        "channel_forum.protected_open",
        "channel_forum.open_protected",
        "channel_forum.protected_protected",
    )


def test_full_content_suite_is_allocated_only_to_standalone_peer_families():
    scenarios = lab_module.required_scenarios()
    assert all(hasattr(spec, "content_profile") for spec in scenarios)
    assert {
        spec.key for spec in scenarios if spec.content_profile == "full"
    } == {
        "basic.open",
        "basic.protected",
        "supergroup.open",
        "supergroup.protected",
        "forum.open",
        "forum.protected",
        "channel.open",
        "channel.protected",
    }
    assert {
        spec.key for spec in scenarios if spec.content_profile == "sentinels"
    } == {
        "channel_plain.open_open",
        "channel_plain.protected_open",
        "channel_plain.open_protected",
        "channel_plain.protected_protected",
        "channel_forum.open_open",
        "channel_forum.protected_open",
        "channel_forum.open_protected",
        "channel_forum.protected_protected",
    }


def test_scenario_specs_encode_source_and_discussion_protection_independently():
    scenarios = lab_module.required_scenarios()
    fields = (
        "source_family",
        "source_protected",
        "discussion_kind",
        "discussion_protected",
    )
    assert all(hasattr(spec, field) for spec in scenarios for field in fields)
    assert {
        (
            spec.source_family,
            spec.source_protected,
            spec.discussion_kind,
            spec.discussion_protected,
        )
        for spec in scenarios
    } == {
        ("basic", False, "none", None),
        ("basic", True, "none", None),
        ("supergroup", False, "none", None),
        ("supergroup", True, "none", None),
        ("forum", False, "none", None),
        ("forum", True, "none", None),
        ("channel", False, "none", None),
        ("channel", True, "none", None),
        *{
            ("channel", channel_protected, discussion_kind, discussion_protected)
            for channel_protected in (False, True)
            for discussion_kind in ("plain", "forum")
            for discussion_protected in (False, True)
        },
    }


def test_basic_sources_are_normalized_to_supergroup_destinations():
    scenarios = lab_module.required_scenarios()
    assert all(hasattr(spec, "destination_family") for spec in scenarios)
    assert {
        spec.destination_family
        for spec in scenarios
        if spec.source_family == "basic"
    } == {"supergroup"}
    assert {
        spec.destination_family
        for spec in scenarios
        if spec.source_family == "supergroup"
    } == {"supergroup"}
    assert {
        spec.destination_family
        for spec in scenarios
        if spec.source_family == "forum"
    } == {"forum"}
    assert {
        spec.destination_family
        for spec in scenarios
        if spec.source_family == "channel"
    } == {"channel"}


def test_record_and_read_seeds():
    manifest = new_manifest(7)
    record_seed(manifest, "open_source", "photo", [11])
    record_seed(manifest, "open_source", "album", [12, 13])
    assert seeded_ids(manifest, "open_source") == {
        "album": [12, 13],
        "photo": [11],
    }
    assert seeded_ids(manifest, "protected_source") == {}


# --- Task 2: fixture matrix and payload generators ---

from telethon.tl import types  # noqa: E402
from telethon.errors import ChannelPrivateError, FloodWaitError  # noqa: E402

from tgcli.mirror_probe import NON_BYTE_KINDS, classify_message  # noqa: E402
from tgcli.mirror_lab import (  # noqa: E402
    BYTE_FIXTURES,
    COVERED_BY_R0,
    EXCLUDED_KINDS,
    NON_BYTE_LAB_KINDS,
    ALBUM_COLORS,
    build_png,
    deterministic_bytes,
    non_byte_media,
    pending_kinds,
    planned_kinds,
)


def test_png_payload_is_valid_signature_and_deterministic():
    payload = build_png((255, 0, 0))
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert payload == build_png((255, 0, 0))
    assert payload != build_png((0, 255, 0))


def test_deterministic_bytes_are_stable_and_sized():
    blob = deterministic_bytes("video", 1_600_000)
    assert len(blob) == 1_600_000
    assert blob == deterministic_bytes("video", 1_600_000)
    assert blob[:64] != deterministic_bytes("audio", 64)


@pytest.mark.skipif(
    os.environ.get("TGCLI_LAB_FIXTURE_SMOKE") != "1",
    reason="requires the lab-only ffmpeg/cwebp toolchain",
)
def test_materialized_media_fixtures_are_valid_and_deterministic(tmp_path):
    expected = {
        "photo": ("jpeg_pipe", "mjpeg"),
        "audio": ("mp3", "mp3"),
        "voice": ("ogg", "opus"),
        "video": ("mov,mp4,m4a,3gp,3g2,mj2", "h264"),
        "video_note": ("mov,mp4,m4a,3gp,3g2,mj2", "h264"),
        "animation": ("gif", "gif"),
        "sticker": ("webp_pipe", "webp"),
    }
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for kind, (format_name, codec_name) in expected.items():
        path_a = REAL_MATERIALIZE_FIXTURE(kind, first)
        path_b = REAL_MATERIALIZE_FIXTURE(kind, second)
        assert hashlib.sha256(path_a.read_bytes()).digest() == hashlib.sha256(
            path_b.read_bytes()
        ).digest()
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=format_name:stream=codec_name", "-of", "json", str(path_a),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        metadata = json.loads(probe.stdout)
        assert metadata["format"]["format_name"] == format_name
        assert metadata["streams"][0]["codec_name"] == codec_name
        if kind == "video":
            assert {stream["codec_name"] for stream in metadata["streams"]} == {
                "h264",
                "aac",
            }
    assert (first / BYTE_FIXTURES["video"].filename).stat().st_size > 512 * 1024

    red = REAL_MATERIALIZE_FIXTURE("photo", first / "red", color=(255, 0, 0))
    green = REAL_MATERIALIZE_FIXTURE("photo", first / "green", color=(0, 255, 0))
    assert hashlib.sha256(red.read_bytes()).digest() != hashlib.sha256(
        green.read_bytes()
    ).digest()


def test_byte_fixture_matrix_covers_every_byte_kind():
    assert sorted(BYTE_FIXTURES) == [
        "animation", "audio", "document", "photo",
        "sticker", "video", "video_note", "voice",
    ]
    for kind, fixture in BYTE_FIXTURES.items():
        assert fixture.kind == kind
        assert fixture.payload()  # non-empty bytes
        assert isinstance(fixture.attributes(), list)
        assert fixture.reupload_fidelity in {"exact", "reencoded"}
    assert BYTE_FIXTURES["photo"].reupload_fidelity == "reencoded"
    assert BYTE_FIXTURES["document"].force_document is True


def test_video_fixture_crosses_download_chunk_boundary():
    assert len(BYTE_FIXTURES["video"].payload()) > 512 * 1024


def test_non_byte_media_constructs_pinned_layer_objects():
    media = non_byte_media()
    assert sorted(media) == sorted(NON_BYTE_LAB_KINDS)
    assert isinstance(media["poll"], types.InputMediaPoll)
    assert isinstance(media["contact"], types.InputMediaContact)
    assert isinstance(media["geo"], types.InputMediaGeoPoint)
    assert isinstance(media["venue"], types.InputMediaVenue)
    assert isinstance(media["dice"], types.InputMediaDice)


def test_every_probe_kind_is_planned_excluded_or_covered():
    all_kinds = set(NON_BYTE_KINDS) | set(BYTE_FIXTURES)
    planned = set(planned_kinds())
    accounted = planned | set(EXCLUDED_KINDS) | COVERED_BY_R0
    assert all_kinds <= accounted
    assert not (set(EXCLUDED_KINDS) & planned)


def test_todo_is_explicitly_unsupported_after_live_server_rejection():
    assert "todo" not in planned_kinds()
    assert "MediaInvalidError" in EXCLUDED_KINDS["todo"]


def test_geo_live_is_explicitly_unsupported_after_native_fidelity_failure():
    assert "geo_live" not in planned_kinds()
    assert "static geo" in EXCLUDED_KINDS["geo_live"]


def test_fixture_attributes_classify_back_to_expected_kind():
    from types import SimpleNamespace as NS

    for kind in ("voice", "video_note", "animation", "sticker", "audio", "video"):
        fixture = BYTE_FIXTURES[kind]
        document = NS(
            mime_type="application/octet-stream",
            attributes=[
                a for a in fixture.attributes()
                if not isinstance(a, types.DocumentAttributeFilename)
            ],
        )
        message = NS(
            id=1, action=None, message="",
            media=types.MessageMediaDocument(document=document),
        )
        assert classify_message(message) == kind


def test_pending_kinds_shrink_as_seeds_are_recorded():
    manifest = new_manifest(7)
    assert pending_kinds(manifest, "open_source") == planned_kinds()
    record_seed(manifest, "open_source", "photo", [11])
    assert "photo" not in pending_kinds(manifest, "open_source")
    assert "photo" in pending_kinds(manifest, "protected_source")


# --- Task 3: verdict and transport-fidelity comparison ---

from tgcli.mirror_lab import compare_transport, lab_verdict  # noqa: E402


def capability(kind, *, shas=(), bytes_state="pass", decode="pass"):
    return {
        "kind": kind,
        "sample_count": max(len(shas), 1),
        "coverage": "complete",
        "telethon_bytes": bytes_state,
        "samples": [
            {"kind": kind, "decode": decode, "telethon_bytes": bytes_state,
             "sha256": sha, "bytes": 1 if sha else None, "error": None}
            for sha in (shas or (None,))
        ],
    }


def report(*capabilities, probe_version=2):
    return {"probe_version": probe_version, "capabilities": list(capabilities)}


def seeded_manifest():
    manifest = new_manifest(7)
    rows = []
    next_id = 1
    for kind in planned_kinds():
        ids = [next_id, next_id + 1] if kind == "album" else [next_id]
        next_id += len(ids)
        record_seed(manifest, "protected_source", kind, ids)
        if kind == "album":
            continue
        if kind in BYTE_FIXTURES:
            rows.append(capability(kind, shas=(f"{kind}-sha",)))
        else:
            rows.append(capability(kind, bytes_state="not_applicable"))
    return manifest, rows


def test_lab_verdict_green_when_all_seeded_kinds_pass():
    manifest, rows = seeded_manifest()
    result = lab_verdict(
        report(*rows),
        manifest,
        "protected_source",
    )
    assert result["verdict"] == "green"
    assert result["missing"] == [] and result["failing"] == []
    assert "giveaway" in result["excluded"]


def test_lab_verdict_red_on_missing_or_failing_kind():
    manifest, rows = seeded_manifest()
    missing = lab_verdict(
        report(*(row for row in rows if row["kind"] != "poll")),
        manifest,
        "protected_source",
    )
    assert missing["verdict"] == "red" and missing["missing"] == ["poll"]

    failing = lab_verdict(
        report(*(
            capability("photo", shas=(), bytes_state="fail")
            if row["kind"] == "photo" else row
            for row in rows
        )),
        manifest,
        "protected_source",
    )
    assert failing["verdict"] == "red" and failing["failing"] == ["photo"]


def test_lab_verdict_rejects_empty_or_partial_seed_matrix():
    empty = new_manifest(7)
    result = lab_verdict(report(), empty, "protected_source")
    assert result["verdict"] == "red"
    assert result["pending"]


def test_lab_verdict_rejects_blocked_or_old_schema_matrix():
    blocked = new_manifest(7)
    for kind in planned_kinds():
        lab_module.record_blocked(blocked, "protected_source", kind, "RuntimeError")
    result = lab_verdict(report(), blocked, "protected_source")
    assert result["verdict"] == "red"
    assert sorted(result["blocked"]) == sorted(planned_kinds())

    manifest, rows = seeded_manifest()
    result = lab_verdict(
        report(*rows, probe_version=1), manifest, "protected_source"
    )
    assert result["verdict"] == "red"
    assert result["schema"] == "unsupported"


def test_compare_transport_native_requires_exact_hashes():
    source = report(capability("video", shas=("v1",)), capability("photo", shas=("p1",)))
    dest_ok = report(capability("video", shas=("v1",)), capability("photo", shas=("p1",)))
    dest_bad = report(capability("video", shas=("zz",)), capability("photo", shas=("p1",)))

    ok = compare_transport(source, dest_ok, transport="native")
    assert ok["verdict"] == "green"
    assert {row["result"] for row in ok["rows"]} == {"pass"}

    bad = compare_transport(source, dest_bad, transport="native")
    assert bad["verdict"] == "red"
    video_row = next(r for r in bad["rows"] if r["kind"] == "video")
    assert video_row["result"] == "fail" and video_row["expectation"] == "exact"

    inconclusive = report(
        capability("video", shas=("v1",), bytes_state="inconclusive")
    )
    result = compare_transport(inconclusive, inconclusive, transport="native")
    assert result["verdict"] == "red"


def test_compare_transport_reupload_allows_photo_reencode():
    source = report(capability("photo", shas=("p1",)), capability("video", shas=("v1",)))
    dest = report(capability("photo", shas=("different",)), capability("video", shas=("v1",)))
    result = compare_transport(source, dest, transport="reupload")
    assert result["verdict"] == "green"
    photo_row = next(r for r in result["rows"] if r["kind"] == "photo")
    assert photo_row["expectation"] == "reencoded" and photo_row["result"] == "pass"


def test_compare_transport_flags_missing_dest_kind():
    source = report(capability("video", shas=("v1",)))
    result = compare_transport(source, report(), transport="native")
    assert result["verdict"] == "red"
    assert result["rows"][0]["result"] == "missing"


def test_compare_transport_cannot_greenlight_incomplete_expected_matrix():
    collapsed = report(capability("document", shas=("same",)))
    result = compare_transport(
        collapsed,
        collapsed,
        transport="native",
        expected_kinds={"document", "audio", "video"},
    )
    assert result["verdict"] == "red"
    assert {row["kind"] for row in result["rows"] if row["result"] == "missing"} == {
        "audio",
        "video",
    }

    observed = report(capability("document", shas=("same",)))
    result = compare_transport(
        observed, observed, transport="native", expected_kinds=set()
    )
    assert result["verdict"] == "red"


def test_compare_transport_checks_non_byte_kinds_and_album_grouping():
    source = report(
        capability("text", bytes_state="not_applicable"),
        capability("poll", bytes_state="not_applicable"),
    )
    source["album_groups"] = [
        {"count": 2, "sha256": ["red", "green"], "telethon_bytes": ["pass", "pass"]}
    ]
    dest = report(
        capability("text", bytes_state="not_applicable"),
        capability("poll", bytes_state="not_applicable"),
    )
    dest["album_groups"] = [
        {"count": 2, "sha256": ["red", "green"], "telethon_bytes": ["pass", "pass"]}
    ]

    result = compare_transport(
        source,
        dest,
        transport="native",
        expected_kinds={"text", "poll", "album"},
    )
    assert result["verdict"] == "green"

    dest["album_groups"] = [
        {"count": 2, "sha256": ["red", "red"], "telethon_bytes": ["pass", "pass"]}
    ]
    result = compare_transport(
        source,
        dest,
        transport="native",
        expected_kinds={"text", "poll", "album"},
    )
    assert result["verdict"] == "red"
    assert next(row for row in result["rows"] if row["kind"] == "album")["result"] == "fail"


def test_compare_transport_rejects_old_schema_and_reupload_album_drift():
    source = report(capability("video", shas=("v1",)), probe_version=1)
    dest = report(capability("video", shas=("v1",)))
    result = compare_transport(source, dest, transport="native")
    assert result["verdict"] == "red"
    assert result["schema"] == "unsupported"

    source = report()
    source["album_groups"] = [
        {"count": 2, "sha256": ["red", "green"], "telethon_bytes": ["pass", "pass"]}
    ]
    dest = report()
    dest["album_groups"] = [
        {"count": 2, "sha256": ["green", "red"], "telethon_bytes": ["pass", "pass"]}
    ]
    result = compare_transport(
        source, dest, transport="reupload", expected_kinds={"album"}
    )
    assert result["verdict"] == "red"
    assert result["rows"] == [
        {"kind": "album", "expectation": "grouped", "result": "fail"}
    ]


@pytest.mark.asyncio
async def test_album_groups_report_ordered_constituent_hashes():
    class AlbumTG:
        async def iter_messages(self, entity, limit):
            for message_id, grouped_id in ((3, 10), (2, 10), (1, None)):
                yield NS(
                    id=message_id,
                    grouped_id=grouped_id,
                    media=types.MessageMediaPhoto(
                        photo=NS(payload=b"red" if message_id == 2 else b"green")
                    ),
                    action=None,
                    message="",
                )

        async def iter_download(self, media, request_size):
            yield media.photo.payload

    tg = AlbumTG()
    groups = await lab_module.album_groups(tg, NS(id=1), limit=20)
    assert groups[0]["count"] == 2
    assert len(groups[0]["sha256"]) == 2
    assert groups[0]["sha256"][0] != groups[0]["sha256"][1]


# --- Task 4: provisioning and seeding engines ---

from types import SimpleNamespace as NS  # noqa: E402

import pytest  # noqa: E402
from telethon.tl import functions  # noqa: E402

from tgcli.mirror_lab import create_lab_channels, seed_sources  # noqa: E402


class FakeTG:
    """Records raw requests and high-level sends; returns canned results."""

    def __init__(self):
        self.raw_requests = []
        self.sent_files = []
        self.sent_messages = []
        self._next_channel_id = 100
        self._next_message_id = 1000
        self._channels = {}

    async def __call__(self, request):
        self.raw_requests.append(request)
        if isinstance(request, functions.channels.CreateChannelRequest):
            self._next_channel_id += 1
            channel = NS(
                id=self._next_channel_id,
                title=request.title,
                creator=True,
                broadcast=True,
                megagroup=False,
                noforwards=False,
            )
            self._channels[channel.id] = channel
            return NS(chats=[channel])
        if isinstance(request, functions.messages.ToggleNoForwardsRequest):
            peer_id = getattr(request.peer, "id", getattr(request.peer, "channel_id", None))
            if peer_id in self._channels:
                self._channels[peer_id].noforwards = request.enabled
        if isinstance(request, functions.messages.SendMediaRequest):
            self._next_message_id += 1
            return NS(updates=[NS(message=NS(id=self._next_message_id))])
        return NS(updates=[])

    async def get_entity(self, ref):
        peer_id = getattr(ref, "channel_id", ref)
        return self._channels.get(peer_id, NS(id=peer_id, title=""))

    async def send_message(self, entity, text):
        self._next_message_id += 1
        self.sent_messages.append(text)
        return NS(id=self._next_message_id)

    async def send_file(self, entity, file, **kwargs):
        self.sent_files.append((entity, file, kwargs))
        if isinstance(file, list):
            out = []
            for _ in file:
                self._next_message_id += 1
                out.append(NS(id=self._next_message_id))
            return out
        self._next_message_id += 1
        return NS(id=self._next_message_id)


@pytest.mark.asyncio
async def test_byte_seed_uses_named_path_and_explicit_mime_type(tmp_path):
    tg = FakeTG()
    await lab_module._seed_kind(tg, NS(id=1), "audio", tmp_path)
    _, uploaded, kwargs = tg.sent_files[-1]
    assert isinstance(uploaded, Path)
    assert uploaded.name == "lab-audio.mp3"
    assert kwargs["mime_type"] == "audio/mpeg"


@pytest.mark.asyncio
async def test_album_seed_uses_named_photo_paths(tmp_path):
    tg = FakeTG()
    await lab_module._seed_kind(tg, NS(id=1), "album", tmp_path)
    _, uploaded, _ = tg.sent_files[-1]
    assert all(isinstance(path, Path) for path in uploaded)
    assert [path.suffix for path in uploaded] == [".jpg", ".jpg"]


def quiet(_message):
    pass


@pytest.mark.asyncio
async def test_create_lab_channels_records_all_roles_and_protects_source(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    assert set(manifest["channels"]) == set(CHANNEL_ROLES)
    for entry in manifest["channels"].values():
        assert entry["title"].startswith(LAB_MARKER)
    toggles = [
        r for r in tg.raw_requests
        if isinstance(r, functions.messages.ToggleNoForwardsRequest)
    ]
    assert len(toggles) == 1 and toggles[0].enabled is True
    assert load_manifest(path)["channels"] == manifest["channels"]


@pytest.mark.asyncio
async def test_create_lab_channels_is_idempotent(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    created = len(tg.raw_requests)
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    assert len(tg.raw_requests) == created  # no second creation


@pytest.mark.asyncio
async def test_create_reconciles_ambiguous_accepted_channel_without_duplicate(tmp_path):
    class AcceptThenCancelCreateTG(FakeTG):
        def __init__(self):
            super().__init__()
            self.cancel_once = True

        async def __call__(self, request):
            if (
                self.cancel_once
                and isinstance(request, functions.channels.CreateChannelRequest)
            ):
                self.cancel_once = False
                await super().__call__(request)
                raise asyncio.CancelledError()
            return await super().__call__(request)

        async def iter_dialogs(self):
            for entity in self._channels.values():
                yield NS(entity=entity)

    tg = AcceptThenCancelCreateTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"

    with pytest.raises(asyncio.CancelledError):
        await create_lab_channels(tg, manifest, path, "labacct", quiet)
    persisted = load_manifest(path)
    assert persisted["channels"] == {}
    assert persisted["creating"]["protected_source"]["state"] == "ambiguous"

    await create_lab_channels(tg, persisted, path, "labacct", quiet)
    creates = [
        request for request in tg.raw_requests
        if isinstance(request, functions.channels.CreateChannelRequest)
    ]
    assert len(creates) == len(CHANNEL_ROLES)
    assert set(load_manifest(path)["channels"]) == set(CHANNEL_ROLES)


@pytest.mark.asyncio
async def test_create_reconciles_disconnect_after_server_acceptance(tmp_path):
    class AcceptThenDisconnectTG(FakeTG):
        def __init__(self):
            super().__init__()
            self.disconnect_once = True

        async def __call__(self, request):
            if (
                self.disconnect_once
                and isinstance(request, functions.channels.CreateChannelRequest)
            ):
                self.disconnect_once = False
                await super().__call__(request)
                raise ConnectionError("connection lost after dispatch")
            return await super().__call__(request)

        async def iter_dialogs(self):
            for entity in self._channels.values():
                yield NS(entity=entity)

    tg = AcceptThenDisconnectTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"

    with pytest.raises(ConnectionError, match="after dispatch"):
        await create_lab_channels(tg, manifest, path, "labacct", quiet)
    persisted = load_manifest(path)
    assert persisted["creating"]["protected_source"]["state"] == "ambiguous"

    await create_lab_channels(tg, persisted, path, "labacct", quiet)
    creates = [
        request for request in tg.raw_requests
        if isinstance(request, functions.channels.CreateChannelRequest)
    ]
    assert len(creates) == len(CHANNEL_ROLES)


@pytest.mark.asyncio
async def test_create_retries_protected_toggle_after_partial_failure(tmp_path):
    class ToggleFailsOnceTG(FakeTG):
        def __init__(self):
            super().__init__()
            self.toggle_attempts = 0

        async def __call__(self, request):
            if isinstance(request, functions.messages.ToggleNoForwardsRequest):
                self.raw_requests.append(request)
                self.toggle_attempts += 1
                if self.toggle_attempts == 1:
                    raise RuntimeError("temporary toggle failure")
            return await super().__call__(request)

    tg = ToggleFailsOnceTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    with pytest.raises(RuntimeError, match="toggle failure"):
        await create_lab_channels(tg, manifest, path, "labacct", quiet)
    created_before_retry = sum(
        isinstance(request, functions.channels.CreateChannelRequest)
        for request in tg.raw_requests
    )

    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    assert tg.toggle_attempts == 2
    assert sum(
        isinstance(request, functions.channels.CreateChannelRequest)
        for request in tg.raw_requests
    ) == created_before_retry + 3


@pytest.mark.asyncio
async def test_create_repairs_live_protection_even_if_manifest_claims_complete(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    protected_id = manifest["channels"]["protected_source"]["peer_id"]
    tg._channels[protected_id].noforwards = False
    toggles_before = sum(
        isinstance(request, functions.messages.ToggleNoForwardsRequest)
        for request in tg.raw_requests
    )

    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    assert tg._channels[protected_id].noforwards is True
    assert sum(
        isinstance(request, functions.messages.ToggleNoForwardsRequest)
        for request in tg.raw_requests
    ) == toggles_before + 1


@pytest.mark.asyncio
async def test_seed_sources_covers_plan_and_is_idempotent(tmp_path):
    tg = FakeTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    results = await seed_sources(tg, manifest, path, "labacct", quiet)

    for role in ("protected_source", "open_source"):
        assert sorted(seeded_ids(manifest, role)) == sorted(planned_kinds())
        assert set(results[role].values()) == {"seeded"}
        assert len(seeded_ids(manifest, role)["album"]) == len(ALBUM_COLORS)

    sent_before = len(tg.sent_files)
    again = await seed_sources(tg, manifest, path, "labacct", quiet)
    assert len(tg.sent_files) == sent_before
    assert again == {"protected_source": {}, "open_source": {}}


@pytest.mark.asyncio
async def test_seed_sources_records_server_rejection_explicitly(tmp_path):
    class RejectingTG(FakeTG):
        async def send_file(self, entity, file, **kwargs):
            attributes = kwargs.get("attributes") or []
            if any(type(a).__name__ == "DocumentAttributeSticker" for a in attributes):
                raise RuntimeError("STICKER_INVALID")
            return await super().send_file(entity, file, **kwargs)

    tg = RejectingTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    results = await seed_sources(tg, manifest, path, "labacct", quiet)
    assert results["open_source"]["sticker"] == "blocked:RuntimeError"
    assert "sticker" not in seeded_ids(manifest, "open_source")


@pytest.mark.asyncio
async def test_seed_stops_immediately_on_flood_wait(tmp_path):
    class FloodTG(FakeTG):
        async def send_message(self, entity, text):
            raise FloodWaitError(request=None, capture=42)

    tg = FloodTG()
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)

    with pytest.raises(FloodWaitError):
        await seed_sources(tg, manifest, path, "labacct", quiet)

    assert seeded_ids(manifest, "protected_source") == {}
    assert seeded_ids(manifest, "open_source") == {}


@pytest.mark.asyncio
async def test_seed_refuses_manifest_peer_without_live_lab_ownership(tmp_path):
    class ForeignTG(FakeTG):
        def __init__(self, title):
            super().__init__()
            self.title = title

        async def get_entity(self, ref):
            return NS(
                id=getattr(ref, "channel_id", ref),
                title=self.title,
                creator=False,
                broadcast=True,
                megagroup=False,
            )

    manifest = new_manifest(7)
    record_channel(manifest, "protected_source", 100, lab_title(manifest, "protected_source"))
    record_channel(manifest, "open_source", 200, lab_title(manifest, "open_source"))

    with pytest.raises(PolicyError, match="owned lab broadcast"):
        await seed_sources(
            ForeignTG(lab_title(manifest, "open_source")),
            manifest,
            tmp_path / "lab.json",
            "labacct",
            quiet,
        )


@pytest.mark.asyncio
async def test_mutations_respect_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_READONLY", "1")
    tg = FakeTG()
    manifest = new_manifest(7)
    with pytest.raises(PolicyError):
        await create_lab_channels(tg, manifest, tmp_path / "lab.json", "labacct", quiet)
    assert tg.raw_requests == []


@pytest.mark.asyncio
async def test_create_writes_correlated_attempt_and_result_audit(tmp_path):
    from tgcli.safety import audit_path

    tg = FakeTG()
    await create_lab_channels(tg, new_manifest(7), tmp_path / "lab.json", "labacct", quiet)

    records = [json.loads(line) for line in audit_path().read_text().splitlines()]
    by_operation = {}
    for record in records:
        by_operation.setdefault(record["operation_id"], []).append(record)
    assert len(by_operation) == 5  # four creates plus protected toggle
    for pair in by_operation.values():
        assert [record["phase"] for record in pair] == ["attempt", "result"]
        assert pair[-1]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_cancelled_mutation_records_ambiguous_audit_result():
    from tgcli.safety import audit_path

    async def cancel():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await lab_module._audited_mutation("lab-test", "labacct", {}, cancel)

    records = [json.loads(line) for line in audit_path().read_text().splitlines()]
    assert [record["phase"] for record in records] == ["attempt", "result"]
    assert records[-1]["status"] == "ambiguous"


# --- Task 5: copy transports ---

from pathlib import Path  # noqa: E402

from telethon.errors import ChatForwardsRestrictedError  # noqa: E402

from tgcli.mirror_lab import copy_native, copy_reupload  # noqa: E402


class TransportTG(FakeTG):
    def __init__(self):
        super().__init__()
        self.forwards = []
        self.downloads = []

    async def __call__(self, request):
        if isinstance(request, functions.messages.ForwardMessagesRequest):
            from_protected = getattr(self, "protected_peer_id", None) == getattr(
                request.from_peer, "id", request.from_peer
            )
            if from_protected:
                raise ChatForwardsRestrictedError(request=request)
            self.forwards.append(request)
            return NS(updates=[])
        return await super().__call__(request)

    async def get_messages(self, entity, ids):
        return [
            NS(id=i, document=NS(attributes=[]), media=object()) for i in ids
        ]

    async def download_media(self, message, file):
        path = Path(f"{file}.bin")
        path.write_bytes(b"payload-%d" % message.id)
        self.downloads.append(path)
        return str(path)


async def seeded_lab(tg, tmp_path):
    manifest = new_manifest(7)
    path = tmp_path / "lab.json"
    await create_lab_channels(tg, manifest, path, "labacct", quiet)
    await seed_sources(tg, manifest, path, "labacct", quiet)
    tg.protected_peer_id = manifest["channels"]["protected_source"]["peer_id"]
    return manifest


@pytest.mark.asyncio
async def test_copy_native_confirms_restriction_and_forwards_per_kind(tmp_path):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    result = await copy_native(tg, manifest, "labacct", quiet)

    assert result["transport"] == "native"
    assert result["restricted_check"] == "confirmed"
    assert set(result["results"]) == set(planned_kinds())
    assert all(v == "forwarded" for v in result["results"].values())
    for request in tg.forwards:
        assert request.drop_author is True
        assert len(request.random_id) == len(request.id)


@pytest.mark.asyncio
async def test_copy_native_records_per_kind_blocks(tmp_path):
    class GeoBlockingTG(TransportTG):
        async def __call__(self, request):
            if isinstance(request, functions.messages.ForwardMessagesRequest):
                if getattr(self, "geo_ids", None) and set(request.id) & self.geo_ids:
                    raise RuntimeError("MEDIA_INVALID")
            return await super().__call__(request)

    tg = GeoBlockingTG()
    manifest = await seeded_lab(tg, tmp_path)
    tg.geo_ids = set(seeded_ids(manifest, "open_source")["geo"])
    result = await copy_native(tg, manifest, "labacct", quiet)
    assert result["results"]["geo"] == "blocked:RuntimeError"
    assert result["results"]["photo"] == "forwarded"


@pytest.mark.asyncio
async def test_copy_reupload_downloads_and_resends_byte_kinds(tmp_path):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = await copy_reupload(tg, manifest, workdir, "labacct", quiet)

    assert result["transport"] == "reupload"
    for kind in BYTE_FIXTURES:
        assert result["results"][kind] == "copied"
    assert result["results"]["album"] == "copied"
    for kind in ("text", *NON_BYTE_LAB_KINDS):
        assert result["results"][kind] == "not_applicable"
    assert tg.downloads  # media actually went through the download path
    assert not list(workdir.iterdir())  # workdir cleaned after the phase


@pytest.mark.asyncio
async def test_copy_phases_respect_kill_switch(tmp_path, monkeypatch):
    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    with pytest.raises(PolicyError):
        await copy_native(tg, manifest, "labacct", quiet)
    with pytest.raises(PolicyError):
        await copy_reupload(tg, manifest, tmp_path, "labacct", quiet)


# --- Task 6: teardown ---

from tgcli.mirror_lab import teardown_lab  # noqa: E402


class TeardownTG(TransportTG):
    def __init__(self, titles):
        super().__init__()
        self._titles = titles
        self.deleted = []

    async def get_entity(self, ref):
        peer_id = getattr(ref, "channel_id", ref)
        return NS(
            id=peer_id,
            title=self._titles.get(peer_id, ""),
            creator=True,
            broadcast=True,
            megagroup=False,
        )

    async def __call__(self, request):
        if isinstance(request, functions.channels.DeleteChannelRequest):
            self.deleted.append(request.channel)
            return NS(updates=[])
        return await super().__call__(request)


@pytest.mark.asyncio
async def test_teardown_deletes_only_marked_lab_channels(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    record_channel(manifest, "dest_native", 200, lab_title(manifest, "dest_native"))
    tg = TeardownTG({
        100: lab_title(manifest, "open_source"),
        200: lab_title(manifest, "dest_native"),
    })
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    result = await teardown_lab(tg, manifest, path, "labacct", quiet)
    assert sorted(result["removed"]) == ["dest_native", "open_source"]
    assert len(tg.deleted) == 2
    assert load_manifest(path)["channels"] == {}


@pytest.mark.asyncio
async def test_teardown_refuses_channel_without_live_marker(tmp_path):
    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    tg = TeardownTG({100: "renamed innocent channel"})
    with pytest.raises(PolicyError):
        await teardown_lab(tg, manifest, tmp_path / "lab.json", "labacct", quiet)
    assert tg.deleted == []


@pytest.mark.asyncio
async def test_teardown_refuses_marked_channel_not_owned_by_account(tmp_path):
    class ForeignTeardownTG(TeardownTG):
        async def get_entity(self, ref):
            entity = await super().get_entity(ref)
            entity.creator = False
            return entity

    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    tg = ForeignTeardownTG({100: lab_title(manifest, "open_source")})
    with pytest.raises(PolicyError, match="owned lab broadcast"):
        await teardown_lab(
            tg, manifest, tmp_path / "lab.json", "labacct", quiet
        )
    assert tg.deleted == []


@pytest.mark.asyncio
async def test_teardown_persists_progress_before_later_delete_failure(tmp_path):
    class SecondDeleteFailsTG(TeardownTG):
        async def __call__(self, request):
            if isinstance(request, functions.channels.DeleteChannelRequest):
                if len(self.deleted) == 1:
                    raise RuntimeError("second delete failed")
            return await super().__call__(request)

    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    record_channel(manifest, "dest_native", 200, lab_title(manifest, "dest_native"))
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    tg = SecondDeleteFailsTG({
        100: lab_title(manifest, "open_source"),
        200: lab_title(manifest, "dest_native"),
    })

    with pytest.raises(RuntimeError, match="second delete"):
        await teardown_lab(tg, manifest, path, "labacct", quiet)

    persisted = load_manifest(path)
    assert set(persisted["channels"]) == {"dest_native"}


@pytest.mark.asyncio
async def test_teardown_preserves_ambiguous_delete_until_confirmed(tmp_path):
    class AcceptThenCancelTG(TeardownTG):
        async def __call__(self, request):
            if isinstance(request, functions.channels.DeleteChannelRequest):
                self.deleted.append(request.channel)
                raise asyncio.CancelledError()
            return await super().__call__(request)

    manifest = new_manifest(7)
    record_channel(manifest, "open_source", 100, lab_title(manifest, "open_source"))
    record_channel(manifest, "dest_native", 200, lab_title(manifest, "dest_native"))
    path = tmp_path / "lab.json"
    save_manifest(path, manifest)
    first = AcceptThenCancelTG({
        100: lab_title(manifest, "open_source"),
        200: lab_title(manifest, "dest_native"),
    })

    with pytest.raises(asyncio.CancelledError):
        await teardown_lab(first, manifest, path, "labacct", quiet)
    assert load_manifest(path)["channels"]["open_source"]["delete_state"] == "deleting"

    class RecoverTG(TeardownTG):
        async def get_entity(self, ref):
            peer_id = getattr(ref, "channel_id", ref)
            if peer_id == 100:
                raise ChannelPrivateError(request=None)
            return await super().get_entity(ref)

    persisted = load_manifest(path)
    second = RecoverTG({200: lab_title(manifest, "dest_native")})
    result = await teardown_lab(second, persisted, path, "labacct", quiet)
    assert result == {"removed": ["dest_native"], "unresolved": ["open_source"]}
    remaining = load_manifest(path)["channels"]
    assert set(remaining) == {"open_source"}
    assert remaining["open_source"]["delete_state"] == "deleting"


@pytest.mark.asyncio
async def test_every_lab_mutation_has_correlated_audit_result(tmp_path):
    from tgcli.safety import audit_path

    tg = TransportTG()
    manifest = await seeded_lab(tg, tmp_path)
    await copy_native(tg, manifest, "labacct", quiet)
    await copy_reupload(tg, manifest, tmp_path / "work", "labacct", quiet)
    titles = {
        entry["peer_id"]: entry["title"] for entry in manifest["channels"].values()
    }
    teardown = TeardownTG(titles)
    await teardown_lab(
        teardown, manifest, tmp_path / "lab.json", "labacct", quiet
    )

    records = [json.loads(line) for line in audit_path().read_text().splitlines()]
    assert records
    assert all("operation_id" in record for record in records)
    operations = {}
    for record in records:
        operations.setdefault(record["operation_id"], []).append(record)
    assert all(
        [record["phase"] for record in pair] == ["attempt", "result"]
        for pair in operations.values()
    )
