"""Where a synchronous command step runs its coroutine (ADR-0124).

A `tg` process runs each one with `asyncio.run`: a fresh loop per step. `tg-serve`
keeps one Telegram client connected across many commands, and a Telethon client
belongs to the loop it connected on, so the server points `run` at its own
long-lived loop instead.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Coroutine, Generator
from typing import Any

_loop: asyncio.AbstractEventLoop | None = None


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run `coro` to completion and return its result."""
    if _loop is None:
        return asyncio.run(coro)
    started: list[asyncio.Task] = []

    async def step() -> T:
        task = asyncio.current_task()
        if task is not None:
            started.append(task)
        return await coro

    future = asyncio.run_coroutine_threadsafe(step(), _loop)
    try:
        return future.result()
    except BaseException:
        # A deadline signal or a kill landed while waiting: the coroutine must
        # not outlive the step that started it, so cancel it and, as
        # asyncio.run would, wait until its cleanup has run.
        future.cancel()
        if started:
            asyncio.run_coroutine_threadsafe(asyncio.wait(started), _loop).result()
        raise


@contextlib.contextmanager
def running_on(loop: asyncio.AbstractEventLoop) -> Generator[None]:
    """Route every `run` to `loop`, which another thread keeps running."""
    global _loop
    previous, _loop = _loop, loop
    try:
        yield
    finally:
        _loop = previous
