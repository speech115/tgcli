"""Finding a clone from what the operator typed.

Turning a `SOURCE` argument into a clone — or into the peer reference that
identifies one — is a question about *local state*, not about the command
surface, and every clone command asks it before it reaches Telegram. It lives
here so `commands/clone.py` stays the decisions and this stays the search.
"""

from __future__ import annotations

from telethon import utils as telethon_utils
from telethon.tl import types

from tgcli.clone import state
from tgcli.errors import PolicyError

# The peer class a recorded source id belongs to, by the kind clone stored.
# Anything else clone supports (broadcast, megagroup, forum) is a channel.
_SOURCE_PEERS = {
    "dialog": types.PeerUser,
    "basic": types.PeerChat,
}


def matches(s: state.CloneState, source: str | None) -> bool:
    """SOURCE is a kind-marked peer id, else a title substring.

    The digit test stays `isdigit()`, not `int()`: int() also accepts `+1`,
    `1_000`, and padded forms, which would steal titles from the substring
    path. Digit-shaped strings int() still rejects (superscripts) fall
    through to that path rather than crashing the listing."""
    if source is None:
        return True
    if not source.lstrip("-").isdigit():
        return source.casefold() in s.source_title.casefold()
    try:
        wanted = int(source)
    except ValueError:
        return source.casefold() in s.source_title.casefold()
    peer = _SOURCE_PEERS.get(s.source_kind, types.PeerChannel)
    return wanted == telethon_utils.get_peer_id(peer(s.source_peer_id))


def slot_ids() -> set[str]:
    """Every clone id with a state file on disk, readable or not."""
    directory = state.clones_dir()
    if not directory.exists():
        return set()
    found = {path.stem for path in directory.glob("*.db")}
    found.update(
        path.stem
        for path in directory.glob("*.json")
        if not path.name.startswith("account-")
    )
    return found


def loaded_states() -> list[state.CloneState]:
    """Every clone state that loads; unreadable slots are skipped, not raised."""
    loaded = []
    for clone_id in slot_ids():
        try:
            found = state.load(clone_id)
        except PolicyError:
            continue
        if found is not None:
            loaded.append(found)
    return loaded


def recorded_source_ref(account_user_id: int, source: str):
    """Peer ref for a SOURCE that names an already-initialized clone by title.

    A bare title is not something Telegram resolves. Telethon falls back to
    its session entity cache, finds the peer by name, and issues
    ``GetChannels`` with whatever access hash it cached — the server answers
    ``CHANNEL_PRIVATE`` for a channel the account reads fine, and the error
    blames the operator's access for a resolution failure (#171). A clone's
    own state already records the peer id under that title, so a title that
    names exactly one clone resolves locally and exactly.

    Returns None when the reference is a username, an invite link, or matches
    no clone at all; those keep the normal Telegram resolution path.
    """
    if telethon_utils.parse_username(source) != (None, False):
        return None
    found = [
        loaded
        for loaded in loaded_states()
        if loaded.account_user_id == account_user_id and matches(loaded, source)
    ]
    if not found:
        return None
    if len(found) > 1:
        raise PolicyError(
            f"clone source {source!r} matched {len(found)} clones; "
            "pass the source id or its username instead"
        )
    peer = _SOURCE_PEERS.get(found[0].source_kind, types.PeerChannel)
    return peer(found[0].source_peer_id)
