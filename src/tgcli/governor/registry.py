"""Which Telegram request type belongs to which paced class (ADR-0072).

Three different keys live here and conflating them is the mistake this module
exists to prevent:

* the **cooldown key** is the request type itself (``request_key``). A
  ``FLOOD_WAIT`` arms a record for exactly the type that drew it, so the gate
  works for every request tgcli can ever issue — including ones nobody
  enumerated, and whatever a ``tg run`` script sends.
* the **paced class** (``classify``) decides the *interval* between
  requests, because ADR-0072 decision 3 states its defaults per class rather
  than per type, and for a ``SHARED_SLOT`` class also the pace key.
* the **pace key** (``pace_key``) is the slot pacing counts in: the request
  type, except that a ``SHARED_SLOT`` class counts all its types together
  (ADR-0122). It never selects a cooldown.

An unlisted request type is therefore not a hole in the gate. It is a request
with no pre-emptive pacing, still fully covered by the cooldown. That asymmetry
is deliberate: an exhaustive type table would be stale the first time Telethon
adds a constructor, and staleness there must not silently disable the gate.
"""

from __future__ import annotations

from enum import StrEnum


class RequestClass(StrEnum):
    """The pacing classes ADR-0072 decision 3 and ADR-0122 give defaults for."""

    HISTORY = "history"
    BY_ID = "by_id"
    ENUMERATION = "enumeration"
    MEDIA = "media"
    SEND = "send"
    MUTATION = "mutation"
    METADATA = "metadata"
    RESOLVE_PHONE = "resolve_phone"


# ADR-0072 decision 3. `None` means no pre-emptive interval; the class is still
# gated on its cooldown. MEDIA is charged per file, not per request (see
# `pacing._charge`).
INTERVALS: dict[RequestClass, float | None] = {
    RequestClass.HISTORY: 3.0,
    # "10 s per 300 ids", and Telethon sends 100 ids per request.
    RequestClass.BY_ID: 10.0 / 3,
    RequestClass.ENUMERATION: 3.0,
    RequestClass.MEDIA: 3.0,
    # ADR-0122: 20 send requests a minute (an album or a multi-id forward is
    # one request), about the pace a clone held without a flood.
    RequestClass.SEND: 3.0,
    RequestClass.MUTATION: None,
    RequestClass.METADATA: None,
    RequestClass.RESOLVE_PHONE: 3.0,
}

# Classes paced from the first request, with no free allowance: Telegram
# punishes phone resolution hardest.
ALWAYS_PACED = frozenset({RequestClass.RESOLVE_PHONE})

# Classes whose types share one pacing slot: Telegram limits how many
# messages land, whichever method put them there (ADR-0122). Cooldowns stay
# per type either way.
SHARED_SLOT = frozenset({RequestClass.SEND})

# Keyed by "<namespace>.<TypeName>" so two namespaces that share a type name
# (channels.GetMessagesRequest vs messages.GetMessagesRequest, and likewise
# ReadHistoryRequest) stay distinct — they are distinct methods to Telegram.
_CLASSES: dict[str, RequestClass] = {
    # History reads: the family the incident hit.
    "messages.GetHistoryRequest": RequestClass.HISTORY,
    "messages.SearchRequest": RequestClass.HISTORY,
    "messages.SearchGlobalRequest": RequestClass.HISTORY,
    "messages.GetRepliesRequest": RequestClass.HISTORY,
    "updates.GetDifferenceRequest": RequestClass.HISTORY,
    "updates.GetChannelDifferenceRequest": RequestClass.HISTORY,
    # get_messages by id — its own interval, charged per request.
    "messages.GetMessagesRequest": RequestClass.BY_ID,
    "channels.GetMessagesRequest": RequestClass.BY_ID,
    # Dialog and participant enumeration.
    "messages.GetDialogsRequest": RequestClass.ENUMERATION,
    "messages.GetPeerDialogsRequest": RequestClass.ENUMERATION,
    "messages.GetAllDraftsRequest": RequestClass.ENUMERATION,
    "channels.GetParticipantsRequest": RequestClass.ENUMERATION,
    # Media transfer: bytes, not RPCs — its own documented error family.
    "upload.GetFileRequest": RequestClass.MEDIA,
    "upload.GetCdnFileRequest": RequestClass.MEDIA,
    "upload.SaveFilePartRequest": RequestClass.MEDIA,
    "upload.SaveBigFilePartRequest": RequestClass.MEDIA,
    # Sends: each one puts messages into a chat, so they share one pace.
    "messages.SendMessageRequest": RequestClass.SEND,
    "messages.SendMediaRequest": RequestClass.SEND,
    "messages.SendMultiMediaRequest": RequestClass.SEND,
    "messages.ForwardMessagesRequest": RequestClass.SEND,
    # Mutations: no interval, but flood_sleep_threshold=0 means a flood here
    # propagates rather than being silently slept off.
    "messages.EditMessageRequest": RequestClass.MUTATION,
    "messages.DeleteMessagesRequest": RequestClass.MUTATION,
    "messages.ReadHistoryRequest": RequestClass.MUTATION,
    "channels.DeleteMessagesRequest": RequestClass.MUTATION,
    "channels.ReadHistoryRequest": RequestClass.MUTATION,
    "channels.CreateChannelRequest": RequestClass.MUTATION,
    "messages.SaveDraftRequest": RequestClass.MUTATION,
    "messages.ToggleDialogPinRequest": RequestClass.MUTATION,
    # Metadata lookups: one or two requests, no iteration.
    "channels.GetFullChannelRequest": RequestClass.METADATA,
    "users.GetFullUserRequest": RequestClass.METADATA,
    "contacts.ResolveUsernameRequest": RequestClass.METADATA,
    "messages.GetCommonChatsRequest": RequestClass.METADATA,
    "updates.GetStateRequest": RequestClass.METADATA,
    # resolve(phone) keeps its own 3 s pace, now as an instance of the
    # general mechanism rather than a module of its own (ADR-0072 decision 3).
    "contacts.ResolvePhoneRequest": RequestClass.RESOLVE_PHONE,
}

# What an unlisted type gets: no pre-emptive pacing, full cooldown coverage.
UNLISTED = RequestClass.METADATA


def request_key(request: object) -> str:
    """The cooldown key: ``"<namespace>.<TypeName>"`` for any request object.

    Derived from the type rather than looked up, so a request Telethon adds
    tomorrow still gets its own record instead of sharing one with an
    unrelated method.
    """
    request_type = type(request)
    module = getattr(request_type, "__module__", "")
    namespace = module.rsplit(".", 1)[-1] if module else ""
    name = request_type.__name__
    return f"{namespace}.{name}" if namespace else name


def classify(request: object) -> RequestClass:
    """The paced class for a request, or ``UNLISTED`` when it has none."""
    return _CLASSES.get(request_key(request), UNLISTED)


def pace_key(request: object) -> str:
    """The pacing slot: the class for a ``SHARED_SLOT`` class, else the type."""
    request_class = classify(request)
    if request_class in SHARED_SLOT:
        return f"class:{request_class}"
    return request_key(request)
