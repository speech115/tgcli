"""The loopback HTTP side of `tg setup`: one page, its state and its actions.

Only the person's own browser tab can drive it. The server listens on
127.0.0.1 alone, every path starts with a random token, the Host header must
name this server (a DNS-rebinding site cannot), and an action is a POST with
a JSON body from the page's own origin. Responses are never cached and never
send a Referer, so the token does not leave the tab.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tgcli.commands.setup import SetupFlow

LOOPBACK = "127.0.0.1"
MAX_HEAD = 16 * 1024
MAX_BODY = 16 * 1024
READ_TIMEOUT_S = 10.0
CLOSE_TIMEOUT_S = 2.0
_REASONS = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Content Too Large",
    415: "Unsupported Media Type",
    500: "Internal Server Error",
}
_OUTCOME_STATUS = {
    "ok": 200,
    "invalid": 400,
    "conflict": 409,
    "unknown": 404,
    "failed": 500,
}
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    csp: str | None = None
    final: bool = False


class _BadRequest(Exception):
    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


@functools.cache
def _template() -> str:
    return resources.files("tgcli").joinpath("setup_page.html").read_text("utf-8")


def choose_language(accept_language: str | None) -> str:
    """Russian unless the browser's first language is something else."""
    if not accept_language:
        return "ru"
    ranked = []
    for index, part in enumerate(accept_language.split(",")):
        tag, _, params = part.strip().partition(";")
        weight = re.search(r"q=([0-9.]+)", params)
        try:
            quality = float(weight.group(1)) if weight else 1.0
        except ValueError:
            quality = 0.0
        if tag and quality > 0:
            ranked.append((-quality, index, tag.strip().lower()))
    if not ranked:
        return "ru"
    return "ru" if min(ranked)[2].startswith("ru") else "en"


@dataclass
class Site:
    flow: SetupFlow
    token: str
    port: int = 0
    final_delivered: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def origin(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"

    @property
    def url(self) -> str:
        return f"{self.origin}/{self.token}/"

    async def respond(self, request: Request, peer: str | None) -> Response:
        if peer != LOOPBACK or request.headers.get("host") != f"{LOOPBACK}:{self.port}":
            return Response(403)
        route = self._route(request.path)
        if route is None:
            return Response(403)
        if route in ("", "state"):
            if request.method != "GET":
                return Response(405)
            return self._page(request) if route == "" else self._state(200)
        if request.method != "POST":
            return Response(405)
        origin = request.headers.get("origin")
        if origin is not None and origin != self.origin:
            return Response(403)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return Response(415)
        try:
            form = json.loads(request.body or b"{}")
        except ValueError:
            return Response(400)
        if not isinstance(form, dict):
            return Response(400)
        outcome = await self.flow.act(route, form)
        return self._state(_OUTCOME_STATUS[outcome])

    def _route(self, path: str) -> str | None:
        token, slash, route = path.removeprefix("/").partition("/")
        expected = self.token.encode("latin-1")
        if not slash or not secrets.compare_digest(token.encode("latin-1"), expected):
            return None
        return route

    def _state(self, status: int) -> Response:
        body = json.dumps(self.flow.view(), ensure_ascii=False).encode("utf-8")
        return Response(
            status,
            body,
            "application/json; charset=utf-8",
            final=self.flow.is_final(),
        )

    def _page(self, request: Request) -> Response:
        nonce = secrets.token_urlsafe(16)
        state = json.dumps(self.flow.view(), ensure_ascii=False)
        values = {
            "nonce": nonce,
            "lang": choose_language(request.headers.get("accept-language")),
            "state": state.replace("<", "\\u003c"),
        }
        html = _PLACEHOLDER.sub(lambda match: values[match.group(1)], _template())
        csp = (
            f"default-src 'none'; script-src 'nonce-{nonce}'; "
            f"style-src 'nonce-{nonce}'; img-src data:; connect-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        return Response(
            200,
            html.encode("utf-8"),
            "text/html; charset=utf-8",
            csp=csp,
            final=self.flow.is_final(),
        )


async def _read_request(reader: asyncio.StreamReader) -> Request:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError) as exc:
        raise _BadRequest(400) from exc
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise _BadRequest(400)
    method, target, _version = parts
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, colon, value = line.partition(":")
        if not colon:
            raise _BadRequest(400)
        headers[name.strip().lower()] = value.strip()
    length = headers.get("content-length", "0")
    if not length.isdigit():
        raise _BadRequest(400)
    if int(length) > MAX_BODY:
        raise _BadRequest(413)
    try:
        body = await reader.readexactly(int(length))
    except asyncio.IncompleteReadError as exc:
        raise _BadRequest(400) from exc
    return Request(method, target.partition("?")[0], headers, body)


def _encode(response: Response) -> bytes:
    lines = [
        f"HTTP/1.1 {response.status} {_REASONS[response.status]}",
        f"Content-Type: {response.content_type}",
        f"Content-Length: {len(response.body)}",
        "Cache-Control: no-store",
        "Referrer-Policy: no-referrer",
        "X-Content-Type-Options: nosniff",
        "X-Frame-Options: DENY",
        "Connection: close",
    ]
    if response.csp is not None:
        lines.append(f"Content-Security-Policy: {response.csp}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + response.body


async def _exchange(
    site: Site, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    peer = writer.get_extra_info("peername")
    try:
        request = await asyncio.wait_for(_read_request(reader), READ_TIMEOUT_S)
    except _BadRequest as exc:
        response = Response(exc.status)
    except (TimeoutError, ConnectionError):
        return
    else:
        response = await site.respond(request, peer[0] if peer else None)
    writer.write(_encode(response))
    try:
        await writer.drain()
    except ConnectionError:
        return
    if response.final:
        site.final_delivered.set()


@contextlib.asynccontextmanager
async def serve(flow: SetupFlow) -> AsyncIterator[Site]:
    """Serve `flow` on a free loopback port until the block exits.

    Idle keep-alive sockets a browser opens ahead of time are closed here:
    `wait_closed` would otherwise wait on them.
    """
    site = Site(flow, secrets.token_urlsafe(24))
    writers: set[asyncio.StreamWriter] = set()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        writers.add(writer)
        try:
            await _exchange(site, reader, writer)
        finally:
            writers.discard(writer)
            writer.close()

    server = await asyncio.start_server(handle, LOOPBACK, 0, limit=MAX_HEAD)
    site.port = server.sockets[0].getsockname()[1]
    try:
        yield site
    finally:
        server.close()
        for writer in list(writers):
            writer.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait_closed(), CLOSE_TIMEOUT_S)
