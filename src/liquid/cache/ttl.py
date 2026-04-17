"""TTL parsing helpers."""

from __future__ import annotations

import re


def parse_ttl(ttl: int | str | None) -> int:
    """Parse TTL from int (seconds) or string shorthand.

    Examples:
        parse_ttl(300) -> 300
        parse_ttl("5m") -> 300
        parse_ttl("1h") -> 3600
        parse_ttl("30s") -> 30
        parse_ttl(None) -> 0
    """
    if ttl is None:
        return 0
    if isinstance(ttl, bool):
        # Guard: bool is a subclass of int in Python; reject explicitly.
        return 0
    if isinstance(ttl, int):
        return max(0, ttl)
    if isinstance(ttl, str):
        match = re.match(r"^(\d+)([smhd])?$", ttl.strip().lower())
        if not match:
            return 0
        num = int(match.group(1))
        unit = match.group(2) or "s"
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        return num * multipliers[unit]
    return 0


def parse_cache_control(header: str | None) -> int | None:
    """Parse Cache-Control header. Returns max-age in seconds, or None.

    Respects no-store and no-cache directives by returning 0.
    """
    if not header:
        return None

    # no-store / no-cache -> don't cache
    if "no-store" in header or "no-cache" in header:
        return 0

    # max-age=N
    match = re.search(r"max-age\s*=\s*(\d+)", header)
    if match:
        return int(match.group(1))

    return None
