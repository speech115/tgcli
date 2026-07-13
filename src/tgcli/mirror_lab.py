from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import asyncio
import secrets
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path

from tgcli.errors import PolicyError
from tgcli.mirror_probe import probe_message, write_report

LAB_MARKER = "tgcli-r1-lab"
MANIFEST_VERSION = 3
SCENARIO_CHECKPOINT_VERSION = 1
COMPATIBILITY_FINGERPRINT_VERSION = 1
EVIDENCE_TTL = timedelta(days=30)
SCENARIO_PHASES = (
    "preflight",
    "create",
    "seed",
    "mirror",
    "verify",
    "teardown",
    "complete",
)
SCENARIO_CHECKPOINT_FIELDS = frozenset(
    {
        "checkpoint_version",
        "phase",
        "created_peers",
        "created_topics",
        "outbound_operations",
        "verdicts",
        "cleanup_obligations",
        "compatibility_fingerprint",
    }
)
CHANNEL_ROLES = (
    "protected_source",
    "open_source",
    "dest_native",
    "dest_reupload",
)


@dataclass(frozen=True)
class ScenarioSpec:
    key: str
    content_profile: str
    source_family: str
    source_protected: bool
    destination_family: str
    discussion_kind: str
    discussion_protected: bool | None


def _scenario_spec(key: str) -> ScenarioSpec:
    topology, protection = key.split(".", 1)
    if topology.startswith("channel_"):
        channel_protection, discussion_protection = protection.split("_", 1)
        return ScenarioSpec(
            key=key,
            content_profile="sentinels",
            source_family="channel",
            source_protected=channel_protection == "protected",
            destination_family="channel",
            discussion_kind=topology.removeprefix("channel_"),
            discussion_protected=discussion_protection == "protected",
        )
    return ScenarioSpec(
        key=key,
        content_profile="full",
        source_family=topology,
        source_protected=protection == "protected",
        destination_family="supergroup" if topology == "basic" else topology,
        discussion_kind="none",
        discussion_protected=None,
    )


_REQUIRED_SCENARIOS = tuple(
    _scenario_spec(key)
    for key in (
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
)


def required_scenarios() -> tuple[ScenarioSpec, ...]:
    return _REQUIRED_SCENARIOS


def select_scenarios(scenario_key: str | None = None) -> tuple[ScenarioSpec, ...]:
    if scenario_key is None:
        return required_scenarios()
    selected = tuple(
        spec for spec in required_scenarios() if spec.key == scenario_key
    )
    if not selected:
        raise ValueError(f"unknown scenario key: {scenario_key}")
    return selected


def scenario_live_blocker(scenario_key: str) -> dict | None:
    spec = select_scenarios(scenario_key)[0]
    if spec.discussion_kind != "forum":
        return None
    return {
        "reason_code": "telegram_forum_discussion_incompatible",
        "evidence": (
            "created_forum_not_discussion_eligible",
            "linked_plain_group_forum_toggle_rejected",
        ),
    }


def required_scenario_domains(spec: ScenarioSpec) -> tuple[str, ...]:
    domains = (
        "content",
        "structure",
        "attribution",
        "transport",
        "audit",
        "cleanup",
    )
    if spec.source_family == "forum" or spec.discussion_kind == "forum":
        domains += ("topic",)
    if spec.discussion_kind in {"plain", "forum"}:
        domains += ("comment_root",)
    return domains


def new_compatibility_fingerprint(
    scenario_key: str,
    *,
    fixture_schema_version: int,
    lab_code_digest: str,
    mirror_code_digest: str,
    telethon_version: str,
    telegram_schema_layer: int,
    account_role_binding: dict,
    config_digest: str,
) -> dict:
    return {
        "fingerprint_version": COMPATIBILITY_FINGERPRINT_VERSION,
        "scenario_key": scenario_key,
        "fixture_schema_version": fixture_schema_version,
        "lab_code_digest": lab_code_digest,
        "mirror_code_digest": mirror_code_digest,
        "telethon_version": telethon_version,
        "telegram_schema_layer": telegram_schema_layer,
        "account_role_binding": {
            role: dict(binding) for role, binding in account_role_binding.items()
        },
        "config_digest": config_digest,
    }


def new_scenario_checkpoint(scenario_key: str, fingerprint: dict) -> dict:
    if fingerprint.get("scenario_key") != scenario_key:
        raise ValueError(f"fingerprint scenario key mismatch: {scenario_key}")
    fingerprint_copy = deepcopy(fingerprint)
    return {
        "checkpoint_version": SCENARIO_CHECKPOINT_VERSION,
        "phase": "preflight",
        "created_peers": {},
        "created_topics": {},
        "outbound_operations": {},
        "verdicts": {},
        "cleanup_obligations": [],
        "compatibility_fingerprint": fingerprint_copy,
    }


def new_manifest(account_user_id: int) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "account_user_id": account_user_id,
        "lab_id": secrets.token_hex(12),
        "created_at": datetime.now(UTC).isoformat(),
        "channels": {},
        "creating": {},
        "seeded": {},
        "blocked": {},
        "scenarios": {},
    }


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_compatibility_fingerprint(
    scenario_key: str,
    fingerprint: object,
    *,
    allow_version_mismatch: bool = False,
) -> None:
    fields = {
        "fingerprint_version",
        "scenario_key",
        "fixture_schema_version",
        "lab_code_digest",
        "mirror_code_digest",
        "telethon_version",
        "telegram_schema_layer",
        "account_role_binding",
        "config_digest",
    }
    if not isinstance(fingerprint, dict) or set(fingerprint) != fields:
        raise ValueError(f"invalid compatibility fingerprint fields: {scenario_key}")
    if (
        type(fingerprint["fingerprint_version"]) is not int
        or fingerprint["fingerprint_version"] <= 0
        or (
            not allow_version_mismatch
            and fingerprint["fingerprint_version"]
            != COMPATIBILITY_FINGERPRINT_VERSION
        )
    ):
        raise ValueError(f"unsupported fingerprint version: {scenario_key}")
    if (
        not isinstance(fingerprint["scenario_key"], str)
        or not fingerprint["scenario_key"]
        or fingerprint["scenario_key"] != scenario_key
    ):
        raise ValueError(f"fingerprint scenario key mismatch: {scenario_key}")
    if not _is_positive_int(fingerprint["fixture_schema_version"]):
        raise ValueError(f"invalid fixture schema version: {scenario_key}")
    if not _is_positive_int(fingerprint["telegram_schema_layer"]):
        raise ValueError(f"invalid Telegram schema layer: {scenario_key}")
    for field in ("lab_code_digest", "mirror_code_digest", "config_digest"):
        digest = fingerprint[field]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError(f"invalid {field}: {scenario_key}")
    telethon_version = fingerprint["telethon_version"]
    if not isinstance(telethon_version, str) or not telethon_version:
        raise ValueError(f"invalid Telethon version: {scenario_key}")
    roles = fingerprint["account_role_binding"]
    if (
        not isinstance(roles, dict)
        or "operator" not in roles
        or not set(roles) <= {"operator", "lab_peer"}
    ):
        raise ValueError(f"invalid account role binding: {scenario_key}")
    role_ids = []
    for role, binding in roles.items():
        if not isinstance(binding, dict) or set(binding) != {"alias", "user_id"}:
            raise ValueError(f"invalid account role: {scenario_key}.{role}")
        if not isinstance(binding["alias"], str) or not binding["alias"]:
            raise ValueError(f"invalid account alias: {scenario_key}.{role}")
        if not _is_positive_int(binding["user_id"]):
            raise ValueError(f"invalid account user id: {scenario_key}.{role}")
        role_ids.append(binding["user_id"])
    if len(role_ids) != len(set(role_ids)):
        raise ValueError(f"duplicate account role user id: {scenario_key}")


def _validate_scenario_checkpoint_envelope(
    scenario_key: str,
    checkpoint: object,
    *,
    bind_fingerprint_to_scenario: bool,
) -> None:
    if not isinstance(checkpoint, dict) or set(checkpoint) != SCENARIO_CHECKPOINT_FIELDS:
        raise ValueError(f"invalid scenario checkpoint fields: {scenario_key}")
    if (
        type(checkpoint["checkpoint_version"]) is not int
        or checkpoint["checkpoint_version"] != SCENARIO_CHECKPOINT_VERSION
    ):
        raise ValueError(f"unsupported checkpoint version: {scenario_key}")
    if checkpoint["phase"] not in SCENARIO_PHASES:
        raise ValueError(f"invalid scenario phase: {scenario_key}")
    for field in (
        "created_peers",
        "created_topics",
        "outbound_operations",
        "verdicts",
    ):
        if not isinstance(checkpoint[field], dict):
            raise ValueError(f"invalid {field}: {scenario_key}")
    if not isinstance(checkpoint["cleanup_obligations"], list):
        raise ValueError(f"invalid cleanup obligations: {scenario_key}")
    fingerprint = checkpoint["compatibility_fingerprint"]
    fingerprint_key = scenario_key
    if not bind_fingerprint_to_scenario:
        fingerprint_key = (
            fingerprint.get("scenario_key") if isinstance(fingerprint, dict) else ""
        )
    _validate_compatibility_fingerprint(
        fingerprint_key,
        fingerprint,
        allow_version_mismatch=not bind_fingerprint_to_scenario,
    )


def scenario_resume_phase(checkpoint: dict) -> str:
    fingerprint = (
        checkpoint.get("compatibility_fingerprint")
        if isinstance(checkpoint, dict)
        else None
    )
    scenario_key = (
        fingerprint.get("scenario_key") if isinstance(fingerprint, dict) else ""
    )
    select_scenarios(scenario_key)
    _validate_scenario_checkpoint_envelope(
        scenario_key,
        checkpoint,
        bind_fingerprint_to_scenario=True,
    )
    return checkpoint["phase"]


def advance_scenario_phase(checkpoint: dict, confirmed_phase: str) -> dict:
    current_phase = scenario_resume_phase(checkpoint)
    if confirmed_phase != current_phase:
        raise ValueError(
            f"phase confirmation mismatch: expected {current_phase}, got {confirmed_phase}"
        )
    advanced = deepcopy(checkpoint)
    if current_phase == "complete":
        return advanced
    if current_phase == "create":
        _, expected_intents = _validated_scenario_operations(checkpoint)
        operations = checkpoint["outbound_operations"]
        if set(operations) != set(expected_intents) or any(
            operation["state"] != "confirmed" for operation in operations.values()
        ):
            raise ValueError("provisioning intents are incomplete")
        expected_peer_roles = {
            intent["target_role"]
            for intent in expected_intents.values()
            if intent["method"] in {"messages.createChat", "channels.createChannel"}
        }
        created_peers = checkpoint["created_peers"]
        cleanup_roles = {
            obligation.get("peer_role")
            for obligation in checkpoint["cleanup_obligations"]
            if isinstance(obligation, dict) and set(obligation) == {"peer_role"}
        }
        if (
            set(created_peers) != expected_peer_roles
            or cleanup_roles != expected_peer_roles
            or any(
                not isinstance(peer, dict)
                or set(peer) != {"peer_id", "title_marker_verified"}
                or not _is_positive_int(peer["peer_id"])
                or peer["title_marker_verified"] is not True
                for peer in created_peers.values()
            )
            or len({peer["peer_id"] for peer in created_peers.values()})
            != len(created_peers)
        ):
            raise ValueError("created peer evidence is incomplete")
    if current_phase == "teardown":
        verdicts = checkpoint["verdicts"]
        domains = verdicts.get("domains") if isinstance(verdicts, dict) else None
        if checkpoint["cleanup_obligations"] or not isinstance(domains, dict) or domains.get(
            "cleanup"
        ) != "green":
            raise ValueError("cleanup is not green")
    current_index = SCENARIO_PHASES.index(current_phase)
    advanced["phase"] = SCENARIO_PHASES[current_index + 1]
    return advanced


def build_scenario_preflight_plan(scenario_key: str, fingerprint: dict) -> dict:
    spec = select_scenarios(scenario_key)[0]
    _validate_compatibility_fingerprint(scenario_key, fingerprint)
    required_account_roles = (
        ("operator", "lab_peer")
        if spec.source_family == "basic"
        else ("operator",)
    )
    bindings = fingerprint["account_role_binding"]
    for role in required_account_roles:
        if role not in bindings:
            raise ValueError(f"missing required account role: {role}")
    return {
        "scenario_key": scenario_key,
        "source_family": spec.source_family,
        "source_protected": spec.source_protected,
        "destination_family": spec.destination_family,
        "destination_private": True,
        "destination_owner_only": True,
        "discussion_kind": spec.discussion_kind,
        "discussion_protected": spec.discussion_protected,
        "required_account_roles": required_account_roles,
        "basic_group_normalization": spec.source_family == "basic",
        "content_profile": spec.content_profile,
        "required_domains": required_scenario_domains(spec),
    }


def build_scenario_provisioning_intents(
    scenario_key: str, fingerprint: dict
) -> tuple[dict, ...]:
    plan = build_scenario_preflight_plan(scenario_key, fingerprint)
    intents = []

    def create_peer(role: str, family: str, *, owner_only: bool) -> None:
        if family == "basic":
            method = "messages.createChat"
            parameters = {
                "family": family,
                "private": True,
                "owner_only": False,
                "participant_roles": ("lab_peer",),
                "title_marker_required": True,
            }
        else:
            method = "channels.createChannel"
            parameters = {
                "family": family,
                "private": True,
                "owner_only": owner_only,
                "participant_roles": (),
                "broadcast": family == "channel",
                "megagroup": family in {"supergroup", "forum"},
                "forum": family == "forum",
                "title_marker_required": True,
            }
        intents.append(
            {
                "intent_key": f"{scenario_key}:create:{role}",
                "method": method,
                "target_role": role,
                "parameters": parameters,
            }
        )

    def protect_peer(role: str) -> None:
        intents.append(
            {
                "intent_key": f"{scenario_key}:protect:{role}",
                "method": "messages.toggleNoForwards",
                "target_role": role,
                "parameters": {"enabled": True},
            }
        )

    def link_discussion(side: str) -> None:
        intents.append(
            {
                "intent_key": f"{scenario_key}:verify:{side}_discussion_eligibility",
                "method": "channels.getGroupsForDiscussion",
                "target_role": f"{side}_discussion",
                "parameters": {
                    "candidate_group_role": f"{side}_discussion",
                },
            }
        )
        intents.append(
            {
                "intent_key": f"{scenario_key}:link:{side}_discussion",
                "method": "channels.setDiscussionGroup",
                "target_role": side,
                "parameters": {
                    "broadcast_role": side,
                    "group_role": f"{side}_discussion",
                },
            }
        )

    create_peer(
        "source",
        plan["source_family"],
        owner_only=plan["source_family"] != "basic",
    )
    if plan["source_protected"]:
        protect_peer("source")
    if plan["discussion_kind"] != "none":
        discussion_family = (
            "forum" if plan["discussion_kind"] == "forum" else "supergroup"
        )
        create_peer("source_discussion", discussion_family, owner_only=True)
        if plan["discussion_protected"]:
            protect_peer("source_discussion")
        link_discussion("source")

    create_peer(
        "destination",
        plan["destination_family"],
        owner_only=plan["destination_owner_only"],
    )
    if plan["discussion_kind"] != "none":
        discussion_family = (
            "forum" if plan["discussion_kind"] == "forum" else "supergroup"
        )
        create_peer("destination_discussion", discussion_family, owner_only=True)
        link_discussion("destination")
    return tuple(intents)


def scenario_peer_title(lab_id: str, scenario_key: str, role: str) -> str:
    if (
        not isinstance(lab_id, str)
        or len(lab_id) != 24
        or any(char not in "0123456789abcdef" for char in lab_id)
    ):
        raise ValueError("invalid lab id")
    spec = select_scenarios(scenario_key)[0]
    allowed_roles = {"source", "destination"}
    if spec.discussion_kind != "none":
        allowed_roles |= {"source_discussion", "destination_discussion"}
    if role not in allowed_roles:
        raise ValueError(f"invalid scenario peer role: {role}")
    return f"{LAB_MARKER} {lab_id} {scenario_key} {role}"


def materialize_scenario_intent_request(
    intent: dict,
    fingerprint: dict,
    *,
    lab_id: str,
    resolved_roles: dict,
):
    scenario_key = (
        fingerprint.get("scenario_key") if isinstance(fingerprint, dict) else ""
    )
    expected = {
        candidate["intent_key"]: candidate
        for candidate in build_scenario_provisioning_intents(
            scenario_key, fingerprint
        )
    }
    if (
        not isinstance(intent, dict)
        or not isinstance(intent.get("intent_key"), str)
        or expected.get(intent["intent_key"]) != intent
    ):
        raise ValueError("intent does not match scenario plan")
    if not isinstance(resolved_roles, dict):
        raise ValueError("resolved roles must be an object")

    def resolved(role: str):
        if role not in resolved_roles:
            raise ValueError(f"missing resolved role: {role}")
        return resolved_roles[role]

    method = intent["method"]
    role = intent["target_role"]
    title = scenario_peer_title(lab_id, scenario_key, role)
    parameters = intent["parameters"]
    if method == "messages.createChat":
        return functions.messages.CreateChatRequest(
            users=[resolved(participant) for participant in parameters["participant_roles"]],
            title=title,
        )
    if method == "channels.createChannel":
        return functions.channels.CreateChannelRequest(
            title=title,
            about=f"{title} disposable fixture",
            broadcast=parameters["broadcast"],
            megagroup=parameters["megagroup"],
            forum=parameters["forum"],
        )
    if method == "messages.toggleNoForwards":
        return functions.messages.ToggleNoForwardsRequest(
            peer=resolved(role),
            enabled=parameters["enabled"],
        )
    if method == "channels.getGroupsForDiscussion":
        return functions.channels.GetGroupsForDiscussionRequest()
    if method == "channels.setDiscussionGroup":
        return functions.channels.SetDiscussionGroupRequest(
            broadcast=resolved(parameters["broadcast_role"]),
            group=resolved(parameters["group_role"]),
        )
    raise ValueError(f"unsupported provisioning method: {method}")


def _validated_scenario_operations(checkpoint: dict) -> tuple[str, dict[str, dict]]:
    phase = scenario_resume_phase(checkpoint)
    if phase != "create":
        raise ValueError(f"scenario intent phase must be create, got {phase}")
    fingerprint = checkpoint["compatibility_fingerprint"]
    scenario_key = fingerprint["scenario_key"]
    expected = {
        intent["intent_key"]: intent
        for intent in build_scenario_provisioning_intents(scenario_key, fingerprint)
    }
    operation_fields = {"method", "target_role", "parameters", "state"}
    for intent_key, operation in checkpoint["outbound_operations"].items():
        expected_intent = expected.get(intent_key)
        if (
            expected_intent is None
            or not isinstance(operation, dict)
            or set(operation) != operation_fields
            or operation["state"]
            not in {"prepared", "dispatched", "confirmed", "ambiguous", "blocked"}
            or {
                "intent_key": intent_key,
                "method": operation["method"],
                "target_role": operation["target_role"],
                "parameters": operation["parameters"],
            }
            != expected_intent
        ):
            raise ValueError(f"invalid scenario operation: {intent_key}")
    expected_keys = tuple(expected)
    present_keys = set(checkpoint["outbound_operations"])
    if present_keys != set(expected_keys[: len(present_keys)]):
        raise ValueError("scenario operations are not a serial plan prefix")
    for intent_key in expected_keys[: max(0, len(present_keys) - 1)]:
        if checkpoint["outbound_operations"][intent_key]["state"] != "confirmed":
            raise ValueError("prior provisioning intent is incomplete")
    return scenario_key, expected


def prepare_scenario_intent(checkpoint: dict, intent: dict) -> dict:
    _, expected = _validated_scenario_operations(checkpoint)
    if (
        not isinstance(intent, dict)
        or not isinstance(intent.get("intent_key"), str)
        or expected.get(intent["intent_key"]) != intent
    ):
        raise ValueError("intent does not match scenario plan")
    prepared = deepcopy(checkpoint)
    intent_key = intent["intent_key"]
    if intent_key not in prepared["outbound_operations"]:
        expected_keys = tuple(expected)
        next_intent_key = expected_keys[len(prepared["outbound_operations"])]
        if intent_key != next_intent_key or any(
            operation["state"] != "confirmed"
            for operation in prepared["outbound_operations"].values()
        ):
            raise ValueError("prior provisioning intent is incomplete")
        prepared["outbound_operations"][intent_key] = {
            "method": intent["method"],
            "target_role": intent["target_role"],
            "parameters": deepcopy(intent["parameters"]),
            "state": "prepared",
        }
    return prepared


def mark_scenario_intent_dispatched(checkpoint: dict, intent_key: str) -> dict:
    _validated_scenario_operations(checkpoint)
    operation = checkpoint["outbound_operations"].get(intent_key)
    if not isinstance(operation, dict) or operation.get("state") != "prepared":
        raise ValueError(f"intent is not prepared: {intent_key}")
    dispatched = deepcopy(checkpoint)
    dispatched["outbound_operations"][intent_key]["state"] = "dispatched"
    return dispatched


def record_scenario_intent_outcome(
    checkpoint: dict, intent_key: str, outcome: str
) -> dict:
    if outcome not in {"confirmed", "ambiguous", "blocked"}:
        raise ValueError(f"invalid intent outcome: {outcome}")
    _validated_scenario_operations(checkpoint)
    operation = checkpoint["outbound_operations"].get(intent_key)
    if not isinstance(operation, dict):
        raise ValueError(f"unknown scenario intent: {intent_key}")
    if operation["state"] == outcome:
        return deepcopy(checkpoint)
    if operation["state"] != "dispatched":
        raise ValueError(f"intent is not dispatched: {intent_key}")
    resolved = deepcopy(checkpoint)
    resolved["outbound_operations"][intent_key]["state"] = outcome
    return resolved


def reconcile_scenario_intent(
    checkpoint: dict, intent_key: str, *, observed: bool | None
) -> dict:
    _validated_scenario_operations(checkpoint)
    operation = checkpoint["outbound_operations"].get(intent_key)
    if not isinstance(operation, dict) or operation.get("state") not in {
        "dispatched",
        "ambiguous",
    }:
        raise ValueError(f"intent does not require reconciliation: {intent_key}")
    if observed is not None and type(observed) is not bool:
        raise ValueError("reconciliation observation must be boolean or None")
    reconciled = deepcopy(checkpoint)
    reconciled["outbound_operations"][intent_key]["state"] = (
        "ambiguous" if observed is None else "confirmed" if observed else "prepared"
    )
    return reconciled


async def dispatch_scenario_intent(
    checkpoint: dict,
    intent: dict,
    *,
    persist,
    execute,
) -> dict:
    _validated_scenario_operations(checkpoint)
    intent_key = intent.get("intent_key") if isinstance(intent, dict) else None
    existing = checkpoint["outbound_operations"].get(intent_key)
    if existing is not None:
        prepared = prepare_scenario_intent(checkpoint, intent)
        state = existing["state"]
        if state == "confirmed":
            return prepared
        if state != "prepared":
            raise PolicyError(f"intent requires reconciliation: {intent_key}")
    else:
        prepared = prepare_scenario_intent(checkpoint, intent)
        persist(prepared)

    dispatched = mark_scenario_intent_dispatched(prepared, intent_key)
    persist(dispatched)
    try:
        observation = await execute(deepcopy(intent))
        observed = _apply_scenario_intent_observation(
            dispatched,
            intent,
            observation,
        )
    except BaseException as exc:
        ambiguous = record_scenario_intent_outcome(
            dispatched, intent_key, "ambiguous"
        )
        try:
            persist(ambiguous)
        except Exception as persist_error:
            raise persist_error from exc
        raise
    confirmed = record_scenario_intent_outcome(
        observed, intent_key, "confirmed"
    )
    persist(confirmed)
    return confirmed


def _scenario_peer_families(fingerprint: dict) -> dict[str, str]:
    scenario_key = fingerprint["scenario_key"]
    return {
        intent["target_role"]: intent["parameters"]["family"]
        for intent in build_scenario_provisioning_intents(
            scenario_key, fingerprint
        )
        if intent["method"] in {"messages.createChat", "channels.createChannel"}
    }


def _scenario_peer_ref(family: str, peer_id: int):
    if family == "basic":
        return types.PeerChat(peer_id)
    return types.PeerChannel(peer_id)


async def execute_scenario_provisioning_intent(
    tg,
    checkpoint: dict,
    intent: dict,
    fingerprint: dict,
    *,
    lab_id: str,
    account_alias: str,
    resolved_account_roles: dict | None = None,
) -> dict:
    comparison = compare_compatibility_fingerprints(
        fingerprint, checkpoint["compatibility_fingerprint"]
    )
    if not comparison["compatible"]:
        raise ValueError("checkpoint fingerprint is not compatible")
    families = _scenario_peer_families(fingerprint)
    resolved_roles = dict(resolved_account_roles or {})
    for role, peer in checkpoint["created_peers"].items():
        resolved_roles[role] = await tg.get_input_entity(
            _scenario_peer_ref(families[role], peer["peer_id"])
        )
    request = materialize_scenario_intent_request(
        intent,
        fingerprint,
        lab_id=lab_id,
        resolved_roles=resolved_roles,
    )
    response = await _audited_mutation(
        "mirror-lab-scenario-provision",
        account_alias,
        {
            "scenario_key": fingerprint["scenario_key"],
            "intent_key": intent["intent_key"],
            "method": intent["method"],
            "role": intent["target_role"],
        },
        lambda: tg(request),
        ambiguous_errors=True,
    )
    method = intent["method"]
    role = intent["target_role"]
    if method in {"messages.createChat", "channels.createChannel"}:
        expected_title = scenario_peer_title(
            lab_id, fingerprint["scenario_key"], role
        )
        matches = [
            peer
            for peer in getattr(response, "chats", ())
            if getattr(peer, "title", None) == expected_title
            and getattr(peer, "creator", False)
        ]
        if len(matches) != 1 or not _is_positive_int(matches[0].id):
            raise ValueError("created peer response lacks exact owned lab peer")
        peer = matches[0]
        family = intent["parameters"]["family"]
        shape_ok = family == "basic" or (
            bool(getattr(peer, "broadcast", False))
            == intent["parameters"]["broadcast"]
            and bool(getattr(peer, "megagroup", False))
            == intent["parameters"]["megagroup"]
            and bool(getattr(peer, "forum", False))
            == intent["parameters"]["forum"]
        )
        if not shape_ok:
            raise ValueError("created peer topology does not match intent")
        return {
            "peer_role": role,
            "peer_id": peer.id,
            "title_marker_verified": True,
        }
    if method == "messages.toggleNoForwards":
        peer = checkpoint["created_peers"][role]
        entity = await tg.get_entity(
            _scenario_peer_ref(families[role], peer["peer_id"])
        )
        return {"protected": bool(getattr(entity, "noforwards", False))}
    if method == "channels.getGroupsForDiscussion":
        candidate_role = intent["parameters"]["candidate_group_role"]
        candidate_id = checkpoint["created_peers"][candidate_role]["peer_id"]
        eligible = any(
            getattr(peer, "id", None) == candidate_id
            for peer in getattr(response, "chats", ())
        )
        return {"eligible": eligible}
    if method == "channels.setDiscussionGroup":
        broadcast_role = intent["parameters"]["broadcast_role"]
        group_role = intent["parameters"]["group_role"]
        full = await tg(
            functions.channels.GetFullChannelRequest(
                channel=resolved_roles[broadcast_role]
            )
        )
        linked_id = getattr(getattr(full, "full_chat", None), "linked_chat_id", None)
        return {
            "linked": linked_id
            == checkpoint["created_peers"][group_role]["peer_id"]
        }
    raise ValueError(f"unsupported provisioning method: {method}")


async def provision_scenario_live(
    tg,
    checkpoint: dict,
    fingerprint: dict,
    *,
    lab_id: str,
    account_alias: str,
    persist,
    resolved_account_roles: dict | None = None,
) -> dict:
    current = deepcopy(checkpoint)

    def persist_current(value):
        nonlocal current
        current = deepcopy(value)
        persist(current)

    for intent in build_scenario_provisioning_intents(
        fingerprint["scenario_key"], fingerprint
    ):
        async def execute(candidate, *, _current=lambda: current):
            return await execute_scenario_provisioning_intent(
                tg,
                _current(),
                candidate,
                fingerprint,
                lab_id=lab_id,
                account_alias=account_alias,
                resolved_account_roles=resolved_account_roles,
            )

        current = await dispatch_scenario_intent(
            current,
            intent,
            persist=persist_current,
            execute=execute,
        )
    current = advance_scenario_phase(current, "create")
    persist(current)
    return current


async def teardown_scenario_peers(
    tg,
    checkpoint: dict,
    fingerprint: dict,
    *,
    lab_id: str,
    account_alias: str,
    persist,
) -> dict:
    current = deepcopy(checkpoint)
    families = _scenario_peer_families(fingerprint)
    create_roles = tuple(families)
    for role in reversed(create_roles):
        peer = current["created_peers"].get(role)
        if peer is None:
            continue
        if families[role] == "basic":
            raise PolicyError("basic-group teardown requires dedicated lab-peer support")
        obligation = next(
            (
                item
                for item in current["cleanup_obligations"]
                if isinstance(item, dict) and item.get("peer_role") == role
            ),
            None,
        )
        deleting = isinstance(obligation, dict) and obligation.get("state") == "deleting"
        ref = _scenario_peer_ref(families[role], peer["peer_id"])
        try:
            entity = await tg.get_entity(ref)
        except (ChannelInvalidError, ChannelPrivateError):
            if not deleting:
                raise
            current["created_peers"].pop(role, None)
            current["cleanup_obligations"] = [
                item
                for item in current["cleanup_obligations"]
                if not isinstance(item, dict) or item.get("peer_role") != role
            ]
            persist(current)
            continue
        expected_title = scenario_peer_title(
            lab_id, fingerprint["scenario_key"], role
        )
        if (
            getattr(entity, "title", None) != expected_title
            or not getattr(entity, "creator", False)
        ):
            raise PolicyError(f"{role}: peer is not the exact owned scenario fixture")
        current["cleanup_obligations"] = [
            {"peer_role": role, "state": "deleting"}
            if isinstance(item, dict) and item.get("peer_role") == role
            else item
            for item in current["cleanup_obligations"]
        ]
        persist(current)
        await _audited_mutation(
            "mirror-lab-scenario-teardown",
            account_alias,
            {"scenario_key": fingerprint["scenario_key"], "role": role},
            lambda: tg(functions.channels.DeleteChannelRequest(channel=entity)),
            ambiguous_errors=True,
        )
        current["created_peers"].pop(role, None)
        current["cleanup_obligations"] = [
            item
            for item in current["cleanup_obligations"]
            if not isinstance(item, dict) or item.get("peer_role") != role
        ]
        persist(current)
    return current


async def _owned_scenario_entity(
    tg,
    checkpoint: dict,
    fingerprint: dict,
    *,
    lab_id: str,
    role: str,
):
    families = _scenario_peer_families(fingerprint)
    peer = checkpoint["created_peers"].get(role)
    if role not in families or not isinstance(peer, dict):
        raise PolicyError(f"missing owned scenario peer: {role}")
    entity = await tg.get_entity(
        _scenario_peer_ref(families[role], peer["peer_id"])
    )
    expected_title = scenario_peer_title(
        lab_id, fingerprint["scenario_key"], role
    )
    if (
        getattr(entity, "title", None) != expected_title
        or not getattr(entity, "creator", False)
    ):
        raise PolicyError(f"{role}: peer is not the exact owned scenario fixture")
    return entity


async def _scenario_canary_write(
    tg,
    *,
    account_alias: str,
    scenario_key: str,
    side: str,
    operation: str,
    request,
    record,
):
    operation_key = f"{side}:{operation}"
    record(operation_key, "prepared")
    record(operation_key, "dispatched")
    try:
        result = await _audited_mutation(
            "mirror-lab-scenario-content",
            account_alias,
            {
                "scenario_key": scenario_key,
                "side": side,
                "operation": operation,
            },
            lambda: tg(request),
            ambiguous_errors=True,
        )
    except BaseException:
        record(operation_key, "ambiguous")
        raise
    record(operation_key, "confirmed")
    return result


async def _bounded_scenario_readback(fetch, accept, description: str):
    last_error = None
    for attempt in range(5):
        try:
            result = await fetch()
        except MsgIdInvalidError as exc:
            last_error = exc
        else:
            if accept(result):
                return result
        if attempt < 4:
            await asyncio.sleep(0.25)
    raise ValueError(f"{description} was not observed after bounded polling") from last_error


async def verify_channel_comment_thread_live(
    tg,
    checkpoint: dict,
    fingerprint: dict,
    *,
    lab_id: str,
    account_alias: str,
    side: str,
    record,
) -> dict:
    scenario_key = fingerprint.get("scenario_key")
    spec = select_scenarios(scenario_key)[0]
    if spec.discussion_kind != "plain":
        raise PolicyError("channel comment canary requires a plain linked discussion")
    if side not in {"source", "destination"}:
        raise ValueError(f"invalid channel comment side: {side}")
    if compare_compatibility_fingerprints(
        fingerprint, checkpoint["compatibility_fingerprint"]
    )["compatible"] is not True:
        raise ValueError("checkpoint fingerprint is not compatible")
    if checkpoint.get("phase") != "seed":
        raise ValueError("channel comment canary requires seed phase")
    if not callable(record):
        raise ValueError("channel comment canary recorder is required")

    channel = await _owned_scenario_entity(
        tg,
        checkpoint,
        fingerprint,
        lab_id=lab_id,
        role=side,
    )
    discussion_role = f"{side}_discussion"
    discussion = await _owned_scenario_entity(
        tg,
        checkpoint,
        fingerprint,
        lab_id=lab_id,
        role=discussion_role,
    )
    channel_input = await tg.get_input_entity(
        _scenario_peer_ref("channel", channel.id)
    )
    discussion_input = await tg.get_input_entity(
        _scenario_peer_ref("supergroup", discussion.id)
    )
    marker_prefix = f"{LAB_MARKER}:{lab_id}:{scenario_key}:comments:{side}"

    post_marker = f"{marker_prefix}:post"
    post_request = functions.messages.SendMessageRequest(
        peer=channel_input,
        message=post_marker,
        random_id=_random_id(),
    )
    post_update = await _scenario_canary_write(
        tg,
        account_alias=account_alias,
        scenario_key=scenario_key,
        side=side,
        operation="post",
        request=post_request,
        record=record,
    )
    post_id = _sent_message_id(
        post_update,
        random_id=post_request.random_id,
        peer_id=channel.id,
        marker=post_marker,
    )

    def discussion_roots(result):
        return [
            message
            for message in getattr(result, "messages", ())
            if getattr(getattr(message, "peer_id", None), "channel_id", None)
            == discussion.id
            and getattr(getattr(message, "fwd_from", None), "channel_post", None)
            == post_id
            and getattr(message, "message", None) == post_marker
        ]

    discussion_result = await _bounded_scenario_readback(
        lambda: tg(
            functions.messages.GetDiscussionMessageRequest(
                peer=channel_input,
                msg_id=post_id,
            )
        ),
        lambda result: len(discussion_roots(result)) == 1,
        "native discussion root",
    )
    roots = discussion_roots(discussion_result)
    if not _is_positive_int(getattr(roots[0], "id", None)):
        raise ValueError("native discussion root has no valid message id")
    root = roots[0]

    comment_marker = f"{marker_prefix}:comment"
    comment_request = functions.messages.SendMessageRequest(
        peer=discussion_input,
        message=comment_marker,
        reply_to=types.InputReplyToMessage(reply_to_msg_id=root.id),
        random_id=_random_id(),
    )
    comment_update = await _scenario_canary_write(
        tg,
        account_alias=account_alias,
        scenario_key=scenario_key,
        side=side,
        operation="comment",
        request=comment_request,
        record=record,
    )
    comment_id = _sent_message_id(
        comment_update,
        random_id=comment_request.random_id,
        peer_id=discussion.id,
        marker=comment_marker,
    )
    nested_marker = f"{marker_prefix}:nested"
    await _scenario_canary_write(
        tg,
        account_alias=account_alias,
        scenario_key=scenario_key,
        side=side,
        operation="nested_reply",
        request=functions.messages.SendMessageRequest(
            peer=discussion_input,
            message=nested_marker,
            reply_to=types.InputReplyToMessage(
                reply_to_msg_id=comment_id,
                top_msg_id=root.id,
            ),
            random_id=_random_id(),
        ),
        record=record,
    )

    def marker_messages(result):
        return {
            marker: [
                message
                for message in getattr(result, "messages", ())
                if getattr(message, "message", None) == marker
            ]
            for marker in (comment_marker, nested_marker)
        }

    replies = await _bounded_scenario_readback(
        lambda: tg(
            functions.messages.GetRepliesRequest(
                peer=discussion_input,
                msg_id=root.id,
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=50,
                max_id=0,
                min_id=0,
                hash=0,
            )
        ),
        lambda result: all(
            len(messages) == 1
            for messages in marker_messages(result).values()
        ),
        "native comment reply chain",
    )
    by_marker = marker_messages(replies)
    if any(len(messages) != 1 for messages in by_marker.values()):
        raise ValueError("native comment markers were not observed exactly once")
    comment = by_marker[comment_marker][0]
    nested = by_marker[nested_marker][0]
    if (
        getattr(getattr(comment, "reply_to", None), "reply_to_msg_id", None)
        != root.id
        or getattr(getattr(nested, "reply_to", None), "reply_to_msg_id", None)
        != comment.id
        or getattr(getattr(nested, "reply_to", None), "reply_to_top_id", None)
        != root.id
    ):
        raise ValueError("native comment reply chain does not match expected parents")
    return {
        "side": side,
        "post": "confirmed",
        "discussion_root": "confirmed",
        "comment": "confirmed",
        "nested_reply": "confirmed",
    }


async def verify_forum_topics_live(
    tg,
    checkpoint: dict,
    fingerprint: dict,
    *,
    lab_id: str,
    account_alias: str,
    side: str,
    record,
) -> dict:
    scenario_key = fingerprint.get("scenario_key")
    spec = select_scenarios(scenario_key)[0]
    if spec.source_family != "forum" or spec.discussion_kind != "none":
        raise PolicyError("forum topic canary requires a standalone forum")
    if side not in {"source", "destination"}:
        raise ValueError(f"invalid forum canary side: {side}")
    if compare_compatibility_fingerprints(
        fingerprint, checkpoint["compatibility_fingerprint"]
    )["compatible"] is not True:
        raise ValueError("checkpoint fingerprint is not compatible")
    if checkpoint.get("phase") != "seed":
        raise ValueError("forum topic canary requires seed phase")
    if not callable(record):
        raise ValueError("forum topic canary recorder is required")

    forum = await _owned_scenario_entity(
        tg,
        checkpoint,
        fingerprint,
        lab_id=lab_id,
        role=side,
    )
    if not getattr(forum, "forum", False):
        raise PolicyError(f"{side}: owned scenario peer is not a forum")
    forum_input = await tg.get_input_entity(
        _scenario_peer_ref("forum", forum.id)
    )
    general_result = await tg(
        functions.messages.GetForumTopicsByIDRequest(
            peer=forum_input,
            topics=[1],
        )
    )
    general_topics = [
        topic
        for topic in getattr(general_result, "topics", ())
        if getattr(topic, "id", None) == 1
        and type(topic).__name__ != "ForumTopicDeleted"
    ]
    if len(general_topics) != 1:
        raise ValueError("General forum topic was not observed exactly once")

    marker_prefix = f"{LAB_MARKER}:{lab_id}:{scenario_key}:forum:{side}"
    custom_title = f"{marker_prefix}:topic"
    topic_update = await _scenario_canary_write(
        tg,
        account_alias=account_alias,
        scenario_key=scenario_key,
        side=side,
        operation="custom_topic",
        request=functions.messages.CreateForumTopicRequest(
            peer=forum_input,
            title=custom_title,
            icon_color=0x6FB9F0,
            random_id=_random_id(),
        ),
        record=record,
    )
    topic_messages = [
        getattr(update, "message", None)
        for update in getattr(topic_update, "updates", ())
    ]
    created_topics = [
        message
        for message in topic_messages
        if message is not None
        and isinstance(
            getattr(message, "action", None),
            types.MessageActionTopicCreate,
        )
        and getattr(message.action, "title", None) == custom_title
        and _is_positive_int(getattr(message, "id", None))
    ]
    if len(created_topics) != 1:
        raise ValueError("custom forum topic creation was not observed exactly once")
    topic_id = created_topics[0].id
    custom_result = await tg(
        functions.messages.GetForumTopicsByIDRequest(
            peer=forum_input,
            topics=[topic_id],
        )
    )
    custom_topics = [
        topic
        for topic in getattr(custom_result, "topics", ())
        if getattr(topic, "id", None) == topic_id
        and getattr(topic, "title", None) == custom_title
        and not getattr(topic, "closed", False)
        and not getattr(topic, "hidden", False)
        and type(topic).__name__ != "ForumTopicDeleted"
    ]
    if len(custom_topics) != 1:
        raise ValueError("custom forum topic readback did not match creation")

    async def send(operation: str, marker: str, reply_to=None) -> int:
        request = functions.messages.SendMessageRequest(
            peer=forum_input,
            message=marker,
            reply_to=reply_to,
            random_id=_random_id(),
        )
        update = await _scenario_canary_write(
            tg,
            account_alias=account_alias,
            scenario_key=scenario_key,
            side=side,
            operation=operation,
            request=request,
            record=record,
        )
        return _sent_message_id(
            update,
            random_id=request.random_id,
            peer_id=forum.id,
            marker=marker,
        )

    general_marker = f"{marker_prefix}:general"
    general_id = await send("general_message", general_marker)
    general_reply_marker = f"{marker_prefix}:general_reply"
    await send(
        "general_reply",
        general_reply_marker,
        types.InputReplyToMessage(reply_to_msg_id=general_id, top_msg_id=1),
    )
    custom_marker = f"{marker_prefix}:custom"
    custom_id = await send(
        "custom_message",
        custom_marker,
        types.InputReplyToMessage(
            reply_to_msg_id=topic_id,
            top_msg_id=topic_id,
        ),
    )
    custom_reply_marker = f"{marker_prefix}:custom_reply"
    await send(
        "custom_reply",
        custom_reply_marker,
        types.InputReplyToMessage(
            reply_to_msg_id=custom_id,
            top_msg_id=topic_id,
        ),
    )

    async def topic_messages(root_id: int, markers: tuple[str, str]):
        result = await tg(
            functions.messages.GetRepliesRequest(
                peer=forum_input,
                msg_id=root_id,
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=50,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )
        found = {
            marker: [
                message
                for message in getattr(result, "messages", ())
                if getattr(message, "message", None) == marker
            ]
            for marker in markers
        }
        if any(len(messages) != 1 for messages in found.values()):
            raise ValueError("forum topic markers were not observed exactly once")
        return tuple(found[marker][0] for marker in markers)

    general_message, general_reply = await topic_messages(
        1, (general_marker, general_reply_marker)
    )
    custom_message, custom_reply = await topic_messages(
        topic_id, (custom_marker, custom_reply_marker)
    )
    failures = []
    general_header = getattr(general_reply, "reply_to", None)
    custom_header = getattr(custom_message, "reply_to", None)
    custom_reply_header = getattr(custom_reply, "reply_to", None)
    if getattr(general_header, "reply_to_msg_id", None) != general_message.id:
        failures.append("general_parent_mismatch")
    if getattr(general_header, "reply_to_top_id", None) not in {None, 1}:
        failures.append("general_top_invalid")
    if getattr(custom_header, "reply_to_msg_id", None) != topic_id:
        failures.append("custom_root_parent_mismatch")
    if (
            getattr(
                custom_header,
                "reply_to_top_id",
                None,
            )
            or getattr(
                custom_header,
                "reply_to_msg_id",
                None,
            )
    ) != topic_id:
        failures.append("custom_effective_root_mismatch")
    if not getattr(custom_header, "forum_topic", False):
        failures.append("custom_forum_flag_missing")
    if getattr(custom_reply_header, "reply_to_msg_id", None) != custom_message.id:
        failures.append("custom_reply_parent_mismatch")
    if getattr(custom_reply_header, "reply_to_top_id", None) != topic_id:
        failures.append("custom_reply_top_mismatch")
    if failures:
        raise ValueError("forum topic reply chain mismatch: " + ",".join(failures))
    return {
        "side": side,
        "general_topic": "confirmed",
        "custom_topic": "confirmed",
        "general_reply": "confirmed",
        "custom_reply": "confirmed",
    }


async def verify_supergroup_reply_chain_live(
    tg,
    checkpoint: dict,
    fingerprint: dict,
    *,
    lab_id: str,
    account_alias: str,
    side: str,
    record,
) -> dict:
    scenario_key = fingerprint.get("scenario_key")
    spec = select_scenarios(scenario_key)[0]
    if spec.source_family != "supergroup" or spec.discussion_kind != "none":
        raise PolicyError("supergroup canary requires a standalone supergroup")
    if side not in {"source", "destination"}:
        raise ValueError(f"invalid supergroup canary side: {side}")
    if compare_compatibility_fingerprints(
        fingerprint, checkpoint["compatibility_fingerprint"]
    )["compatible"] is not True:
        raise ValueError("checkpoint fingerprint is not compatible")
    if checkpoint.get("phase") != "seed":
        raise ValueError("supergroup canary requires seed phase")
    if not callable(record):
        raise ValueError("supergroup canary recorder is required")

    group = await _owned_scenario_entity(
        tg,
        checkpoint,
        fingerprint,
        lab_id=lab_id,
        role=side,
    )
    if not getattr(group, "megagroup", False) or getattr(group, "forum", False):
        raise PolicyError(f"{side}: owned scenario peer is not a plain supergroup")
    group_input = await tg.get_input_entity(
        _scenario_peer_ref("supergroup", group.id)
    )
    marker_prefix = f"{LAB_MARKER}:{lab_id}:{scenario_key}:group:{side}"

    async def send(operation: str, marker: str, reply_to=None) -> int:
        request = functions.messages.SendMessageRequest(
            peer=group_input,
            message=marker,
            reply_to=reply_to,
            random_id=_random_id(),
        )
        update = await _scenario_canary_write(
            tg,
            account_alias=account_alias,
            scenario_key=scenario_key,
            side=side,
            operation=operation,
            request=request,
            record=record,
        )
        return _sent_message_id(
            update,
            random_id=request.random_id,
            peer_id=group.id,
            marker=marker,
        )

    root_marker = f"{marker_prefix}:root"
    root_id = await send("root", root_marker)
    reply_marker = f"{marker_prefix}:reply"
    reply_id = await send(
        "direct_reply",
        reply_marker,
        types.InputReplyToMessage(reply_to_msg_id=root_id),
    )
    nested_marker = f"{marker_prefix}:nested"
    nested_id = await send(
        "nested_reply",
        nested_marker,
        types.InputReplyToMessage(reply_to_msg_id=reply_id),
    )

    markers = (root_marker, reply_marker, nested_marker)

    def marker_messages(result):
        return {
            marker: [
                message
                for message in getattr(result, "messages", ())
                if getattr(message, "message", None) == marker
            ]
            for marker in markers
        }

    messages = await _bounded_scenario_readback(
        lambda: tg(
            functions.channels.GetMessagesRequest(
                channel=group_input,
                id=[
                    types.InputMessageID(id=message_id)
                    for message_id in (root_id, reply_id, nested_id)
                ],
            )
        ),
        lambda result: all(
            len(found) == 1 for found in marker_messages(result).values()
        ),
        "supergroup reply chain",
    )
    by_marker = marker_messages(messages)
    root = by_marker[root_marker][0]
    reply = by_marker[reply_marker][0]
    nested = by_marker[nested_marker][0]
    if getattr(getattr(reply, "reply_to", None), "reply_to_msg_id", None) != root.id:
        raise ValueError("supergroup direct reply parent mismatch")
    if (
        getattr(getattr(nested, "reply_to", None), "reply_to_msg_id", None)
        != reply.id
    ):
        raise ValueError("supergroup nested reply parent mismatch")
    return {
        "side": side,
        "root": "confirmed",
        "direct_reply": "confirmed",
        "nested_reply": "confirmed",
    }


def _apply_scenario_intent_observation(
    checkpoint: dict,
    intent: dict,
    observation: object,
) -> dict:
    method = intent["method"]
    observed = deepcopy(checkpoint)
    if method in {"messages.createChat", "channels.createChannel"}:
        if (
            not isinstance(observation, dict)
            or set(observation)
            != {"peer_role", "peer_id", "title_marker_verified"}
            or observation["peer_role"] != intent["target_role"]
            or not _is_positive_int(observation["peer_id"])
            or observation["title_marker_verified"] is not True
        ):
            raise ValueError("invalid created peer observation")
        entry = {
            "peer_id": observation["peer_id"],
            "title_marker_verified": True,
        }
        existing = observed["created_peers"].get(intent["target_role"])
        if existing is not None and existing != entry:
            raise ValueError("created peer observation conflicts with checkpoint")
        if any(
            role != intent["target_role"] and peer.get("peer_id") == entry["peer_id"]
            for role, peer in observed["created_peers"].items()
            if isinstance(peer, dict)
        ):
            raise ValueError("created peer observation reuses peer id")
        observed["created_peers"][intent["target_role"]] = entry
        obligation = {"peer_role": intent["target_role"]}
        if obligation not in observed["cleanup_obligations"]:
            observed["cleanup_obligations"].append(obligation)
        return observed
    expected_observations = {
        "messages.toggleNoForwards": {"protected": True},
        "channels.getGroupsForDiscussion": {"eligible": True},
        "channels.setDiscussionGroup": {"linked": True},
    }
    if observation != expected_observations.get(method):
        raise ValueError(f"invalid intent observation: {method}")
    return observed


def compare_compatibility_fingerprints(expected: dict, actual: dict) -> dict:
    expected_key = expected.get("scenario_key") if isinstance(expected, dict) else ""
    _validate_compatibility_fingerprint(expected_key, expected)
    actual_key = actual.get("scenario_key") if isinstance(actual, dict) else ""
    _validate_compatibility_fingerprint(
        actual_key,
        actual,
        allow_version_mismatch=True,
    )
    fields = (
        "fingerprint_version",
        "scenario_key",
        "fixture_schema_version",
        "lab_code_digest",
        "mirror_code_digest",
        "telethon_version",
        "telegram_schema_layer",
        "account_role_binding",
        "config_digest",
    )
    reasons = [
        f"{field}_mismatch" for field in fields if expected[field] != actual[field]
    ]
    return {"compatible": not reasons, "reasons": reasons}


def classify_scenario_cell(
    scenario_key: str,
    checkpoint: dict | None,
    expected_fingerprint: dict,
    *,
    now: datetime | None,
    last_observed_at: datetime | None = None,
) -> dict:
    _validate_compatibility_fingerprint(scenario_key, expected_fingerprint)
    spec = next((spec for spec in required_scenarios() if spec.key == scenario_key), None)
    if spec is None:
        raise ValueError(f"unknown scenario key: {scenario_key}")
    if checkpoint is None:
        return {
            "scenario_key": scenario_key,
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
    if not isinstance(checkpoint, dict):
        raise ValueError(f"invalid scenario checkpoint: {scenario_key}")
    _validate_scenario_checkpoint_envelope(
        scenario_key,
        checkpoint,
        bind_fingerprint_to_scenario=False,
    )
    comparison = compare_compatibility_fingerprints(
        expected_fingerprint,
        checkpoint.get("compatibility_fingerprint"),
    )
    required_domains = required_scenario_domains(spec)
    reasons = list(comparison["reasons"])
    blocked = False
    red = False

    verdicts = checkpoint.get("verdicts")
    if not isinstance(verdicts, dict) or set(verdicts) != {"completed_at", "domains"}:
        reasons.append("result_metadata_invalid")
        blocked = True
        verdicts = {}
    raw_completed_at = verdicts.get("completed_at")
    completed_at_valid = (
        isinstance(raw_completed_at, datetime)
        and raw_completed_at.tzinfo is not None
        and raw_completed_at.utcoffset() is not None
    )
    completed_at = raw_completed_at if completed_at_valid else None
    expires_at = completed_at + EVIDENCE_TTL if completed_at is not None else None

    raw_domains = verdicts.get("domains")
    if not isinstance(raw_domains, dict):
        if "result_metadata_invalid" not in reasons:
            reasons.append("result_metadata_invalid")
        blocked = True
        domains = {}
    else:
        domains = dict(raw_domains)
        unknown_domains = set(domains) - set(required_domains)
        if unknown_domains:
            raise ValueError(f"unknown scenario domains: {scenario_key}")
        if any(value not in {"green", "red", "blocked"} for value in domains.values()):
            raise ValueError(f"invalid scenario domain status: {scenario_key}")
    for domain in required_domains:
        state = domains.get(domain)
        if state is None:
            reasons.append(f"domain_missing:{domain}")
            blocked = True
        elif state == "blocked":
            reasons.append(f"domain_blocked:{domain}")
            blocked = True
        elif state == "red":
            reasons.append(f"domain_red:{domain}")
            red = True

    phase = checkpoint.get("phase")
    obligations = checkpoint.get("cleanup_obligations")
    cleanup_pending = phase == "teardown" or (
        isinstance(obligations, list) and bool(obligations)
    )
    if cleanup_pending:
        cleanup = "pending"
        reasons.append("cleanup_pending")
    elif not isinstance(obligations, list):
        cleanup = "blocked"
        reasons.append("cleanup_blocked")
        blocked = True
    elif phase != "complete" or domains.get("cleanup") != "green":
        cleanup = "blocked"
        reasons.append("cleanup_blocked")
        blocked = True
    else:
        cleanup = "green"
    if phase not in {"complete", "teardown"}:
        reasons.append("phase_incomplete")
        blocked = True

    clock_reasons = []
    now_valid = (
        isinstance(now, datetime)
        and now.tzinfo is not None
        and now.utcoffset() is not None
    )
    observed_valid = (
        isinstance(last_observed_at, datetime)
        and last_observed_at.tzinfo is not None
        and last_observed_at.utcoffset() is not None
    )
    if now is None:
        clock_reasons.append("clock_unavailable")
    elif not now_valid:
        clock_reasons.append("clock_invalid")
    if not completed_at_valid and "clock_invalid" not in clock_reasons:
        clock_reasons.append("clock_invalid")
    if last_observed_at is not None and not observed_valid:
        if "clock_invalid" not in clock_reasons:
            clock_reasons.append("clock_invalid")
    if now_valid and completed_at_valid and now < raw_completed_at:
        clock_reasons.append("clock_before_completion")
    if now_valid and observed_valid and now < last_observed_at:
        clock_reasons.append("clock_moved_backward")
    if clock_reasons:
        freshness = "blocked"
        blocked = True
        reasons.extend(clock_reasons)
    elif now >= expires_at:
        freshness = "stale"
        reasons.append("evidence_expired")
    else:
        freshness = "fresh"

    if comparison["reasons"] or freshness == "stale":
        status = "stale"
    elif cleanup_pending:
        status = "cleanup_pending"
    elif blocked:
        status = "blocked"
    elif red:
        status = "red"
    else:
        status = "machine_compatible"
    return {
        "scenario_key": scenario_key,
        "status": status,
        "freshness": freshness,
        "machine_compatible": status == "machine_compatible",
        "reasons": reasons,
        "fingerprint_reasons": comparison["reasons"],
        "completed_at": completed_at,
        "expires_at": expires_at,
        "domains": domains,
        "cleanup": cleanup,
    }


def classify_scenario_matrix(
    checkpoints: dict[str, dict],
    expected_fingerprints: dict[str, dict],
    *,
    now: datetime | None,
    last_observed_at: datetime | None = None,
) -> dict:
    if not isinstance(checkpoints, dict) or not isinstance(expected_fingerprints, dict):
        raise ValueError("scenario matrix inputs must be objects")
    scenario_keys = tuple(spec.key for spec in required_scenarios())
    required_keys = set(scenario_keys)
    if set(expected_fingerprints) != required_keys:
        raise ValueError("expected fingerprints must cover every required scenario")
    if not set(checkpoints) <= required_keys:
        raise ValueError("unknown scenario checkpoint key")
    for scenario_key, fingerprint in expected_fingerprints.items():
        _validate_compatibility_fingerprint(scenario_key, fingerprint)
    for scenario_key, checkpoint in checkpoints.items():
        if (
            not isinstance(checkpoint, dict)
            or not isinstance(checkpoint.get("compatibility_fingerprint"), dict)
            or checkpoint["compatibility_fingerprint"].get("scenario_key")
            != scenario_key
        ):
            raise ValueError(f"checkpoint scenario key mismatch: {scenario_key}")

    cells = {
        scenario_key: classify_scenario_cell(
            scenario_key,
            checkpoints.get(scenario_key),
            expected_fingerprints[scenario_key],
            now=now,
            last_observed_at=last_observed_at,
        )
        for scenario_key in scenario_keys
    }
    statuses = (
        "missing",
        "stale",
        "blocked",
        "red",
        "cleanup_pending",
        "machine_compatible",
    )
    counts = {
        status: sum(cell["status"] == status for cell in cells.values())
        for status in statuses
    }
    non_green_cells = [
        scenario_key
        for scenario_key in scenario_keys
        if cells[scenario_key]["status"] != "machine_compatible"
        or cells[scenario_key]["cleanup"] != "green"
    ]
    return {
        "machine_green": not non_green_cells,
        "cells": cells,
        "counts": counts,
        "non_green_cells": non_green_cells,
    }


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or type(data.get("manifest_version")) is not int
        or data["manifest_version"] not in {2, 3}
    ):
        raise ValueError("unsupported lab manifest")
    source_version = data["manifest_version"]
    if source_version == 2 and "scenarios" in data:
        raise ValueError("v2 manifest must not contain scenarios")
    if source_version == 3 and not isinstance(data.get("scenarios"), dict):
        raise ValueError("v3 manifest requires scenarios object")
    for key in ("account_user_id", "lab_id", "channels", "seeded"):
        if key not in data:
            raise ValueError(f"lab manifest missing {key!r}")
    if not _is_positive_int(data["account_user_id"]):
        raise ValueError("invalid lab account user id")
    if not isinstance(data["channels"], dict):
        raise ValueError("invalid lab channels")
    if not isinstance(data["seeded"], dict):
        raise ValueError("invalid lab seeded state")
    lab_id = data["lab_id"]
    if (
        not isinstance(lab_id, str)
        or len(lab_id) != 24
        or any(char not in "0123456789abcdef" for char in lab_id)
    ):
        raise ValueError("invalid lab id")
    seen_peer_ids = set()
    for role, channel in data["channels"].items():
        if role not in CHANNEL_ROLES:
            raise ValueError(f"unknown lab channel role: {role}")
        if not isinstance(channel, dict):
            raise ValueError(f"invalid lab channel entry: {role}")
        peer_id = channel.get("peer_id")
        title = channel.get("title")
        if not _is_positive_int(peer_id):
            raise ValueError(f"invalid lab peer id: {role}")
        if title != lab_title(data, role):
            raise ValueError(f"lab channel title does not match provenance: {role}")
        if peer_id in seen_peer_ids:
            raise ValueError(f"duplicate lab peer id: {peer_id}")
        seen_peer_ids.add(peer_id)
    blocked = data.setdefault("blocked", {})
    if not isinstance(blocked, dict):
        raise ValueError("invalid blocked state")
    creating = data.setdefault("creating", {})
    if not isinstance(creating, dict):
        raise ValueError("invalid creating state")
    for role, pending in creating.items():
        if role not in CHANNEL_ROLES or not isinstance(pending, dict):
            raise ValueError(f"invalid creating role: {role}")
        if role in data["channels"]:
            raise ValueError(f"creating role already has a channel: {role}")
        if pending.get("title") != lab_title(data, role):
            raise ValueError(f"invalid creating title: {role}")
        if pending.get("state") not in {"dispatching", "ambiguous"}:
            raise ValueError(f"invalid creating state: {role}")
    if source_version == 2:
        data["manifest_version"] = MANIFEST_VERSION
        data["scenarios"] = {}
    allowed_scenarios = {spec.key for spec in required_scenarios()}
    for scenario_key, checkpoint in data["scenarios"].items():
        if scenario_key not in allowed_scenarios:
            raise ValueError(f"unknown scenario key: {scenario_key}")
        _validate_scenario_checkpoint_envelope(
            scenario_key,
            checkpoint,
            bind_fingerprint_to_scenario=True,
        )
    return data


def save_manifest(path: Path, manifest: dict) -> None:
    write_report(path, manifest)


def lab_title(manifest: dict, role: str) -> str:
    if role not in CHANNEL_ROLES:
        raise ValueError(f"unknown lab channel role: {role}")
    return f"{LAB_MARKER} {manifest['lab_id']} {role}"


def record_channel(manifest: dict, role: str, peer_id: int, title: str) -> None:
    if role not in CHANNEL_ROLES:
        raise ValueError(f"unknown lab channel role: {role}")
    if title != lab_title(manifest, role):
        raise PolicyError(f"channel title lacks exact lab provenance: {title!r}")
    manifest["channels"][role] = {"peer_id": peer_id, "title": title}


def lab_peer_ids(manifest: dict) -> set[int]:
    return {entry["peer_id"] for entry in manifest["channels"].values()}


def assert_lab_peer(manifest: dict, peer_id: int) -> None:
    if peer_id not in lab_peer_ids(manifest):
        raise PolicyError(f"peer {peer_id} is not a lab channel; refusing mutation")


def record_seed(manifest: dict, role: str, kind: str, message_ids: list[int]) -> None:
    manifest["seeded"].setdefault(role, {})[kind] = list(message_ids)


def record_blocked(manifest: dict, role: str, kind: str, error: str) -> None:
    manifest.setdefault("blocked", {}).setdefault(role, {})[kind] = error


def seeded_ids(manifest: dict, role: str) -> dict[str, list[int]]:
    return dict(manifest["seeded"].get(role, {}))


# --- Task 2: fixture matrix and payload generators ---

import hashlib
import struct
import zlib
from typing import Callable

from telethon.tl import types


def build_png(color: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        block = tag + data
        return struct.pack(">I", len(data)) + block + struct.pack(
            ">I", zlib.crc32(block)
        )

    width = height = 4
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(color) * width
    body = zlib.compress(row * height)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", body)
        + chunk(b"IEND", b"")
    )


def deterministic_bytes(seed: str, size: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < size:
        out.extend(hashlib.sha256(f"{seed}:{counter}".encode()).digest())
        counter += 1
    return bytes(out[:size])


@dataclass(frozen=True)
class ByteFixture:
    kind: str
    filename: str
    payload: Callable[[], bytes]
    attributes: Callable[[], list]
    force_document: bool
    reupload_fidelity: str  # "exact" | "reencoded"
    mime_type: str


BYTE_FIXTURES: dict[str, ByteFixture] = {
    "photo": ByteFixture(
        kind="photo",
        filename="lab-photo.jpg",
        payload=lambda: build_png((0, 0, 255)),
        attributes=lambda: [],
        force_document=False,
        reupload_fidelity="reencoded",
        mime_type="image/jpeg",
    ),
    "document": ByteFixture(
        kind="document",
        filename="lab-document.txt",
        payload=lambda: deterministic_bytes("document", 16_384),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-document.txt"),
        ],
        force_document=True,
        reupload_fidelity="exact",
        mime_type="text/plain",
    ),
    "audio": ByteFixture(
        kind="audio",
        filename="lab-audio.mp3",
        payload=lambda: deterministic_bytes("audio", 32_768),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-audio.mp3"),
            types.DocumentAttributeAudio(
                duration=3, title="Lab audio", performer="tgcli"
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="audio/mpeg",
    ),
    "voice": ByteFixture(
        kind="voice",
        filename="lab-voice.ogg",
        payload=lambda: deterministic_bytes("voice", 8_192),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-voice.ogg"),
            types.DocumentAttributeAudio(duration=2, voice=True),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="audio/ogg",
    ),
    "video": ByteFixture(
        kind="video",
        filename="lab-video.mp4",
        payload=lambda: deterministic_bytes("video", 1_600_000),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-video.mp4"),
            types.DocumentAttributeVideo(
                duration=2, w=64, h=64, supports_streaming=True
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="video/mp4",
    ),
    "video_note": ByteFixture(
        kind="video_note",
        filename="lab-note.mp4",
        payload=lambda: deterministic_bytes("video_note", 24_576),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-note.mp4"),
            types.DocumentAttributeVideo(
                duration=2, w=240, h=240, round_message=True
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="video/mp4",
    ),
    "animation": ByteFixture(
        kind="animation",
        filename="lab-animation.gif",
        payload=lambda: deterministic_bytes("animation", 20_480),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-animation.gif"),
            types.DocumentAttributeAnimated(),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="image/gif",
    ),
    "sticker": ByteFixture(
        kind="sticker",
        filename="lab-sticker.webp",
        payload=lambda: deterministic_bytes("sticker", 4_096),
        attributes=lambda: [
            types.DocumentAttributeFilename("lab-sticker.webp"),
            types.DocumentAttributeSticker(
                alt="🙂", stickerset=types.InputStickerSetEmpty()
            ),
        ],
        force_document=False,
        reupload_fidelity="exact",
        mime_type="image/webp",
    ),
}

NON_BYTE_LAB_KINDS = (
    "contact", "dice", "geo", "poll", "venue",
)

EXCLUDED_KINDS: dict[str, str] = {
    "game": "requires a bot-owned game",
    "giveaway": "requires a Premium boost purchase with real cost",
    "giveaway_results": "exists only after a finished giveaway",
    "geo_live": "native channel forward degrades live location to static geo",
    "invoice": "requires a payment-enabled bot",
    "paid_media_preview": "requires a monetization-enabled channel",
    "paid_media_revealed": "requires a monetization-enabled channel",
    "story": "excluded by ADR-0013 fidelity target; channel stories need boosts",
    "todo": "broadcast channels rejected InputMediaTodo with MediaInvalidError in R1",
}

COVERED_BY_R0 = frozenset({"text", "webpage", "service", "empty", "unsupported"})

ALBUM_COLORS = ((255, 0, 0), (0, 255, 0))


def preflight_fixture_tools() -> dict[str, str]:
    tools = {name: shutil.which(name) for name in ("ffmpeg", "cwebp")}
    missing = [name for name, path in tools.items() if path is None]
    if missing:
        raise ValueError(f"lab fixture tool unavailable: {', '.join(missing)}")
    encoders = subprocess.run(
        [tools["ffmpeg"], "-hide_banner", "-encoders"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    required = (
        "libmp3lame", "libopus", "libx264", "aac", "png", "mjpeg", "gif"
    )
    unavailable = [encoder for encoder in required if encoder not in encoders]
    if unavailable:
        raise ValueError(
            f"lab fixture encoder unavailable: {', '.join(unavailable)}"
        )
    return {name: path for name, path in tools.items() if path is not None}


def _run_fixture_command(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip().splitlines()[-1] if exc.stderr else "command failed"
        raise ValueError(f"lab fixture generation failed: {detail}") from exc


def materialize_fixture(
    kind: str,
    directory: Path,
    *,
    color: tuple[int, int, int] = (0, 0, 255),
) -> Path:
    fixture = BYTE_FIXTURES[kind]
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / fixture.filename
    if kind == "document":
        output.write_bytes(deterministic_bytes("document", 16_384))
        return output

    tools = preflight_fixture_tools()
    common = [
        tools["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
    ]
    if kind == "photo":
        hex_color = "".join(f"{component:02x}" for component in color)
        command = common + [
            "-f", "lavfi", "-i", f"color=c=0x{hex_color}:s=512x512:d=1",
            "-frames:v", "1", "-map_metadata", "-1", "-fflags", "+bitexact",
            "-flags:v", "+bitexact", "-threads", "1", "-c:v", "mjpeg",
            "-q:v", "2", str(output),
        ]
    elif kind == "audio":
        command = common + [
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
            "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k",
            "-id3v2_version", "0", "-write_xing", "0", str(output),
        ]
    elif kind == "voice":
        command = common + [
            "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=2",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
            "-ac", "1", "-c:a", "libopus", "-b:a", "24k", "-vbr", "off",
            "-application", "voip", "-frame_duration", "20", "-serial_offset", "137",
            str(output),
        ]
    elif kind in {"video", "video_note"}:
        settings = {
            "video": ("640x360", "30", "2", "0"),
            "video_note": ("240x240", "24", "2", "23"),
        }
        size, rate, duration, crf = settings[kind]
        inputs = [
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={duration}",
        ]
        audio = []
        if kind == "video":
            inputs += [
                "-f", "lavfi", "-i",
                "sine=frequency=330:sample_rate=48000:duration=2",
            ]
            audio = ["-c:a", "aac", "-b:a", "64k", "-shortest"]
        else:
            audio = ["-an"]
        command = common + inputs + [
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", crf,
            "-pix_fmt", "yuv420p", "-threads", "1", "-x264-params",
            "threads=1:lookahead_threads=1:sync-lookahead=0:force-cfr=1",
            "-movflags", "+faststart",
        ] + audio + [str(output)]
    elif kind == "animation":
        command = common + [
            "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=15:duration=1",
            "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
            "-an", "-c:v", "gif", "-threads", "1", str(output),
        ]
    elif kind == "sticker":
        png = directory / "lab-sticker.png"
        _run_fixture_command(common + [
            "-f", "lavfi", "-i", "color=c=0x3366cc:s=512x512:d=1",
            "-frames:v", "1", "-map_metadata", "-1", "-fflags", "+bitexact",
            str(png),
        ])
        _run_fixture_command([
            tools["cwebp"], "-quiet", "-lossless", "-m", "6", "-o", str(output), str(png),
        ])
        png.unlink()
        return output
    else:
        raise ValueError(f"unknown byte fixture: {kind}")
    _run_fixture_command(command)
    return output


def non_byte_media() -> dict[str, object]:
    def geo_point() -> types.InputGeoPoint:
        return types.InputGeoPoint(lat=59.93, long=30.31)

    return {
        "poll": types.InputMediaPoll(
            poll=types.Poll(
                id=0,
                hash=0,
                question=types.TextWithEntities(text="lab poll", entities=[]),
                answers=[
                    types.PollAnswer(
                        text=types.TextWithEntities(text="A", entities=[]),
                        option=b"0",
                    ),
                    types.PollAnswer(
                        text=types.TextWithEntities(text="B", entities=[]),
                        option=b"1",
                    ),
                ],
            )
        ),
        "contact": types.InputMediaContact(
            phone_number="+10000000000",
            first_name="Lab",
            last_name="Fixture",
            vcard="",
        ),
        "geo": types.InputMediaGeoPoint(geo_point=geo_point()),
        "venue": types.InputMediaVenue(
            geo_point=geo_point(),
            title="Lab venue",
            address="Lab address",
            provider="lab",
            venue_id="lab-1",
            venue_type="lab",
        ),
        "dice": types.InputMediaDice(emoticon="🎲"),
    }


def planned_kinds() -> list[str]:
    return ["text", *sorted(BYTE_FIXTURES), *NON_BYTE_LAB_KINDS, "album"]


def pending_kinds(manifest: dict, role: str) -> list[str]:
    done = set(manifest["seeded"].get(role, {}))
    return [kind for kind in planned_kinds() if kind not in done]


# --- Task 3: verdict and transport-fidelity comparison ---

from tgcli.mirror_probe import NON_BYTE_KINDS


def lab_verdict(report: dict, manifest: dict, role: str) -> dict:
    schema_ok = report.get("probe_version") == 2
    seeded = manifest["seeded"].get(role, {})
    planned = set(planned_kinds())
    blocked = dict(manifest.get("blocked", {}).get(role, {}))
    pending = sorted(planned - set(seeded) - set(blocked))
    expected = planned - {"album"} - set(blocked)
    observed = {row["kind"]: row for row in report["capabilities"]}
    missing = sorted(expected - set(observed))
    failing = sorted(
        kind
        for kind, row in observed.items()
        if kind in expected
        and not (
            row["telethon_bytes"] == "pass"
            or (
                kind in NON_BYTE_KINDS
                and all(sample["decode"] == "pass" for sample in row["samples"])
            )
        )
    )
    green = schema_ok and not blocked and not missing and not failing and not pending
    return {
        "verdict": "green" if green else "red",
        "schema": "pass" if schema_ok else "unsupported",
        "missing": missing,
        "failing": failing,
        "pending": pending,
        "blocked": blocked,
        "excluded": dict(EXCLUDED_KINDS),
        "covered_by_r0": sorted(COVERED_BY_R0),
    }


def _kind_shas(report: dict) -> dict[str, list[str]]:
    return {
        row["kind"]: sorted(
            sample["sha256"] for sample in row["samples"] if sample["sha256"]
        )
        for row in report["capabilities"]
    }


async def album_groups(tg, entity, *, limit: int) -> list[dict]:
    groups: dict[int, list] = {}
    async for message in tg.iter_messages(entity, limit=limit):
        grouped_id = getattr(message, "grouped_id", None)
        if grouped_id is not None:
            groups.setdefault(grouped_id, []).append(message)
    result = []
    ordered_groups = sorted(
        (messages for messages in groups.values() if len(messages) > 1),
        key=lambda messages: min(message.id for message in messages),
    )
    for messages in ordered_groups:
        samples = [
            await probe_message(tg, message)
            for message in sorted(messages, key=lambda message: message.id)
        ]
        result.append(
            {
                "count": len(samples),
                "sha256": [sample["sha256"] for sample in samples],
                "telethon_bytes": [sample["telethon_bytes"] for sample in samples],
            }
        )
    return result


def compare_transport(
    source_report: dict,
    dest_report: dict,
    *,
    transport: str,
    expected_kinds: set[str] | None = None,
) -> dict:
    if transport not in {"native", "reupload"}:
        raise ValueError(f"unknown transport: {transport}")
    schema_ok = (
        source_report.get("probe_version") == 2
        and dest_report.get("probe_version") == 2
    )
    source = {row["kind"]: row for row in source_report["capabilities"]}
    dest = {row["kind"]: row for row in dest_report["capabilities"]}
    source_shas = _kind_shas(source_report)
    dest_shas = _kind_shas(dest_report)

    rows = []
    kinds = (
        expected_kinds
        if expected_kinds is not None
        else (set(source) & set(BYTE_FIXTURES))
    )
    for kind in sorted(kinds):
        if kind == "album":
            source_groups = source_report.get("album_groups", [])
            dest_groups = dest_report.get("album_groups", [])

            def valid(groups):
                return bool(groups) and all(
                    group["count"] == len(group["sha256"])
                    == len(group["telethon_bytes"])
                    and all(state == "pass" for state in group["telethon_bytes"])
                    and None not in group["sha256"]
                    and len(set(group["sha256"])) == group["count"]
                    for group in groups
                )

            matched = (
                valid(source_groups)
                and valid(dest_groups)
                and source_groups == dest_groups
            )
            rows.append(
                {
                    "kind": kind,
                    "expectation": "grouped",
                    "result": "pass" if matched else "fail",
                }
            )
            continue
        expectation = "exact"
        if transport == "reupload":
            expectation = BYTE_FIXTURES.get(
                kind, BYTE_FIXTURES["document"]
            ).reupload_fidelity
        if kind not in source:
            rows.append({"kind": kind, "expectation": expectation, "result": "missing"})
            continue
        if kind not in dest:
            rows.append({"kind": kind, "expectation": expectation, "result": "missing"})
            continue
        if kind not in BYTE_FIXTURES:
            matched = (
                source[kind].get("sample_count") == dest[kind].get("sample_count")
                and all(
                    sample.get("decode") == "pass"
                    for sample in source[kind].get("samples", [])
                    + dest[kind].get("samples", [])
                )
            )
        elif expectation == "exact":
            matched = (
                source[kind].get("telethon_bytes") == "pass"
                and dest[kind].get("telethon_bytes") == "pass"
                and bool(source_shas[kind])
                and source_shas[kind] == dest_shas.get(kind)
            )
        else:
            matched = (
                source[kind].get("telethon_bytes") == "pass"
                and dest[kind].get("telethon_bytes") == "pass"
                and len(dest_shas.get(kind, [])) == len(source_shas[kind])
            )
        rows.append(
            {
                "kind": kind,
                "expectation": expectation,
                "result": "pass" if matched else "fail",
            }
        )
    green = schema_ok and rows and all(row["result"] == "pass" for row in rows)
    return {
        "transport": transport,
        "verdict": "green" if green else "red",
        "schema": "pass" if schema_ok else "unsupported",
        "rows": rows,
    }


# --- Task 4: channel provisioning and seeding engines ---

import os

from telethon.tl import functions

from tgcli.safety import append_audit, enforce_mutation_allowed


async def _audited_mutation(
    action, account_alias, details, mutation, *, ambiguous_errors=False
):
    enforce_mutation_allowed(readonly=False)
    operation_id = secrets.token_hex(12)
    append_audit(
        action,
        account_alias,
        {**details, "operation_id": operation_id, "phase": "attempt"},
    )
    try:
        result = await mutation()
    except BaseException as exc:
        status = (
            "ambiguous"
            if ambiguous_errors or isinstance(exc, asyncio.CancelledError)
            else "failed"
        )
        append_audit(
            action,
            account_alias,
            {
                **details,
                "operation_id": operation_id,
                "phase": "result",
                "status": status,
                "error": type(exc).__name__,
            },
        )
        raise
    append_audit(
        action,
        account_alias,
        {
            **details,
            "operation_id": operation_id,
            "phase": "result",
            "status": "confirmed",
        },
    )
    return result


def _random_id() -> int:
    return int.from_bytes(os.urandom(8), "little", signed=True)


def _sent_message_id(
    update,
    *,
    random_id: int | None = None,
    peer_id: int | None = None,
    marker: str | None = None,
) -> int:
    if random_id is not None:
        correlated = [
            item.id
            for item in getattr(update, "updates", ())
            if type(item).__name__ == "UpdateMessageID"
            and getattr(item, "random_id", None) == random_id
            and _is_positive_int(getattr(item, "id", None))
        ]
        if len(correlated) == 1:
            return correlated[0]
        messages = [
            item.message
            for item in getattr(update, "updates", ())
            if getattr(item, "message", None) is not None
            and getattr(item.message, "message", None) == marker
            and (
                peer_id is None
                or getattr(getattr(item.message, "peer_id", None), "channel_id", None)
                == peer_id
            )
        ]
        if len(messages) == 1 and _is_positive_int(getattr(messages[0], "id", None)):
            return messages[0].id
        raise ValueError("could not correlate sent message id to request")
    for item in getattr(update, "updates", ()):
        message = getattr(item, "message", None)
        if message is not None and hasattr(message, "id"):
            return message.id
    for item in getattr(update, "updates", ()):
        if type(item).__name__ == "UpdateMessageID":
            return item.id
    raise ValueError("could not extract sent message id from update")


async def _lab_entity(tg, manifest: dict, role: str):
    channel = manifest["channels"][role]
    assert_lab_peer(manifest, channel["peer_id"])
    entity = await tg.get_entity(types.PeerChannel(channel["peer_id"]))
    if (
        getattr(entity, "title", None) != channel["title"]
        or not getattr(entity, "creator", False)
        or not getattr(entity, "broadcast", False)
        or getattr(entity, "megagroup", False)
    ):
        raise PolicyError(f"{role}: peer is not the expected owned lab broadcast")
    return entity


async def _find_ambiguous_created_channel(tg, title: str):
    matches = []
    async for dialog in tg.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if (
            getattr(entity, "title", None) == title
            and getattr(entity, "creator", False)
            and getattr(entity, "broadcast", False)
            and not getattr(entity, "megagroup", False)
        ):
            matches.append(entity)
    if len(matches) > 1:
        raise PolicyError("ambiguous lab create matched multiple owned channels")
    return matches[0] if matches else None


async def create_lab_channels(tg, manifest, manifest_path, account_alias, note) -> dict:
    enforce_mutation_allowed(readonly=False)
    manifest.setdefault("creating", {})
    for role in CHANNEL_ROLES:
        if role in manifest["channels"]:
            if role == "protected_source":
                entity = await _lab_entity(tg, manifest, role)
                if (
                    not manifest["channels"][role].get("protection_enabled", False)
                    or not getattr(entity, "noforwards", False)
                ):
                    await _audited_mutation(
                        "mirror-lab-protect",
                        account_alias,
                        {"role": role},
                        lambda: tg(
                            functions.messages.ToggleNoForwardsRequest(
                                peer=entity,
                                enabled=True,
                            )
                        ),
                    )
                    manifest["channels"][role]["protection_enabled"] = True
                    save_manifest(manifest_path, manifest)
                    note(f"{role}: protection enabled")
                else:
                    note(f"{role}: already created, skipping")
            else:
                note(f"{role}: already created, skipping")
            continue
        title = lab_title(manifest, role)
        pending = manifest["creating"].get(role)
        if pending:
            channel = await _find_ambiguous_created_channel(tg, pending["title"])
            if channel is None:
                pending["state"] = "ambiguous"
                save_manifest(manifest_path, manifest)
                raise PolicyError(
                    f"{role}: ambiguous create is not yet visible; refusing duplicate"
                )
        else:
            manifest["creating"][role] = {"title": title, "state": "dispatching"}
            save_manifest(manifest_path, manifest)
            try:
                update = await _audited_mutation(
                    "mirror-lab-create",
                    account_alias,
                    {"role": role, "title": title},
                    lambda: tg(
                        functions.channels.CreateChannelRequest(
                            title=title,
                            about="tgcli R1 disposable lab channel",
                            broadcast=True,
                            megagroup=False,
                        )
                    ),
                    ambiguous_errors=True,
                )
            except BaseException:
                manifest["creating"][role]["state"] = "ambiguous"
                save_manifest(manifest_path, manifest)
                raise
            channel = update.chats[0]
        record_channel(manifest, role, channel.id, title)
        del manifest["creating"][role]
        save_manifest(manifest_path, manifest)
        if role == "protected_source":
            await _audited_mutation(
                "mirror-lab-protect",
                account_alias,
                {"role": role},
                lambda: tg(
                    functions.messages.ToggleNoForwardsRequest(
                        peer=channel, enabled=True
                    )
                ),
            )
            manifest["channels"][role]["protection_enabled"] = True
            save_manifest(manifest_path, manifest)
        note(f"{role}: created lab channel")
    return manifest


async def _seed_kind(tg, entity, kind: str, fixture_dir: Path) -> list[int]:
    if kind == "text":
        message = await tg.send_message(entity, "lab text fixture")
        return [message.id]
    if kind == "album":
        files = [
            materialize_fixture(
                "photo", fixture_dir / f"album-{index}", color=color
            )
            for index, color in enumerate(ALBUM_COLORS)
        ]
        messages = await tg.send_file(entity, files)
        return [message.id for message in messages]
    if kind in BYTE_FIXTURES:
        fixture = BYTE_FIXTURES[kind]
        path = materialize_fixture(kind, fixture_dir)
        message = await tg.send_file(
            entity,
            path,
            attributes=fixture.attributes(),
            force_document=fixture.force_document,
            mime_type=fixture.mime_type,
        )
        return [message.id]
    media = non_byte_media()[kind]
    update = await tg(
        functions.messages.SendMediaRequest(
            peer=entity, media=media, message="", random_id=_random_id()
        )
    )
    return [_sent_message_id(update)]


async def seed_sources(tg, manifest, manifest_path, account_alias, note) -> dict:
    results: dict[str, dict[str, str]] = {}
    with tempfile.TemporaryDirectory(prefix="tgcli-r1-fixtures-") as fixture_dir:
        fixture_path = Path(fixture_dir)
        for role in ("protected_source", "open_source"):
            entity = await _lab_entity(tg, manifest, role)
            results[role] = {}
            for kind in pending_kinds(manifest, role):
                try:
                    ids = await _audited_mutation(
                        "mirror-lab-seed",
                        account_alias,
                        {"role": role, "kind": kind},
                        lambda: _seed_kind(tg, entity, kind, fixture_path),
                    )
                except FloodWaitError:
                    raise
                except Exception as exc:
                    results[role][kind] = f"blocked:{type(exc).__name__}"
                    record_blocked(manifest, role, kind, type(exc).__name__)
                    save_manifest(manifest_path, manifest)
                    note(f"{role}: {kind} blocked by {type(exc).__name__}")
                    continue
                record_seed(manifest, role, kind, ids)
                save_manifest(manifest_path, manifest)
                results[role][kind] = "seeded"
                note(f"{role}: seeded {kind}")
    return results


# --- Task 5: copy transports ---

import shutil
from pathlib import Path

from telethon.errors import (
    ChannelInvalidError,
    ChannelPrivateError,
    ChatForwardsRestrictedError,
    FloodWaitError,
    MsgIdInvalidError,
)


async def copy_native(tg, manifest, account_alias, note) -> dict:
    open_entity = await _lab_entity(tg, manifest, "open_source")
    dest_entity = await _lab_entity(tg, manifest, "dest_native")
    protected_entity = await _lab_entity(tg, manifest, "protected_source")

    protected_seeds = seeded_ids(manifest, "protected_source")
    restricted_check = "skipped:no_protected_seed"
    if protected_seeds:
        first_ids = next(iter(protected_seeds.values()))
        try:
            await _audited_mutation(
                "mirror-lab-forward-restricted-check",
                account_alias,
                {"ids": len(first_ids)},
                lambda: tg(
                    functions.messages.ForwardMessagesRequest(
                        from_peer=protected_entity,
                        id=list(first_ids),
                        random_id=[_random_id() for _ in first_ids],
                        to_peer=dest_entity,
                        drop_author=True,
                    )
                ),
            )
            restricted_check = "unexpected_success"
        except ChatForwardsRestrictedError:
            restricted_check = "confirmed"
        note(f"protected forward check: {restricted_check}")

    results: dict[str, str] = {}
    for kind, ids in seeded_ids(manifest, "open_source").items():
        try:
            await _audited_mutation(
                "mirror-lab-copy-native",
                account_alias,
                {"kind": kind, "ids": len(ids)},
                lambda: tg(
                    functions.messages.ForwardMessagesRequest(
                        from_peer=open_entity,
                        id=list(ids),
                        random_id=[_random_id() for _ in ids],
                        to_peer=dest_entity,
                        drop_author=True,
                    )
                ),
            )
        except FloodWaitError:
            raise
        except Exception as exc:
            results[kind] = f"blocked:{type(exc).__name__}"
            note(f"native {kind}: blocked by {type(exc).__name__}")
            continue
        results[kind] = "forwarded"
        note(f"native {kind}: forwarded")
    return {
        "transport": "native",
        "restricted_check": restricted_check,
        "results": results,
    }


async def copy_reupload(tg, manifest, workdir: Path, account_alias, note) -> dict:
    source_entity = await _lab_entity(tg, manifest, "protected_source")
    dest_entity = await _lab_entity(tg, manifest, "dest_reupload")
    enforce_mutation_allowed(readonly=False)

    results: dict[str, str] = {}
    run_dir = Path(workdir) / "reupload"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        for kind, ids in seeded_ids(manifest, "protected_source").items():
            if kind not in BYTE_FIXTURES and kind != "album":
                results[kind] = "not_applicable"
                continue
            try:
                async def reupload_one():
                    messages = await tg.get_messages(source_entity, ids=list(ids))
                    paths = []
                    attributes = None
                    force_document = False
                    for message in messages:
                        target = run_dir / f"{kind}-{message.id}"
                        paths.append(Path(await tg.download_media(message, file=target)))
                    if kind in BYTE_FIXTURES:
                        document = getattr(messages[0], "document", None)
                        if document is not None:
                            attributes = list(document.attributes)
                            force_document = BYTE_FIXTURES[kind].force_document
                    if kind == "album":
                        return await tg.send_file(dest_entity, [str(p) for p in paths])
                    return await tg.send_file(
                        dest_entity,
                        str(paths[0]),
                        attributes=attributes,
                        force_document=force_document,
                    )

                await _audited_mutation(
                    "mirror-lab-copy-reupload",
                    account_alias,
                    {"kind": kind, "ids": len(ids)},
                    reupload_one,
                )
            except FloodWaitError:
                raise
            except Exception as exc:
                results[kind] = f"blocked:{type(exc).__name__}"
                note(f"reupload {kind}: blocked by {type(exc).__name__}")
                continue
            results[kind] = "copied"
            note(f"reupload {kind}: copied")
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    return {"transport": "reupload", "results": results}


# --- Task 6: teardown ---

async def teardown_lab(tg, manifest, manifest_path, account_alias, note) -> dict:
    removed = []
    unresolved = []
    for role, channel in list(manifest["channels"].items()):
        deleting = channel.get("delete_state") == "deleting"
        try:
            entity = await _lab_entity(tg, manifest, role)
        except (ChannelInvalidError, ChannelPrivateError):
            if not deleting:
                raise
            unresolved.append(role)
            note(f"{role}: delete remains ambiguous; manifest entry preserved")
            continue
        if not deleting:
            channel["delete_state"] = "deleting"
            save_manifest(manifest_path, manifest)
        if entity is not None:
            try:
                await _audited_mutation(
                    "mirror-lab-teardown",
                    account_alias,
                    {"role": role},
                    lambda: tg(
                        functions.channels.DeleteChannelRequest(channel=entity)
                    ),
                )
            except (ChannelInvalidError, ChannelPrivateError):
                unresolved.append(role)
                note(f"{role}: delete remains ambiguous; manifest entry preserved")
                continue
        removed.append(role)
        del manifest["channels"][role]
        save_manifest(manifest_path, manifest)
        note(f"{role}: deleted lab channel")
    return {"removed": removed, "unresolved": unresolved}
