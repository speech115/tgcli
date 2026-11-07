"""A fixed channel for the `tg clone sync` benchmark, and the run that measures it.

Each scenario is a source built the same way every time, synced through the
real CLI (`tgcli.cli.main`) against a fake Telegram client. A run reports,
per sync invocation ("phase"):

- ``requests``: every call into the Telegram client, by raw request type or
  client method. ``iter_messages`` counts the history pages Telethon would
  fetch for what the run read (100 messages a page). Deterministic, so the
  benchmark test holds it equal to the baseline.
- ``completeness``: what the fake destination actually received, never what
  clone state claims: expected source messages, those with a copy, those
  copied more than once, and those missing.
- ``state_saves``: clone state writes, the local cost a sync pays per batch.
- ``sync``: the counters `tg clone sync --json` reported.
- ``seconds``: wall time of the invocation. Machine-dependent, so it is
  reported and only held to a loose ceiling.

`scripts/bench-clone.py` prints the report and rewrites the baseline;
`tests/test_clone_benchmark.py` runs it in the gate.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from telethon.tl import functions, types

from tests.fakes.clone import SAMPLE, CloneCommentsClient, anchor, message
from tgcli.clone import state

BASELINE = Path(__file__).with_name("clone_bench_baseline.json")
PAGE = 100  # messages per GetHistoryRequest page, Telethon's iter_messages size

SOURCE_CHANNEL = 123
SOURCE_GROUP = 55

SENDS = (
    functions.messages.ForwardMessagesRequest,
    functions.messages.SendMessageRequest,
    functions.messages.SendMediaRequest,
    functions.messages.SendMultiMediaRequest,
)
_SOURCE = re.compile(r"source (post|comment) #(\d+)")


def _photo(photo_id: int):
    return types.MessageMediaPhoto(
        photo=types.Photo(
            id=photo_id,
            access_hash=photo_id * 7,
            file_reference=b"ref",
            date=None,
            sizes=[],
            dc_id=2,
        )
    )


def _protected_photo(photo_id: int):
    return types.MessageMediaPhoto(photo=types.PhotoEmpty(id=photo_id))


@dataclass
class Source:
    """A generated channel and its discussion group, oldest first."""

    posts: list = field(default_factory=list)
    group: list = field(default_factory=list)
    expected_posts: set[int] = field(default_factory=set)
    expected_comments: set[int] = field(default_factory=set)
    group_last_id: int = 0

    def add_posts(self, first: int, count: int, *, protected: bool) -> None:
        """Append posts ``first``..``first+count-1`` with a fixed mix of kinds.

        Every tenth id is a service message; ids ending in 4-6 form a
        three-photo album; an id ending in 3 or 8 replies to the post two
        before it; every seventh id carries a single photo. Each non-service
        post gets its auto-forwarded anchor in the group, and every fourth
        one a two-comment thread: a comment on the anchor and a reply to it.
        """
        for post_id in range(first, first + count):
            if post_id % 10 == 0:
                self.posts.append(
                    message(
                        post_id,
                        chat=SOURCE_CHANNEL,
                        action=types.MessageActionChatEditTitle(title=f"t{post_id}"),
                    )
                )
                continue
            fields: dict = {"message": f"source post #{post_id}"}
            media = _protected_photo if protected else _photo
            if post_id % 10 in (4, 5, 6):
                fields["grouped_id"] = 10_000 + post_id // 10
                fields["media"] = media(post_id)
            elif post_id % 7 == 0:
                fields["media"] = media(post_id)
            if post_id % 10 in (3, 8) and post_id > 2:
                fields["reply_to"] = types.MessageReplyHeader(
                    reply_to_msg_id=post_id - 2
                )
            self.posts.append(message(post_id, chat=SOURCE_CHANNEL, **fields))
            self.expected_posts.add(post_id)
            self._group_thread(post_id)

    def _group_thread(self, post_id: int) -> None:
        self.group_last_id += 1
        anchor_id = self.group_last_id
        self.group.append(
            anchor(
                anchor_id, post_id, source_channel_id=SOURCE_CHANNEL, chat=SOURCE_GROUP
            )
        )
        if post_id % 4:
            return
        self.group_last_id += 1
        top = self.group_last_id
        self.group.append(
            message(
                top,
                chat=SOURCE_GROUP,
                message=f"source comment #{top}",
                reply_to=types.MessageReplyHeader(reply_to_msg_id=anchor_id),
            )
        )
        self.group_last_id += 1
        nested = self.group_last_id
        self.group.append(
            message(
                nested,
                chat=SOURCE_GROUP,
                message=f"source comment #{nested}",
                reply_to=types.MessageReplyHeader(
                    reply_to_msg_id=top, reply_to_top_id=anchor_id
                ),
            )
        )
        self.expected_comments |= {top, nested}


class BenchClient(CloneCommentsClient):
    """The comments fake, counting every call and recording every copy.

    ``copies`` maps each source message, as ("post"|"comment", id), to how
    many destination messages carry it. Telegram auto-forwards each new
    destination post into the destination group, so a channel copy gets its
    destination anchor at once.
    """

    def __init__(self, source: Source, *, protected: bool):
        super().__init__(source.posts, source.group)
        self.source.noforwards = protected
        self.calls: Counter[str] = Counter()
        self.copies: Counter[tuple[str, int]] = Counter()
        self.state_saves = 0

    def _count(self, name: str) -> None:
        self.calls[name] += 1

    async def get_entity(self, ref):
        self._count("get_entity")
        return await super().get_entity(ref)

    async def get_messages(self, entity, limit=None, ids=None):
        self._count("get_messages")
        return await super().get_messages(entity, limit=limit, ids=ids)

    async def iter_messages(self, entity, *, min_id=0, reverse=False):
        read = 0
        async for item in super().iter_messages(entity, min_id=min_id, reverse=reverse):
            if read % PAGE == 0:
                self._count("GetHistoryRequest (iter_messages)")
            read += 1
            yield item
        if read == 0:
            self._count("GetHistoryRequest (iter_messages)")

    async def iter_participants(self, entity, limit=None):
        self._count("GetParticipantsRequest (iter_participants)")
        async for item in super().iter_participants(entity, limit=limit):
            yield item

    async def download_media(self, message, file=None):
        self._count("download_media")
        return await super().download_media(message, file=file)

    async def upload_file(self, path):
        self._count("upload_file")
        return await super().upload_file(path)

    async def __call__(self, request):
        self._count(type(request).__name__)
        response = await super().__call__(request)
        if isinstance(request, SENDS):
            peer = getattr(request, "peer", None) or getattr(request, "to_peer", None)
            assigned = {
                update.random_id: update.id
                for update in response.updates
                if isinstance(update, types.UpdateMessageID)
            }
            for source in _sources(request):
                self.copies[source] += 1
            if peer is not self.destination_group:
                for destination_id in assigned.values():
                    self.group_last_id += 1
                    self.anchor_ids[destination_id] = self.group_last_id
        return response


def _sources(request) -> list[tuple[str, int]]:
    """The source messages a send request carries, as ("post"|"comment", id)."""
    if isinstance(request, functions.messages.ForwardMessagesRequest):
        kind = (
            "comment"
            if getattr(request.from_peer, "channel_id", None) == SOURCE_GROUP
            else "post"
        )
        return [(kind, item) for item in request.id]
    if isinstance(request, functions.messages.SendMultiMediaRequest):
        bodies = [item.message for item in request.multi_media]
    else:
        bodies = [request.message]
    found = []
    for body in bodies:
        match = _SOURCE.search(body or "")
        assert match is not None, f"a send names no source message: {body!r}"
        found.append((match.group(1), int(match.group(2))))
    return found


@dataclass(frozen=True)
class Phase:
    """One `tg clone sync` invocation: what is new in the source before it."""

    name: str
    new_posts: int


@dataclass(frozen=True)
class Scenario:
    name: str
    protected: bool
    comments: bool
    phases: tuple[Phase, ...]


SCENARIOS = (
    # An open channel with comments: posts forward, comments interleave in
    # windows (ADR-0051), then a sync with nothing new, then a small catch-up.
    Scenario(
        "open-with-comments",
        protected=False,
        comments=True,
        phases=(
            Phase("initial", 240),
            Phase("idle", 0),
            Phase("incremental", 12),
        ),
    ),
    # A protected channel without comments: every post is downloaded and
    # reuploaded, albums as one SendMultiMedia.
    Scenario(
        "protected-reupload",
        protected=True,
        comments=False,
        phases=(Phase("initial", 120),),
    ),
)


def _seed(scenario: Scenario) -> None:
    clone_state = state.CloneState.new(
        account_user_id=42, source_peer_id=SOURCE_CHANNEL, source_title="Source channel"
    )
    clone_state.destination_peer_id = 999
    if scenario.comments:
        clone_state.comments = "enabled"
        clone_state.discussion_source_peer_id = SOURCE_GROUP
        clone_state.discussion_destination_peer_id = 888
        clone_state.discussion_linked = True
    state.save(clone_state)


@contextlib.contextmanager
def _isolated(client: BenchClient) -> Iterator[None]:
    """A fresh config and state dir, and session.client routed to ``client``."""
    from tgcli import session
    from tgcli.governor import pacing

    saved_env = dict(os.environ)
    saved_client = session.client
    saved_save = state.save

    def counting_save(clone_state: state.CloneState) -> None:
        client.state_saves += 1
        saved_save(clone_state)

    @contextlib.asynccontextmanager
    async def fake_session(account, *, mutation_safe=False, role=None, govern=True):
        yield client

    with tempfile.TemporaryDirectory(prefix="tgcli-bench-") as scratch:
        for name in list(os.environ):
            if name.startswith("TGCLI_"):
                del os.environ[name]
        config = Path(scratch) / "config.toml"
        config.write_text(SAMPLE)
        os.environ["TGCLI_CONFIG"] = str(config)
        os.environ["TGCLI_STATE_DIR"] = str(Path(scratch) / "state")
        session.client = fake_session  # type: ignore[assignment]
        state.save = counting_save
        try:
            yield
        finally:
            state.save = saved_save
            session.client = saved_client
            os.environ.clear()
            os.environ.update(saved_env)
            pacing.reset_runtime()


def _sync(clock: Callable[[], float]) -> tuple[int, dict, float]:
    from tgcli.cli import main
    from tgcli.governor import pacing

    pacing.reset_runtime()
    out = io.StringIO()
    started = clock()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = main(["clone", "sync", "@source", "--json"])
    seconds = clock() - started
    return code, json.loads(out.getvalue()), seconds


SYNC_FIELDS = (
    "copied",
    "forwarded",
    "reuploaded",
    "snapshots",
    "reply_flattened",
    "skipped_service",
    "skipped_autoforward",
    "cursor",
    "discussion_cursor",
    "more",
)


def run(scenario: Scenario, *, clock: Callable[[], float] = time.perf_counter):
    """Sync ``scenario`` phase by phase; one report row per phase."""
    source = Source()
    client = BenchClient(source, protected=scenario.protected)
    if not scenario.comments:
        client.comments = []
    rows = []
    next_id = 1
    with _isolated(client):
        _seed(scenario)
        for phase in scenario.phases:
            group_before = len(source.group)
            source.add_posts(next_id, phase.new_posts, protected=scenario.protected)
            next_id += phase.new_posts
            client.messages[:] = source.posts
            if scenario.comments:
                client.comments.extend(source.group[group_before:])
            calls_before = Counter(client.calls)
            saves_before = client.state_saves
            code, document, seconds = _sync(clock)
            requests = client.calls - calls_before
            expected = {("post", item) for item in source.expected_posts}
            if scenario.comments:
                expected |= {("comment", item) for item in source.expected_comments}
            copied = {key for key, count in client.copies.items() if count}
            sync = document.get("sync", {})
            rows.append(
                {
                    "phase": phase.name,
                    "exit": code,
                    "requests": dict(sorted(requests.items())),
                    "total_requests": sum(requests.values()),
                    "state_saves": client.state_saves - saves_before,
                    "completeness": {
                        "expected": len(expected),
                        "copied": len(expected & copied),
                        "duplicated": sorted(
                            f"{kind}:{item}"
                            for (kind, item), count in client.copies.items()
                            if count > 1
                        ),
                        "missing": sorted(
                            f"{kind}:{item}" for kind, item in expected - copied
                        ),
                        "unexpected": sorted(
                            f"{kind}:{item}" for kind, item in copied - expected
                        ),
                    },
                    "sync": {name: sync.get(name) for name in SYNC_FIELDS},
                    "seconds": round(seconds, 4),
                }
            )
    return {"scenario": scenario.name, "phases": rows}


def deterministic(report: dict) -> dict:
    """``report`` without wall time: what must match the baseline exactly."""
    return {
        "scenario": report["scenario"],
        "phases": [
            {key: value for key, value in row.items() if key != "seconds"}
            for row in report["phases"]
        ],
    }


def load_baseline() -> dict:
    return json.loads(BASELINE.read_text())


def _flatten(value, prefix: str = "") -> dict[str, object]:
    if isinstance(value, dict):
        flat: dict[str, object] = {}
        for key, item in value.items():
            flat.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return flat
    return {prefix: value}


def drift(baseline: list[dict], current: list[dict]) -> list[str]:
    """One line per value that differs, as ``scenario/phase key: old -> new``."""

    def keyed(reports: list[dict]) -> dict[str, object]:
        flat: dict[str, object] = {}
        for report in reports:
            for row in report["phases"]:
                where = f"{report['scenario']}/{row['phase']}"
                for key, value in _flatten(row).items():
                    if key != "phase":
                        flat[f"{where} {key}"] = value
        return flat

    old, new = keyed(baseline), keyed(current)
    return [
        f"{key}: {old.get(key, '(absent)')} -> {new.get(key, '(absent)')}"
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]
