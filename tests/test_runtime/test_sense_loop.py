"""The sensorimotor loop — merge_senses (fan-in) and react (dispatch). Pure
in-process async tests with synthetic sense streams; no transport needed."""

from __future__ import annotations

import asyncio

from liquid.sense_loop import merge_senses, react
from liquid.transport.base import SenseEvent


async def _emit(values, *, delay=0.0, prefix="s"):
    """A synthetic sense stream yielding one event per value."""
    for v in values:
        if delay:
            await asyncio.sleep(delay)
        yield SenseEvent(source=prefix, payload={"v": v})


# --- merge_senses ----------------------------------------------------------


async def test_merge_yields_all_events_from_all_sources():
    merged = merge_senses(_emit([1, 2], prefix="a"), _emit([3, 4], prefix="b"))
    seen = [e.payload["v"] async for e in merged]
    assert sorted(seen) == [1, 2, 3, 4]


async def test_merge_interleaves_by_arrival():
    # Source "fast" emits every 0.01s; "slow" every 0.05s → fast events arrive first.
    fast = _emit(["f1", "f2", "f3"], delay=0.01, prefix="fast")
    slow = _emit(["s1"], delay=0.05, prefix="slow")
    seen = [e.payload["v"] async for e in merge_senses(fast, slow)]
    assert seen.index("f1") < seen.index("s1")
    assert set(seen) == {"f1", "f2", "f3", "s1"}


async def test_merge_isolates_a_failing_source():
    async def boom():
        yield SenseEvent(source="boom", payload={"v": "ok"})
        raise RuntimeError("source died")

    seen = [e.payload["v"] async for e in merge_senses(boom(), _emit([1, 2], prefix="good"))]
    # The good source's events all survive; the failing one is dropped after its error.
    assert set(seen) == {"ok", 1, 2}


async def test_merge_empty_is_empty():
    assert [e async for e in merge_senses()] == []


# --- react -----------------------------------------------------------------


async def test_react_dispatches_each_event_in_order():
    handled: list = []

    async def handler(event):
        handled.append(event.payload["v"])

    count = await react(_emit([1, 2, 3]), handler)
    assert handled == [1, 2, 3]
    assert count == 3


async def test_react_isolates_handler_errors_and_continues():
    handled: list = []
    errors: list = []

    async def handler(event):
        if event.payload["v"] == 2:
            raise ValueError("bad event")
        handled.append(event.payload["v"])

    async def on_error(event, exc):
        errors.append((event.payload["v"], str(exc)))

    count = await react(_emit([1, 2, 3]), handler, on_error=on_error)
    assert handled == [1, 3]  # event 2 failed but the loop continued
    assert errors == [(2, "bad event")]
    assert count == 3  # all three were dispatched


async def test_react_bounds_concurrency():
    in_flight = 0
    peak = 0

    async def handler(event):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1

    await react(_emit(list(range(10))), handler, max_concurrency=3)
    assert peak <= 3  # never more than 3 handlers running at once
    assert peak > 1  # but it did overlap (proves concurrency, not just sequential)


async def test_react_default_is_sequential():
    in_flight = 0
    peak = 0

    async def handler(event):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1

    await react(_emit(list(range(5))), handler)  # max_concurrency=1
    assert peak == 1


async def test_react_over_merged_streams():
    handled: list = []

    async def handler(event):
        handled.append(event.payload["v"])

    merged = merge_senses(_emit([1, 2], prefix="a"), _emit([3], prefix="b"))
    count = await react(merged, handler)
    assert sorted(handled) == [1, 2, 3]
    assert count == 3
