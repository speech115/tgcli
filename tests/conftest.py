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
        search_messages=None,
        message_total=None,
        participants=(),
    ):
        self._dialogs = list(dialogs)
        self._messages = list(messages)
        self._entities = entities or {}
        self._search_messages = search_messages or {}
        self._message_total = message_total
        self._participants = list(participants)
        self.session = ns(takeout_id=None)
        self.iter_messages_calls = []
        self.iter_messages_reverse_calls = []
        self.get_messages_calls = []
        self.iter_participants_calls = []
        self.takeout_calls = []
        self.takeout_error = None
        self.iter_messages_error = None

    async def iter_dialogs(self, limit=None):
        for dialog in self._dialogs[:limit]:
            yield dialog

    async def iter_messages(self, entity, search=None, limit=None, reverse=False):
        self.iter_messages_calls.append((entity, search, limit))
        self.iter_messages_reverse_calls.append(reverse)
        if self.iter_messages_error is not None:
            raise self.iter_messages_error
        messages = self._search_messages.get(search, self._messages)
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

    async def get_messages(self, entity, ids=None, limit=None):
        self.get_messages_calls.append((entity, ids, limit))
        if limit == 0:
            return ns(total=self._message_total)
        return next((message for message in self._messages if message.id == ids), None)


@pytest.fixture(autouse=True)
def state_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))


def make_session_fake(monkeypatch, fake_client):
    """Route tgcli.cli's session.client(...) to a FakeClient."""
    from tgcli import cli

    @asynccontextmanager
    async def fake_session(account):
        yield fake_client

    monkeypatch.setattr(cli.session, "client", fake_session)


def ns(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)
