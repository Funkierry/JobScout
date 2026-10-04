"""JobScout request cancellation and progress-stream keepalives."""

import asyncio

from anyio import CancelScope


async def until_disconnected(operation, request):
    """Drain owned work on disconnect; cancellation never becomes a saved check."""
    if request is None:  # Direct callers/tests have no HTTP connection.
        return await operation
    task = asyncio.create_task(operation)

    async def watch():
        while not await request.is_disconnected():
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            return await task
        # Propagate watcher errors instead of leaving the operation orphaned.
        await watcher
        raise asyncio.CancelledError
    finally:
        with CancelScope(shield=True):
            for owned in (task, watcher):
                if not owned.done():
                    owned.cancel()
            await asyncio.gather(task, watcher, return_exceptions=True)


async def keepalive_stream(events, *, interval=10):
    """Yield None for a heartbeat without cancelling slow iterator work."""
    pending = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(events))
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield None
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield event
    finally:
        # Starlette cancels the stream's AnyIO scope on disconnect. Drain work
        # inside a shield so that scope cannot interrupt browser/lock cleanup.
        with CancelScope(shield=True):
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await events.aclose()
