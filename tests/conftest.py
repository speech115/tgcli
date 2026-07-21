from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


class FakeClient:
    """Duck-typed stand-in for TelegramClient used by command tests."""

    def __init__(
        self,
        dialogs=(),
        messages=(),
        entities=None,
        me=None,
        search_messages=None,
        message_total=None,
        participants=(),
        resolve_phone_result=None,
        contacts_result=None,
        contacts_search_result=None,
    ):
        self._dialogs = list(dialogs)
        self._messages = list(messages)
        self._entities = entities or {}
        self._me = me
        self._search_messages = search_messages or {}
        self._message_total = message_total
        self._participants = list(participants)
        self._resolve_phone_result = resolve_phone_result
        self._contacts_result = contacts_result
        self._contacts_search_result = contacts_search_result
        self.session = ns(takeout_id=None)
        self.iter_messages_calls = []
        self.iter_messages_reverse_calls = []
        self.get_messages_calls = []
        self.iter_participants_calls = []
        self.takeout_calls = []
        self.takeout_error = None
        self.iter_messages_error = None
        self.call_requests = []

    async def iter_dialogs(self, limit=None):
        for dialog in self._dialogs[:limit]:
            yield dialog

    async def iter_messages(
        self,
        entity,
        search=None,
        limit=None,
        reverse=False,
        min_id=0,
        offset_id=0,
        offset_date=None,
        reply_to=None,
        from_user=None,
    ):
        self.iter_messages_calls.append((entity, search, limit))
        self.iter_messages_reverse_calls.append(reverse)
        self.iter_messages_kwargs = {
            "min_id": min_id,
            "offset_id": offset_id,
            "offset_date": offset_date,
            "reply_to": reply_to,
            "from_user": from_user,
        }
        if self.iter_messages_error is not None:
            raise self.iter_messages_error
        messages = self._search_messages.get(search, self._messages)
        if from_user is not None:
            wanted = str(from_user).lstrip("@")
            messages = [
                message
                for message in messages
                if getattr(getattr(message, "sender", None), "username", None) == wanted
            ]
        messages = [message for message in messages if message.id > min_id]
        if offset_id:
            messages = [message for message in messages if message.id < offset_id]
        if offset_date is not None:
            messages = [
                message
                for message in messages
                if message.date and message.date < offset_date
            ]
        if reply_to is not None:
            messages = [
                message
                for message in messages
                if getattr(message, "topic", None) == reply_to
            ]
        if reverse:
            messages = list(reversed(messages))
        for message in messages[:limit]:
            yield message

    @asynccontextmanager
    async def takeout(self, **kwargs):
        self.takeout_calls.append(kwargs)
        if self.takeout_error is not None:
            raise self.takeout_error
        yield self

    async def iter_participants(self, entity, limit=None):
        self.iter_participants_calls.append((entity, limit))
        for participant in self._participants[:limit]:
            yield participant

    async def get_entity(self, key):
        if key not in self._entities:
            raise ValueError(f"no entity {key!r}")
        return self._entities[key]

    async def get_input_entity(self, key):
        return key

    async def get_me(self):
        return self._me

    async def get_messages(self, entity, ids=None, limit=None):
        self.get_messages_calls.append((entity, ids, limit))
        if limit == 0:
            return ns(total=self._message_total)
        if isinstance(ids, list):
            return [
                next(
                    (message for message in self._messages if message.id == item), None
                )
                for item in ids
            ]
        return next((message for message in self._messages if message.id == ids), None)

    async def __call__(self, request):
        from telethon.tl import functions

        self.call_requests.append(request)
        if isinstance(request, functions.contacts.ResolvePhoneRequest):
            if self._resolve_phone_result is None:
                raise AssertionError(
                    "FakeClient received ResolvePhoneRequest but no "
                    "resolve_phone_result was configured"
                )
            return self._resolve_phone_result
        if isinstance(request, functions.contacts.GetContactsRequest):
            if self._contacts_result is None:
                raise AssertionError(
                    "FakeClient received GetContactsRequest but no "
                    "contacts_result was configured"
                )
            return self._contacts_result
        if isinstance(request, functions.contacts.SearchRequest):
            if self._contacts_search_result is None:
                raise AssertionError(
                    "FakeClient received SearchRequest but no "
                    "contacts_search_result was configured"
                )
            return self._contacts_search_result
        raise AssertionError(f"FakeClient received unexpected raw request: {request!r}")


@pytest.fixture(autouse=True)
def state_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))


def make_session_fake(monkeypatch, fake_client):
    """Route tgcli.cli's session.client(...) to a FakeClient."""
    from tgcli import cli

    @asynccontextmanager
    async def fake_session(account, *, mutation_safe=False):
        fake_client.session_mutation_safe = mutation_safe
        yield fake_client

    monkeypatch.setattr(cli.session, "client", fake_session)


def ns(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)
