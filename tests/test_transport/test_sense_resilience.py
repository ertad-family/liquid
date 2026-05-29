"""Sense resilience: a stream that errors must end gracefully (not crash the host
loop) AND leave a debug breadcrumb (so a *bug* mid-stream isn't fully invisible —
the lesson from the silently-swallowed SSE discovery bug). Exercised on the shared
SQL delta-poll loop that backs all five SQL sense drivers."""

from __future__ import annotations

import logging

from liquid.models.schema import Endpoint
from liquid.transport._sql import SQLITE, run_sql_delta_sense


class _Ctx:
    """Minimal SenseContext stand-in for the shared loop."""

    def __init__(self):
        self.endpoint = Endpoint(
            path="/t",
            protocol="sqlite",
            method="GET",
            transport_meta={"table": "t", "columns": ["id"], "watch_column": "id"},
        )
        self.cursor = None
        self.poll_interval = 0.01
        self.max_events = None
        self.max_seconds = 5.0


async def test_query_error_ends_stream_gracefully_with_breadcrumb(caplog):
    async def run_query(sql, args):
        raise RuntimeError("table went away")

    with caplog.at_level(logging.DEBUG, logger="liquid.transport._sql"):
        events = [e async for e in run_sql_delta_sense(_Ctx(), SQLITE, run_query)]

    assert events == []  # ended gracefully, no exception propagated
    assert any("ended on error" in r.message for r in caplog.records)  # breadcrumb left
    assert any(r.exc_info for r in caplog.records)  # with a traceback to debug from


async def test_event_shaping_is_not_swallowed_by_the_query_guard():
    # The except guards only run_query (a DB/connection failure). Event shaping
    # (coerce_row / SenseEvent) sits outside it, so a bug there must propagate —
    # not be silently absorbed as "stream ended". A row missing the watch column
    # still yields (cursor falls back), proving shaping runs outside the guard.
    async def run_query(sql, args):
        return [{"id": 1, "__cursor__": 1}]

    ctx = _Ctx()
    ctx.max_events = 1
    events = [e async for e in run_sql_delta_sense(ctx, SQLITE, run_query)]
    assert len(events) == 1
    assert events[0].cursor == "1"
