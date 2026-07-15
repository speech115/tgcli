"""tg clone — copy a broadcast channel into a user-owned channel (ADR-0017).

Read window first: `list_clones` reports the state of every clone without
touching the network. Later tasks add init/sync on top of the same state files.
"""

from tgcli.clone import state


def _entry(s: state.CloneState) -> dict:
    return {
        "clone_id": s.clone_id,
        "source": {"id": s.source_peer_id, "title": s.source_title},
        "destination_id": s.destination_peer_id,
        "cursor": s.cursor,
        "copied": len(s.id_map),
        "cooldown_until": s.retry_not_before,
        "created_at": s.created_at,
        "last_synced_at": s.last_synced_at,
    }


def _matches(s: state.CloneState, source: str | None) -> bool:
    if source is None:
        return True
    if source.lstrip("-").isdigit():
        return s.source_peer_id == int(source)
    return source.casefold() in s.source_title.casefold()


def list_clones(source: str | None = None) -> dict:
    directory = state.clones_dir()
    entries = []
    if directory.exists():
        for path in directory.glob("*.json"):
            loaded = state.load(path.stem)
            if loaded is not None and _matches(loaded, source):
                entries.append(_entry(loaded))
    entries.sort(key=lambda entry: entry["created_at"])
    return {"clones": entries}


def status_rows(data: dict) -> list[tuple]:
    return [
        (
            clone["source"]["id"],
            clone["source"]["title"],
            clone["destination_id"],
            clone["cursor"],
            clone["copied"],
            clone["last_synced_at"],
        )
        for clone in data["clones"]
    ]
