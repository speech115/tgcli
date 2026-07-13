#!/usr/bin/env python3
"""R1 controlled-lab probe: mutates only its own disposable lab channels."""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

import telethon
from telethon.errors import FloodWaitError
from telethon.tl import alltlobjects

from tgcli import config, session
from tgcli.errors import ConfigError, NotFoundError, PolicyError
from tgcli import mirror_lab
from tgcli.mirror_probe import probe_chat, write_report


def note(message: str) -> None:
    print(message, file=sys.stderr)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", default=None)
    sub = parser.add_subparsers(dest="phase", required=True)

    for phase in ("create", "seed", "copy-native", "copy-reupload", "teardown"):
        p = sub.add_parser(phase)
        p.add_argument("--manifest", required=True)
        if phase.startswith("copy-"):
            p.add_argument("--output", required=True)

    probe = sub.add_parser("probe")
    probe.add_argument("--manifest", required=True)
    probe.add_argument("--role", required=True, choices=mirror_lab.CHANNEL_ROLES)
    probe.add_argument("--limit", type=int, default=200)
    probe.add_argument("--output", required=True)

    verdict = sub.add_parser("verdict")
    verdict.add_argument("--manifest", required=True)
    verdict.add_argument("--source-report", required=True)
    verdict.add_argument("--dest-report", default=None)
    verdict.add_argument("--copy-report", default=None)
    verdict.add_argument("--transport", choices=("native", "reupload"), default=None)
    verdict.add_argument("--role", choices=mirror_lab.CHANNEL_ROLES, default=None)

    expanded = sub.add_parser("expanded-provision-canary")
    expanded.add_argument("--manifest", required=True)
    expanded.add_argument(
        "--scenario",
        required=True,
        choices=tuple(spec.key for spec in mirror_lab.required_scenarios()),
    )
    comments = sub.add_parser("expanded-comments-canary")
    comments.add_argument("--manifest", required=True)
    comments.add_argument(
        "--scenario",
        required=True,
        choices=tuple(
            spec.key
            for spec in mirror_lab.required_scenarios()
            if spec.discussion_kind == "plain"
        ),
    )
    forum = sub.add_parser("expanded-forum-canary")
    forum.add_argument("--manifest", required=True)
    forum.add_argument(
        "--scenario",
        required=True,
        choices=tuple(
            spec.key
            for spec in mirror_lab.required_scenarios()
            if spec.source_family == "forum" and spec.discussion_kind == "none"
        ),
    )
    supergroup = sub.add_parser("expanded-supergroup-canary")
    supergroup.add_argument("--manifest", required=True)
    supergroup.add_argument(
        "--scenario",
        required=True,
        choices=tuple(
            spec.key
            for spec in mirror_lab.required_scenarios()
            if spec.source_family == "supergroup"
            and spec.discussion_kind == "none"
        ),
    )
    cleanup = sub.add_parser("expanded-cleanup")
    cleanup.add_argument("--manifest", required=True)
    cleanup.add_argument(
        "--scenario",
        required=True,
        choices=tuple(spec.key for spec in mirror_lab.required_scenarios()),
    )

    return parser.parse_args(argv)


def _sha256_paths(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def live_scenario_fingerprint(account, me, scenario_key: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    config_digest = hashlib.sha256(
        json.dumps(
            {"account_alias": account.alias, "scenario_key": scenario_key},
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return mirror_lab.new_compatibility_fingerprint(
        scenario_key,
        fixture_schema_version=1,
        lab_code_digest=_sha256_paths((root / "src/tgcli/mirror_lab.py",)),
        mirror_code_digest=_sha256_paths(
            (root / "src/tgcli/mirror_probe.py", root / "CONTEXT.md")
        ),
        telethon_version=telethon.__version__,
        telegram_schema_layer=alltlobjects.LAYER,
        account_role_binding={
            "operator": {"alias": account.alias, "user_id": me.id},
        },
        config_digest=config_digest,
    )


def run_verdict(args) -> dict:
    source = json.loads(Path(args.source_report).read_text(encoding="utf-8"))
    if args.dest_report:
        if not args.transport:
            raise ValueError("--transport is required with --dest-report")
        if not args.copy_report:
            raise ValueError("--copy-report is required with --dest-report")
        dest = json.loads(Path(args.dest_report).read_text(encoding="utf-8"))
        copy = json.loads(Path(args.copy_report).read_text(encoding="utf-8"))
        manifest = mirror_lab.load_manifest(Path(args.manifest))
        role = "open_source" if args.transport == "native" else "protected_source"
        expected = set(mirror_lab.planned_kinds())
        if args.transport == "reupload":
            expected &= set(mirror_lab.BYTE_FIXTURES) | {"album"}
        result = mirror_lab.compare_transport(
            source,
            dest,
            transport=args.transport,
            expected_kinds=expected,
        )
        expected_status = "forwarded" if args.transport == "native" else "copied"
        copy_gate = (
            copy.get("transport") == args.transport
            and all(copy.get("results", {}).get(kind) == expected_status for kind in expected)
            and (
                args.transport != "native"
                or copy.get("restricted_check") == "confirmed"
            )
        )
        result["copy_gate"] = "pass" if copy_gate else "fail"
        if not copy_gate:
            result["verdict"] = "red"
        return result
    if not args.role:
        raise ValueError("--role is required without --dest-report")
    manifest = mirror_lab.load_manifest(Path(args.manifest))
    return mirror_lab.lab_verdict(source, manifest, args.role)


async def run(args) -> dict:
    if args.phase in {
        "create",
        "seed",
        "copy-native",
        "copy-reupload",
        "teardown",
        "expanded-provision-canary",
        "expanded-comments-canary",
        "expanded-forum-canary",
        "expanded-supergroup-canary",
        "expanded-cleanup",
    }:
        mirror_lab.enforce_mutation_allowed(readonly=False)
    if args.phase == "expanded-provision-canary":
        blocker = mirror_lab.scenario_live_blocker(args.scenario)
        if blocker is not None:
            evidence = ",".join(blocker["evidence"])
            raise PolicyError(f"{blocker['reason_code']}: {evidence}")
    if args.phase == "seed":
        mirror_lab.preflight_fixture_tools()
    account = config.resolve_account(config.load_config(), args.account)
    manifest_path = Path(args.manifest)
    async with session.client(account) as tg:
        me = await tg.get_me()
        if manifest_path.exists():
            manifest = mirror_lab.load_manifest(manifest_path)
            if manifest["account_user_id"] != me.id:
                raise ValueError("lab manifest belongs to another account")
        else:
            if args.phase != "create":
                if args.phase not in {
                    "expanded-provision-canary",
                    "expanded-comments-canary",
                    "expanded-forum-canary",
                    "expanded-supergroup-canary",
                }:
                    raise ValueError(f"manifest not found: {manifest_path}")
            manifest = mirror_lab.new_manifest(me.id)

        if args.phase == "expanded-cleanup":
            checkpoint = manifest["scenarios"].get(args.scenario)
            if checkpoint is None:
                raise ValueError(f"scenario checkpoint not found: {args.scenario}")
            fingerprint = checkpoint["compatibility_fingerprint"]
            operator = fingerprint["account_role_binding"].get("operator")
            if operator != {"alias": account.alias, "user_id": me.id}:
                raise PolicyError("cleanup account does not match checkpoint operator")

            def persist(value):
                manifest["scenarios"][args.scenario] = value
                mirror_lab.save_manifest(manifest_path, manifest)

            cleaned = await mirror_lab.teardown_scenario_peers(
                tg,
                checkpoint,
                fingerprint,
                lab_id=manifest["lab_id"],
                account_alias=account.alias,
                persist=persist,
            )
            cleanup_complete = (
                not cleaned["created_peers"]
                and not cleaned["cleanup_obligations"]
            )
            if not cleanup_complete:
                raise PolicyError("expanded cleanup is incomplete")
            operations_confirmed = bool(cleaned["outbound_operations"]) and all(
                operation["state"] == "confirmed"
                for operation in cleaned["outbound_operations"].values()
            )
            if operations_confirmed:
                manifest["scenarios"].pop(args.scenario, None)
                mirror_lab.save_manifest(manifest_path, manifest)
            return {
                "phase": "expanded-cleanup",
                "scenario": args.scenario,
                "cleanup": "green",
                "checkpoint_retained": not operations_confirmed,
            }

        if args.phase in {
            "expanded-provision-canary",
            "expanded-comments-canary",
            "expanded-forum-canary",
            "expanded-supergroup-canary",
        }:
            spec = mirror_lab.select_scenarios(args.scenario)[0]
            if spec.source_family == "basic":
                raise ValueError(
                    "basic scenario requires a configured dedicated lab-peer account"
                )
            fingerprint = live_scenario_fingerprint(account, me, args.scenario)
            checkpoint = manifest["scenarios"].get(args.scenario)
            if checkpoint is None:
                checkpoint = mirror_lab.new_scenario_checkpoint(
                    args.scenario, fingerprint
                )
                checkpoint["phase"] = "create"
                manifest["scenarios"][args.scenario] = checkpoint
                mirror_lab.save_manifest(manifest_path, manifest)
            else:
                canary_operations = checkpoint.get("verdicts", {}).get(
                    "canary_operations", {}
                )
                if args.phase in {
                    "expanded-comments-canary",
                    "expanded-forum-canary",
                    "expanded-supergroup-canary",
                } and canary_operations:
                    raise PolicyError(
                        "interrupted content canary is cleanup-only; "
                        "run expanded-cleanup"
                    )
                if not mirror_lab.compare_compatibility_fingerprints(
                    fingerprint, checkpoint["compatibility_fingerprint"]
                )["compatible"]:
                    raise ValueError(
                        "existing scenario checkpoint fingerprint is stale"
                    )

            def persist(value):
                manifest["scenarios"][args.scenario] = value
                mirror_lab.save_manifest(manifest_path, manifest)

            provisioned = None
            succeeded = False
            try:
                provisioned = await mirror_lab.provision_scenario_live(
                    tg,
                    checkpoint,
                    fingerprint,
                    lab_id=manifest["lab_id"],
                    account_alias=account.alias,
                    persist=persist,
                )
                if args.phase in {
                    "expanded-comments-canary",
                    "expanded-forum-canary",
                    "expanded-supergroup-canary",
                }:
                    def record(operation_key, state):
                        current = deepcopy(manifest["scenarios"][args.scenario])
                        operations = current["verdicts"].setdefault(
                            "canary_operations", {}
                        )
                        previous = operations.get(operation_key)
                        previous_state = (
                            previous.get("state")
                            if isinstance(previous, dict)
                            else None
                        )
                        allowed = {
                            None: {"prepared"},
                            "prepared": {"dispatched"},
                            "dispatched": {"confirmed", "ambiguous"},
                        }
                        if state not in allowed.get(previous_state, set()):
                            raise ValueError(
                                f"invalid canary operation transition: {operation_key}"
                            )
                        operations[operation_key] = {"state": state}
                        persist(current)

                    if args.phase == "expanded-comments-canary":
                        verifier = mirror_lab.verify_channel_comment_thread_live
                        result_field = "comment_threads"
                    elif args.phase == "expanded-forum-canary":
                        verifier = mirror_lab.verify_forum_topics_live
                        result_field = "forum_peers"
                    else:
                        verifier = mirror_lab.verify_supergroup_reply_chain_live
                        result_field = "supergroup_peers"
                    results = []
                    for side in ("source", "destination"):
                        results.append(
                            await verifier(
                                tg,
                                manifest["scenarios"][args.scenario],
                                fingerprint,
                                lab_id=manifest["lab_id"],
                                account_alias=account.alias,
                                side=side,
                                record=record,
                            )
                        )
                    succeeded = True
                    return {
                        "phase": args.phase,
                        "scenario": args.scenario,
                        result_field: [result["side"] for result in results],
                        "cleanup": "green",
                    }
                succeeded = True
                return {
                    "phase": args.phase,
                    "scenario": args.scenario,
                    "provisioned_roles": sorted(provisioned["created_peers"]),
                    "intent_count": len(provisioned["outbound_operations"]),
                    "cleanup": "green",
                }
            finally:
                latest = manifest["scenarios"].get(args.scenario)
                if latest is not None:
                    cleaned = await mirror_lab.teardown_scenario_peers(
                        tg,
                        latest,
                        fingerprint,
                        lab_id=manifest["lab_id"],
                        account_alias=account.alias,
                        persist=persist,
                    )
                    cleanup_complete = (
                        not cleaned["created_peers"]
                        and not cleaned["cleanup_obligations"]
                    )
                    operations_confirmed = bool(cleaned["outbound_operations"]) and all(
                        operation["state"] == "confirmed"
                        for operation in cleaned["outbound_operations"].values()
                    )
                    if cleanup_complete and operations_confirmed and succeeded:
                        manifest["scenarios"].pop(args.scenario, None)
                        mirror_lab.save_manifest(manifest_path, manifest)
                    elif provisioned is not None and not cleanup_complete:
                        raise PolicyError("expanded canary cleanup is incomplete")

        if args.phase == "create":
            await mirror_lab.create_lab_channels(
                tg, manifest, manifest_path, account.alias, note
            )
            return {"phase": "create", "channels": sorted(manifest["channels"])}
        if args.phase == "seed":
            results = await mirror_lab.seed_sources(
                tg, manifest, manifest_path, account.alias, note
            )
            return {"phase": "seed", "results": results}
        if args.phase == "probe":
            peer_id = manifest["channels"][args.role]["peer_id"]
            report = await probe_chat(
                tg, str(peer_id), me.id, role="lab", limit=args.limit
            )
            entity = await tg.get_entity(peer_id)
            report["album_groups"] = await mirror_lab.album_groups(
                tg, entity, limit=args.limit
            )
            return report
        if args.phase == "copy-native":
            return await mirror_lab.copy_native(tg, manifest, account.alias, note)
        if args.phase == "copy-reupload":
            with tempfile.TemporaryDirectory(prefix="tgcli-r1-") as workdir:
                return await mirror_lab.copy_reupload(
                    tg, manifest, Path(workdir), account.alias, note
                )
        if args.phase == "teardown":
            return await mirror_lab.teardown_lab(
                tg, manifest, manifest_path, account.alias, note
            )
        raise ValueError(f"unknown phase: {args.phase}")


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.phase == "verdict":
            result = run_verdict(args)
        else:
            result = asyncio.run(run(args))
        if getattr(args, "output", None):
            write_report(Path(args.output), result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except PolicyError as exc:
        print(f"lab: policy blocked: {exc}", file=sys.stderr)
        return 2
    except ConfigError as exc:
        print(f"lab: {exc}", file=sys.stderr)
        return 3
    except (NotFoundError, ValueError, FileNotFoundError, KeyError) as exc:
        print(f"lab: invalid input: {exc}", file=sys.stderr)
        return 4
    except FloodWaitError as exc:
        print(f"lab: FLOOD_WAIT retry_after={exc.seconds}", file=sys.stderr)
        return 5
    except Exception as exc:
        print(f"lab: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
