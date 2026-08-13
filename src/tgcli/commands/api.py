"""Fail-closed helpers for raw read-only Telegram API calls."""

import base64
import binascii
import json
from inspect import isclass
from types import ModuleType
from typing import get_args

from telethon import utils
from telethon.tl import functions, types
from telethon.tl.tlobject import TLObject, TLRequest

from tgcli import chatref
from tgcli.errors import ConfigError, NotFoundError

READ_METHOD_ALLOWLIST = frozenset(
    {
        "channels.getAdminLog",
        "channels.getAdminedPublicChannels",
        "channels.getChannels",
        "channels.getFullChannel",
        "channels.getMessages",
        "channels.getParticipant",
        "channels.getParticipants",
        "contacts.getContacts",
        "contacts.resolvePhone",
        "contacts.resolveUsername",
        "contacts.search",
        "messages.getCommonChats",
        "messages.getDialogs",
        "messages.getDiscussionMessage",
        "messages.getForumTopics",
        "messages.getFullChat",
        "messages.getHistory",
        "messages.getMessageReactionsList",
        "messages.getMessages",
        "messages.getMessagesReactions",
        "messages.getPeerDialogs",
        "messages.getReplies",
        "messages.getSavedDialogs",
        "messages.getSavedHistory",
        "messages.getSearchCounters",
        "messages.getUnreadMentions",
        "messages.getUnreadReactions",
        "messages.search",
        "messages.searchGlobal",
        "photos.getUserPhotos",
        "stats.getBroadcastStats",
        "stats.getMegagroupStats",
        "stats.getMessagePublicForwards",
        "stats.getMessageStats",
        "stories.getStoriesArchive",
        "stories.getStoriesByID",
        "stories.getPeerStories",
        "stories.getStoryViewsList",
        "upload.getFile",
        "users.getFullUser",
        "users.getUsers",
    }
)
SENSITIVE_KEY_TOKENS = (
    "accesshash",
    "apihash",
    "authkey",
    "password",
    "secret",
    "srpb",
    "securerandom",
    "tmppassword",
)
HARD_DENYLIST = frozenset(
    {
        "account.deleteAccount",
        "auth.logOut",
        "auth.resetAuthorizations",
        "account.resetAuthorization",
    }
)
# ADR-0092: the write path mirrors the read path's ADR-0010 wholesale
# auth.*/account.* exclusion instead of naming only these four methods.
# HARD_DENYLIST is a strict subset kept as an explicit, named permanent
# denylist even if this wholesale rule were ever narrowed.
WRITE_NAMESPACE_DENYLIST = frozenset({"account", "auth"})
# Irreversible writes the delete*/reset*/leave*/block*/edit*Admin*/edit*Banned*
# prefix rule cannot see: both are one-way conversions with no undo.
IRREVERSIBLE_METHODS = frozenset(
    {
        "channels.convertToGigagroup",
        "messages.migrateChat",
    }
)
# The one non-Input argument an allowlisted read method needs: the abstract
# filter of channels.getParticipants. Taken from the pinned Telethon union so
# the converter stays a peer/filter builder, not a general TL constructor.
PARTICIPANTS_FILTER_TYPES = frozenset(get_args(types.TypeChannelParticipantsFilter))
# editAdmin / editBanned have no Input* form for their rights objects.
RIGHTS_TYPES = frozenset({types.ChatAdminRights, types.ChatBannedRights})
# Raw-write params that identify what a write touched. Audit records carry
# these (sanitized) so audit.jsonl can answer "what did this write touch?"
AUDIT_TARGET_KEYS = (
    "channel",
    "chat",
    "chat_id",
    "id",
    "participant",
    "peer",
    # editAdmin / editChatAdmin / deleteChatUser name their target here and
    # nowhere else: without it the audit records the room, never the person.
    "user_id",
)


def is_read_method(name: str) -> bool:
    """Return whether a reviewed raw API method is safe in phase 2."""
    return name in READ_METHOD_ALLOWLIST


def is_hard_denied(name: str) -> bool:
    return name in HARD_DENYLIST


def is_namespace_denied(name: str) -> bool:
    """Return whether a canonical write method's namespace is wholesale-excluded."""
    return name.split(".", 1)[0] in WRITE_NAMESPACE_DENYLIST


def requires_confirmation(name: str) -> bool:
    if name in IRREVERSIBLE_METHODS:
        return True
    method = name.rsplit(".", 1)[-1].casefold()
    return method.startswith(("delete", "reset", "leave", "block")) or (
        method.startswith("edit") and ("admin" in method or "banned" in method)
    )


def audit_details(name: str, params_json: str | None) -> dict:
    """Return the audit record for a raw write: the method and what it touched.

    ADR-0010/ADR-0011: the journal stays metadata-only, so only the target
    identifiers of `AUDIT_TARGET_KEYS` are recorded — never message bodies —
    and they run through the same sensitive-key filter as RPC results. Params
    that are not a JSON object leave the record as method-only rather than
    failing the write; validation belongs to `build_request`.
    """
    details = {"method": name}
    if not isinstance(params_json, str):
        return details
    try:
        params = json.loads(params_json)
    except ValueError:
        return details
    if not isinstance(params, dict):
        return details
    target = {
        key: _sanitize_result(params[key]) for key in AUDIT_TARGET_KEYS if key in params
    }
    if target:
        details["target"] = target
    return details


def _resolve_method(name: str):
    parts = name.split(".")
    if len(parts) != 2 or not all(parts):
        raise NotFoundError(f"raw API method not found: {name!r}")
    namespace, method = parts
    module = getattr(functions, namespace, None)
    request_type = getattr(module, f"{method[:1].upper()}{method[1:]}Request", None)
    if (
        not isinstance(module, ModuleType)
        or not isclass(request_type)
        or not issubclass(request_type, TLRequest)
    ):
        raise NotFoundError(f"raw API method not found: {name!r}")
    return request_type


def canonical_method(name: str) -> str:
    """Return the dispatched request's canonical `Namespace.method` identity.

    Policy checks key on this form so no case variant can resolve to the same
    Telethon request yet reach a different denylist/confirm branch. Fail-closed:
    unresolvable methods raise NotFoundError.
    """
    request_type = _resolve_method(name)
    namespace = name.split(".", 1)[0]
    stem = request_type.__name__[: -len("Request")]
    return f"{namespace}.{stem[:1].lower()}{stem[1:]}"


def try_canonical_method(name: str) -> str | None:
    """Canonicalize `name`, or return None when it cannot be resolved."""
    try:
        return canonical_method(name)
    except NotFoundError:
        return None


async def build_request(client, name: str, params_json: str):
    """Build a Telethon request from a JSON object without dynamic imports."""
    request_type = _resolve_method(name)
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as exc:
        raise ConfigError("raw API params must be valid JSON") from exc
    except TypeError as exc:
        raise ConfigError("raw API params must be a JSON object") from exc
    if not isinstance(params, dict):
        raise ConfigError("raw API params must be a JSON object")
    try:
        return request_type(
            **{
                key: await _convert_value(
                    client, value, _field_annotation(request_type, key)
                )
                for key, value in params.items()
            }
        )
    except TypeError as exc:
        raise ConfigError(f"invalid raw API parameters: {exc}") from exc


async def call(client, name: str, params_json: str) -> dict:
    """Invoke a safe raw request and return the documented JSON envelope."""
    if name == "contacts.resolvePhone":
        from tgcli.resolve_phone import enforce_resolve_phone_cooldown

        enforce_resolve_phone_cooldown()
    result = await client(await build_request(client, name, params_json))
    return {"method": name, "result": _serialize_rpc_result(result)}


def _serialize_rpc_result(result):
    """TLObjects expose to_dict(); Bool/int/None RPCs return bare JSON scalars."""
    to_dict = getattr(result, "to_dict", None)
    if callable(to_dict):
        return _sanitize_result(to_dict())
    return _sanitize_result(result)


def _sanitize_result(value):
    if isinstance(value, list):
        return [_sanitize_result(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _sanitize_result(item)
            for key, item in value.items()
            if not _is_sensitive_key(key)
        }
    return value


def _is_sensitive_key(key) -> bool:
    normalized = ""
    if isinstance(key, str):
        normalized = "".join(char for char in key.casefold() if char.isalnum())
    return any(token in normalized for token in SENSITIVE_KEY_TOKENS)


def _field_annotation(constructor_type, field: str):
    return getattr(constructor_type.__init__, "__annotations__", {}).get(field)


def _is_peer_field(annotation) -> bool:
    return annotation is not None and "TypeInput" in str(annotation)


def _is_peer_alias(value) -> bool:
    return value.startswith("@") or value.lstrip("-").isdigit()


async def _convert_value(client, value, annotation=None):
    if _is_peer_field(annotation) and isinstance(value, str) and _is_peer_alias(value):
        if "TypeInputUser" in str(annotation) and value == "@self":
            return types.InputUserSelf()
        try:
            entity = await client.get_input_entity(chatref.parse(value))
        except ValueError:
            raise NotFoundError(f"dialog not found: {value!r}") from None
        annotation_text = str(annotation)
        if "TypeInputUser" in annotation_text:
            return utils.get_input_user(entity)
        if "TypeInputChannel" in annotation_text:
            return utils.get_input_channel(entity)
        return entity
    if isinstance(value, list):
        return [await _convert_value(client, item, annotation) for item in value]
    if not isinstance(value, dict):
        return value
    constructor = value.get("_")
    if constructor is None:
        return {key: await _convert_value(client, item) for key, item in value.items()}
    if constructor == "bytes":
        if set(value) != {"_", "base64"} or not isinstance(value["base64"], str):
            raise ConfigError("raw API bytes must use {'_': 'bytes', 'base64': '...'}")
        try:
            return base64.b64decode(value["base64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ConfigError("raw API bytes must contain valid base64") from exc
    if not isinstance(constructor, str):
        raise ConfigError(f"raw API constructor is not allowed: {constructor!r}")
    constructor_type = getattr(types, constructor, None)
    if not isclass(constructor_type) or not issubclass(constructor_type, TLObject):
        raise ConfigError(f"raw API constructor is not allowed: {constructor!r}")
    if (
        not constructor.startswith("Input")
        and constructor_type not in PARTICIPANTS_FILTER_TYPES
        and constructor_type not in RIGHTS_TYPES
    ):
        raise ConfigError(f"raw API constructor is not allowed: {constructor!r}")
    try:
        # Dynamic TL construction: field types come from Telethon annotations at
        # runtime. Pyright cannot narrow the recursive converter's union per key
        # (rights objects especially have many Optional[bool] fields).
        return constructor_type(
            **{  # type: ignore[arg-type]
                key: await _convert_value(
                    client, item, _field_annotation(constructor_type, key)
                )
                for key, item in value.items()
                if key != "_"
            }
        )
    except TypeError as exc:
        raise ConfigError(
            f"invalid raw API constructor {constructor!r}: {exc}"
        ) from exc
