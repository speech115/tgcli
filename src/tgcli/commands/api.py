"""Fail-closed helpers for raw read-only Telegram API calls."""

import base64
import binascii
import json
from inspect import isclass
from types import ModuleType

from telethon import utils
from telethon.tl import functions, types
from telethon.tl.tlobject import TLObject, TLRequest

from tgcli.errors import ConfigError, NotFoundError


READ_VERBS = ("get", "search", "check", "resolve")
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


def is_read_method(name: str) -> bool:
    """Return whether a TL method's final segment has an allowlisted read verb."""
    method = name.rsplit(".", maxsplit=1)[-1]
    return method.startswith(READ_VERBS)


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
        return request_type(**{
            key: await _convert_value(
                client, value, _field_annotation(request_type, key)
            )
            for key, value in params.items()
        })
    except TypeError as exc:
        raise ConfigError(f"invalid raw API parameters: {exc}") from exc


async def call(client, name: str, params_json: str) -> dict:
    """Invoke a safe raw request and return the documented JSON envelope."""
    result = await client(await build_request(client, name, params_json))
    return {"method": name, "result": _sanitize_result(result.to_dict())}


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
        entity = await client.get_input_entity(value)
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
        return {
            key: await _convert_value(client, item)
            for key, item in value.items()
        }
    if constructor == "bytes":
        if set(value) != {"_", "base64"} or not isinstance(value["base64"], str):
            raise ConfigError("raw API bytes must use {'_': 'bytes', 'base64': '...'}")
        try:
            return base64.b64decode(value["base64"], validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ConfigError("raw API bytes must contain valid base64") from exc
    if not isinstance(constructor, str) or not constructor.startswith("Input"):
        raise ConfigError(f"raw API constructor is not allowed: {constructor!r}")
    constructor_type = getattr(types, constructor, None)
    if not isclass(constructor_type) or not issubclass(constructor_type, TLObject):
        raise ConfigError(f"raw API constructor is not allowed: {constructor!r}")
    try:
        return constructor_type(**{
            key: await _convert_value(
                client, item, _field_annotation(constructor_type, key)
            )
            for key, item in value.items()
            if key != "_"
        })
    except TypeError as exc:
        raise ConfigError(f"invalid raw API constructor {constructor!r}: {exc}") from exc
