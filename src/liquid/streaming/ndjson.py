"""NDJSON (newline-delimited JSON) parser over an async byte stream."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def parse_ndjson(
    byte_stream: AsyncIterator[bytes],
    *,
    skip_blank_lines: bool = True,
    strict: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """Yield one parsed JSON object per newline-terminated line.

    Byte chunks from the upstream iterator may split mid-line — this buffer
    accumulates partials and only emits complete lines. Any non-object root
    (array/scalar) in ``strict=True`` mode raises ``ValueError``; in
    ``strict=False`` mode it is silently skipped.
    """
    buf = b""
    async for chunk in byte_stream:
        if not chunk:
            continue
        buf += chunk
        while True:
            newline_idx = buf.find(b"\n")
            if newline_idx < 0:
                break
            line = buf[:newline_idx]
            buf = buf[newline_idx + 1 :]
            if skip_blank_lines and not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                if strict:
                    raise
                continue
            if not isinstance(obj, dict):
                if strict:
                    raise ValueError(f"expected object, got {type(obj).__name__}")
                continue
            yield obj
    # flush trailing line without newline
    if buf.strip():
        try:
            obj = json.loads(buf)
        except json.JSONDecodeError:
            if strict:
                raise
            return
        if isinstance(obj, dict):
            yield obj
        elif strict:
            raise ValueError(f"expected object, got {type(obj).__name__}")
