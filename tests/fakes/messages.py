"""Real Telethon messages for fakes, so tests see the fields Telegram always sets.

A ``SimpleNamespace`` message lacks ``peer_id``, ``date`` and ``fwd_from``, and
production code then grows branches only fakes take. These factories return
``telethon.tl.types.Message`` / ``MessageService`` with every field Telegram
guarantees: ``peer_id`` comes from the chat, ``date`` is fixed.
"""

from datetime import UTC, datetime

from telethon.tl import types

DATE = datetime(2026, 1, 2, tzinfo=UTC)


def peer(chat):
    """The ``Peer`` a message in ``chat`` carries as its ``peer_id``.

    ``chat`` is a ``Peer`` already, a bare channel id, or an entity: a
    ``User``, a legacy ``Chat``, or anything else with an ``id`` (a channel).
    """
    if isinstance(chat, (types.PeerUser, types.PeerChat, types.PeerChannel)):
        return chat
    if isinstance(chat, int):
        return types.PeerChannel(channel_id=chat)
    if isinstance(chat, types.User):
        return types.PeerUser(user_id=chat.id)
    if isinstance(chat, types.Chat):
        return types.PeerChat(chat_id=chat.id)
    return types.PeerChannel(channel_id=chat.id)


def message(message_id, chat, **fields):
    """A real ``types.Message`` with id ``message_id`` sent in ``chat``."""
    values = {"date": DATE, "message": f"message {message_id}"}
    values.update(fields)
    return types.Message(id=message_id, peer_id=peer(chat), **values)


def service(message_id, chat, action, **fields):
    """A real ``types.MessageService`` carrying ``action`` in ``chat``."""
    values = {"date": DATE}
    values.update(fields)
    return types.MessageService(
        id=message_id, peer_id=peer(chat), action=action, **values
    )


def document(*, size, mime_type="application/octet-stream", attributes=(), **fields):
    """A real ``types.Document``: ``message.file.size`` reads its ``size``."""
    values = {
        "id": 1,
        "access_hash": 1,
        "file_reference": b"",
        "date": DATE,
        "dc_id": 2,
    }
    values.update(fields)
    return types.Document(
        mime_type=mime_type, size=size, attributes=list(attributes), **values
    )
