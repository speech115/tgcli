"""A FakeClient that `session.client` and `session.hold` can connect."""

from tests.conftest import FakeClient
from tgcli import session


class ConnectingClient(FakeClient):
    """FakeClient plus the connection surface `session.client` drives."""

    def __init__(self, *args, authorized=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.authorized = authorized
        self.connected = False
        self.connects = 0
        self._request_retries = 5
        self.flood_sleep_threshold = 30
        self.retries_seen = []

    async def connect(self):
        self.connected = True
        self.connects += 1

    def is_connected(self):
        return self.connected

    async def is_user_authorized(self):
        return self.authorized

    async def disconnect(self):
        self.connected = False

    async def _call(self, sender, request, *args, **kwargs):
        return None


def install(monkeypatch, client: ConnectingClient) -> list:
    made = []

    def make(path, account, *, mutation_safe=False):
        made.append(path)
        return client

    monkeypatch.setattr(session, "_make_client", make)
    return made
