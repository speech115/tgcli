import pytest

from tgcli.commands.api import build_request, call
from tgcli.errors import ConfigError, NotFoundError
from telethon.tl import functions, types


class FakeClient:
    async def get_input_entity(self, value):
        raise AssertionError(f"unexpected peer lookup: {value}")


@pytest.mark.asyncio
async def test_build_request_resolves_two_segment_telethon_function():
    request = await build_request(
        FakeClient(), "users.getFullUser", '{"id": {"_": "InputUserSelf"}}'
    )

    assert request.__class__.__name__ == "GetFullUserRequest"
    assert request.id.__class__.__name__ == "InputUserSelf"


@pytest.mark.asyncio
async def test_call_serializes_tl_result_in_raw_api_envelope():
    class Result:
        def to_dict(self):
            return {"_": "UserFull", "id": 42}

    class Client(FakeClient):
        async def __call__(self, request):
            assert request.__class__.__name__ == "GetFullUserRequest"
            return Result()

    assert await call(
        Client(), "users.getFullUser", '{"id": {"_": "InputUserSelf"}}'
    ) == {
        "method": "users.getFullUser",
        "result": {"_": "UserFull", "id": 42},
    }


@pytest.mark.asyncio
async def test_call_serializes_bool_rpc_result_in_raw_api_envelope():
    """account.updateStatus and similar RPCs return a bare bool, not a TLObject."""

    class Client(FakeClient):
        async def __call__(self, request):
            assert request.__class__.__name__ == "UpdateStatusRequest"
            return True

    assert await call(Client(), "account.updateStatus", '{"offline": false}') == {
        "method": "account.updateStatus",
        "result": True,
    }


@pytest.mark.asyncio
async def test_build_request_resolves_alias_only_for_peer_typed_field():
    class Client:
        def __init__(self):
            self.lookups = []

        async def get_input_entity(self, value):
            self.lookups.append(value)
            return types.InputPeerSelf()

    client = Client()
    request = await build_request(
        client,
        "messages.getHistory",
        '{"peer": "@example", "offset_id": 0, "offset_date": null, "add_offset": 0, "limit": 1, "max_id": 0, "min_id": 0, "hash": 0}',
    )

    assert request.peer.__class__.__name__ == "InputPeerSelf"
    assert client.lookups == ["@example"]


@pytest.mark.asyncio
async def test_build_request_coerces_resolved_peer_to_requested_input_type():
    class Client:
        async def get_input_entity(self, value):
            return types.InputPeerSelf()

    request = await build_request(Client(), "users.getFullUser", '{"id": "@self"}')

    assert request.id.__class__.__name__ == "InputUserSelf"


class StrictPeerClient:
    """Telethon reads a bare digit string as a phone number, never as a dialog
    id, so a fake that accepts raw strings proves nothing (AGENTS.md)."""

    def __init__(self):
        self.lookups = []

    async def get_input_entity(self, value):
        if isinstance(value, str):
            raise ValueError(f'Cannot find any entity corresponding to "{value}"')
        self.lookups.append(value)
        return types.InputPeerChannel(channel_id=3890108644, access_hash=7)


@pytest.mark.asyncio
async def test_build_request_resolves_numeric_peer_alias_as_a_dialog_id():
    client = StrictPeerClient()

    request = await build_request(
        client, "channels.getFullChannel", '{"channel": "-1003890108644"}'
    )

    assert client.lookups == [-1003890108644]
    assert request.channel.__class__.__name__ == "InputChannel"


@pytest.mark.asyncio
async def test_build_request_reports_unresolvable_peer_alias_as_not_found():
    class Client:
        async def get_input_entity(self, value):
            raise ValueError(f'Cannot find any entity corresponding to "{value}"')

    with pytest.raises(NotFoundError, match="dialog not found"):
        await build_request(
            Client(), "channels.getFullChannel", '{"channel": "-1003890108644"}'
        )


@pytest.mark.asyncio
async def test_build_request_reports_digit_shaped_non_integer_alias_as_not_found():
    with pytest.raises(NotFoundError, match="dialog not found"):
        await build_request(
            FakeClient(), "channels.getFullChannel", '{"channel": "--123"}'
        )


@pytest.mark.asyncio
async def test_build_request_does_not_resolve_alias_in_non_peer_field():
    request = await build_request(
        FakeClient(),
        "contacts.search",
        '{"q": "@not-a-peer", "limit": 1}',
    )

    assert request.q == "@not-a-peer"


@pytest.mark.asyncio
async def test_build_request_rejects_malformed_params_json():
    with pytest.raises(ConfigError, match="valid JSON"):
        await build_request(FakeClient(), "users.getFullUser", "{")


@pytest.mark.asyncio
async def test_build_request_rejects_unknown_or_malformed_method():
    with pytest.raises(NotFoundError):
        await build_request(FakeClient(), "users.notARealMethod", "{}")
    with pytest.raises(NotFoundError):
        await build_request(FakeClient(), "users.getFullUser.extra", "{}")


@pytest.mark.asyncio
async def test_build_request_rejects_non_request_function_attribute(monkeypatch):
    monkeypatch.setattr(functions.users, "GetMetadataRequest", object(), raising=False)

    with pytest.raises(NotFoundError):
        await build_request(FakeClient(), "users.getMetadata", "{}")


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [None, 42, "[]"])
async def test_build_request_rejects_non_object_params(params):
    with pytest.raises(ConfigError, match="JSON object"):
        await build_request(FakeClient(), "users.getFullUser", params)


@pytest.mark.asyncio
async def test_build_request_decodes_explicit_base64_bytes_marker():
    request = await build_request(
        FakeClient(),
        "messages.getBotCallbackAnswer",
        '{"peer": {"_": "InputPeerSelf"}, "msg_id": 1, "game": false, "data": {"_": "bytes", "base64": "aGVsbG8="}}',
    )

    assert request.data == b"hello"


@pytest.mark.asyncio
async def test_build_request_rejects_unapproved_constructor_names():
    with pytest.raises(ConfigError, match="constructor is not allowed"):
        await build_request(
            FakeClient(),
            "users.getFullUser",
            '{"id": {"_": "os.system"}}',
        )


@pytest.mark.asyncio
async def test_call_never_returns_sensitive_account_password_values():
    class Result:
        def to_dict(self):
            return {
                "id": 1,
                "access_hash": 2,
                "accessHash": 3,
                "apiHash": "secret",
                "authKey": "secret",
                "current_algo": {"salt1": "public", "SRP_B": "secret"},
                "srpB": "secret",
                "password": "secret",
                "hint": "public",
                "new_secure_random": "secret",
                "nested": {
                    "auth_key": "secret",
                    "tmp_password": "secret",
                    "ok": True,
                },
            }

    class Client(FakeClient):
        async def __call__(self, request):
            return Result()

    data = await call(Client(), "users.getFullUser", '{"id": {"_": "InputUserSelf"}}')

    assert data["result"] == {
        "id": 1,
        "current_algo": {"salt1": "public"},
        "hint": "public",
        "nested": {"ok": True},
    }
