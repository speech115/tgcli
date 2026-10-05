"""Which Telegram request type belongs to which paced class (ADR-0072).

Two different keys live here and conflating them is the mistake this module
exists to prevent:

* the **cooldown key** is the request type itself (``request_key``). A
  ``FLOOD_WAIT`` arms a record for exactly the type that drew it, so the gate
  works for every request tgcli can ever issue — including ones nobody
  enumerated, and including whatever an operator sends through ``tg api``.
* the **paced class** (``classify``) decides only the *interval* between
  requests, because ADR-0072 decision 3 states its defaults per class rather
  than per type.

An unlisted request type is therefore not a hole in the gate. It is a request
with no pre-emptive pacing, still fully covered by the cooldown. That asymmetry
is deliberate: an exhaustive type table would be stale the first time Telethon
adds a constructor, and staleness there must not silently disable the gate.
"""

from __future__ import annotations

from enum import StrEnum


class RequestClass(StrEnum):
    """The pacing classes ADR-0072 decision 3 gives defaults for."""

    HISTORY = "history"
    BY_ID = "by_id"
    ENUMERATION = "enumeration"
    MEDIA = "media"
    MUTATION = "mutation"
    METADATA = "metadata"
    RESOLVE_PHONE = "resolve_phone"


# ADR-0072 decision 3. `None` means no pre-emptive interval; the class is still
# gated on its cooldown. BY_ID and MEDIA are per-unit rather than per-request —
# see `BY_ID_BATCH` and the media transfer's per-file accounting.
INTERVALS: dict[RequestClass, float | None] = {
    RequestClass.HISTORY: 3.0,
    RequestClass.BY_ID: 10.0,
    RequestClass.ENUMERATION: 3.0,
    RequestClass.MEDIA: 3.0,
    RequestClass.MUTATION: None,
    RequestClass.METADATA: None,
    RequestClass.RESOLVE_PHONE: 3.0,
}

# Classes paced from the first request, with no free allowance: Telegram
# punishes phone resolution hardest.
ALWAYS_PACED = frozenset({RequestClass.RESOLVE_PHONE})

# "10 s per 300 ids" — the interval above is charged once per batch of this
# many ids, not once per id.
BY_ID_BATCH = 300

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
    # get_messages by id — its own interval, charged per BY_ID_BATCH ids.
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
    # Mutations: no interval, but flood_sleep_threshold=0 means a flood here
    # propagates rather than being silently slept off.
    "messages.SendMessageRequest": RequestClass.MUTATION,
    "messages.SendMediaRequest": RequestClass.MUTATION,
    "messages.SendMultiMediaRequest": RequestClass.MUTATION,
    "messages.EditMessageRequest": RequestClass.MUTATION,
    "messages.DeleteMessagesRequest": RequestClass.MUTATION,
    "messages.ForwardMessagesRequest": RequestClass.MUTATION,
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


def interval_for(request: object) -> float | None:
    """Seconds of minimum start-to-start spacing, or ``None`` when unpaced.

    Start-to-start is the whole point (ADR-0072 decision 3): the caller
    reserves this interval *before* dispatching, so a slow request does not
    add its own latency on top of the pace.
    """
    return INTERVALS[classify(request)]
