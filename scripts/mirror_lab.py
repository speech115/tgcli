#!/usr/bin/env python3
"""R1 controlled-lab probe: mutates only its own disposable lab channels."""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from telethon.errors import FloodWaitError

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
    verdict.add_argument("--transport", choices=("native", "reupload"), default=None)
    verdict.add_argument("--role", choices=mirror_lab.CHANNEL_ROLES, default=None)

    return parser.parse_args(argv)


def run_verdict(args) -> dict:
    source = json.loads(Path(args.source_report).read_text(encoding="utf-8"))
    if args.dest_report:
        if not args.transport:
            raise ValueError("--transport is required with --dest-report")
        dest = json.loads(Path(args.dest_report).read_text(encoding="utf-8"))
        manifest = mirror_lab.load_manifest(Path(args.manifest))
        role = "open_source" if args.transport == "native" else "protected_source"
        expected = set(mirror_lab.seeded_ids(manifest, role))
        if args.transport == "reupload":
            expected &= set(mirror_lab.BYTE_FIXTURES) | {"album"}
        return mirror_lab.compare_transport(
            source,
            dest,
            transport=args.transport,
            expected_kinds=expected,
        )
    if not args.role:
        raise ValueError("--role is required without --dest-report")
    manifest = mirror_lab.load_manifest(Path(args.manifest))
    return mirror_lab.lab_verdict(source, manifest, args.role)


async def run(args) -> dict:
    if args.phase in {"create", "seed", "copy-native", "copy-reupload", "teardown"}:
        mirror_lab.enforce_mutation_allowed(readonly=False)
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
                raise ValueError(f"manifest not found: {manifest_path}")
            manifest = mirror_lab.new_manifest(me.id)

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
            report["album_group_sizes"] = await mirror_lab.album_group_sizes(
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
