"""The sensorimotor loop — drive an agent from what it perceives.

`sense()` gives an agent a stream of events; this module is the host-side glue
that turns perception into action. An LLM agent is a pull-loop (it acts only when
invoked), so a long-running *host* runs the loop: it perceives events and, for
each, wakes the agent — which may then act (`write`/`execute`). That closes the
afferent→efferent arc the whole library is built around.

Two primitives, both pure-`asyncio` (no new dependency):

* :func:`merge_senses` — fan several sense streams into one, yielding events as
  any source produces them, so a single loop can watch a DB table *and* a Redis
  channel *and* an inbound webhook at once.
* :func:`react` — consume one stream and dispatch each event to a handler with
  error isolation (one bad event never kills the loop) and bounded concurrency
  (back-pressure on the stream when handlers fall behind).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from liquid.transport.base import SenseEvent

logger = logging.getLogger(__name__)

_SENTINEL = object()


async def merge_senses(*streams: AsyncIterator[Any]) -> AsyncIterator[Any]:
    """Merge several sense streams into one, yielding events as they arrive.

    Each input is drained by its own task into a shared queue; events surface in
    arrival order across all sources (not round-robin). The merged stream ends
    when *every* input is exhausted. A source that raises is logged and treated
    as ended — it never tears down the others. On consumer exit (break / outer
    cancellation) the pump tasks are cancelled.
    """
    if not streams:
        return

    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def _pump(stream: AsyncIterator[Any]) -> None:
        try:
            async for event in stream:
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sense source failed; dropping it from the merge")
        finally:
            await queue.put(_SENTINEL)

    tasks = [asyncio.create_task(_pump(s)) for s in streams]
    active = len(tasks)
    try:
        while active > 0:
            item = await queue.get()
            if item is _SENTINEL:
                active -= 1
                continue
            yield item
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def react(
    stream: AsyncIterator[Any],
    handler: Callable[[SenseEvent], Awaitable[Any]],
    *,
    max_concurrency: int = 1,
    on_error: Callable[[SenseEvent, Exception], Awaitable[Any] | Any] | None = None,
) -> int:
    """Drive ``handler`` for each event in ``stream`` — the host's react loop.

    Perceive → act: every event is handed to ``handler`` (an async callback that
    typically inspects the event and may call ``liquid.write`` / ``liquid.execute``
    in response). Runs until the stream ends (it's bounded by the underlying
    ``sense``'s ``max_events`` / ``max_seconds``) or the caller cancels.

    * ``max_concurrency`` — how many handler invocations may run at once. The
      default ``1`` processes events one at a time, in order; higher values let
      slow handlers overlap. The loop applies back-pressure: it stops pulling new
      events while ``max_concurrency`` handlers are in flight.
    * ``on_error`` — called as ``on_error(event, exc)`` (sync or async) when a
      handler raises; if omitted, the exception is logged. Either way the loop
      continues — one failed event never stops perception.

    Returns the number of events dispatched.
    """
    sem = asyncio.Semaphore(max(1, max_concurrency))
    tasks: set[asyncio.Task[Any]] = set()
    dispatched = 0

    async def _run_one(event: Any) -> None:
        try:
            await handler(event)
        except Exception as exc:
            if on_error is not None:
                try:
                    result = on_error(event, exc)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception("sense on_error callback failed")
            else:
                logger.exception("sense handler failed for event from %s", getattr(event, "source", "?"))
        finally:
            sem.release()

    try:
        async for event in stream:
            await sem.acquire()  # back-pressure: block until a slot frees
            dispatched += 1
            task = asyncio.create_task(_run_one(event))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    return dispatched
