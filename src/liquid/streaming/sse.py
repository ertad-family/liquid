"""Server-Sent Events parser per the WHATWG spec.

Events are delimited by a blank line. Known fields:
  * ``event:`` — event type (defaults to ``"message"``)
  * ``data:`` — payload; multiple ``data:`` lines concatenate with ``\\n``
  * ``id:``   — last-event-id for reconnection (exposed on the event)
  * ``retry:`` — reconnect delay in ms (exposed on the event)

Comment lines (``: ...``) are ignored.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class SSEEvent(BaseModel):
    event: str = "message"
    data: str = ""
    id: str | None = None
    retry: int | None = None


async def parse_sse(byte_stream: AsyncIterator[bytes]) -> AsyncIterator[SSEEvent]:
    """Yield one :class:`SSEEvent` per blank-line-terminated event block."""
    buf = b""
    event_type = "message"
    data_lines: list[str] = []
    event_id: str | None = None
    retry: int | None = None

    async def _emit() -> SSEEvent | None:
        nonlocal event_type, data_lines, retry
        if not data_lines and event_type == "message" and retry is None:
            # empty/comment-only block — skip
            data_lines = []
            event_type = "message"
            return None
        ev = SSEEvent(
            event=event_type,
            data="\n".join(data_lines),
            id=event_id,
            retry=retry,
        )
        event_type = "message"
        data_lines = []
        retry = None
        return ev

    async for chunk in byte_stream:
        if not chunk:
            continue
        buf += chunk
        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                break
            raw_line = buf[:nl]
            buf = buf[nl + 1 :]
            # strip trailing CR if present (CRLF normalisation)
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            line = raw_line.decode("utf-8", errors="replace")

            if line == "":
                ev = await _emit()
                if ev is not None:
                    yield ev
                continue
            if line.startswith(":"):
                # comment — ignore
                continue
            if ":" in line:
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
            else:
                field, value = line, ""
            if field == "event":
                event_type = value or "message"
            elif field == "data":
                data_lines.append(value)
            elif field == "id":
                event_id = value or None
            elif field == "retry":
                with contextlib.suppress(ValueError):
                    retry = int(value)
            # unknown fields are silently dropped per spec

    # flush final event if buffer ended without blank line
    if data_lines or event_type != "message":
        ev = await _emit()
        if ev is not None:
            yield ev
