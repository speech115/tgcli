from contextlib import asynccontextmanager
from types import SimpleNamespace


class FakeClient:
    """Duck-typed stand-in for TelegramClient used by command tests."""

    def __init__(self, dialogs=(), messages=(), entities=None):
        self._dialogs = list(dialogs)
        self._messages = list(messages)
        self._entities = entities or {}

    async def iter_dialogs(self, limit=None):
        for dialog in self._dialogs[:limit]:
            yield dialog

    async def iter_messages(self, entity, limit=None):
        for message in self._messages[:limit]:
            yield message

    async def get_entity(self, key):
        if key not in self._entities:
            raise ValueError(f"no entity {key!r}")
        return self._entities[key]


def make_session_fake(monkeypatch, fake_client):
    """Route tgcli.cli's session.client(...) to a FakeClient."""
    from tgcli import cli

    @asynccontextmanager
    async def fake_session(account):
        yield fake_client

    monkeypatch.setattr(cli.session, "client", fake_session)


def ns(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)
