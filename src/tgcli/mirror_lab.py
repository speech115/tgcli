from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tgcli.errors import PolicyError
from tgcli.mirror_probe import write_report

LAB_MARKER = "tgcli-r1-lab"
MANIFEST_VERSION = 1
CHANNEL_ROLES = (
    "protected_source",
    "open_source",
    "dest_native",
    "dest_reupload",
)


def new_manifest(account_user_id: int) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "account_user_id": account_user_id,
        "created_at": datetime.now(UTC).isoformat(),
        "channels": {},
        "seeded": {},
    }


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError("unsupported lab manifest")
    for key in ("account_user_id", "channels", "seeded"):
        if key not in data:
            raise ValueError(f"lab manifest missing {key!r}")
    return data


def save_manifest(path: Path, manifest: dict) -> None:
    write_report(path, manifest)


def record_channel(manifest: dict, role: str, peer_id: int, title: str) -> None:
    if role not in CHANNEL_ROLES:
        raise ValueError(f"unknown lab channel role: {role}")
    if not title.startswith(LAB_MARKER):
        raise PolicyError(f"channel title lacks lab marker: {title!r}")
    manifest["channels"][role] = {"peer_id": peer_id, "title": title}


def lab_peer_ids(manifest: dict) -> set[int]:
    return {entry["peer_id"] for entry in manifest["channels"].values()}


def assert_lab_peer(manifest: dict, peer_id: int) -> None:
    if peer_id not in lab_peer_ids(manifest):
        raise PolicyError(f"peer {peer_id} is not a lab channel; refusing mutation")


def record_seed(manifest: dict, role: str, kind: str, message_ids: list[int]) -> None:
    manifest["seeded"].setdefault(role, {})[kind] = list(message_ids)


def seeded_ids(manifest: dict, role: str) -> dict[str, list[int]]:
    return dict(manifest["seeded"].get(role, {}))
