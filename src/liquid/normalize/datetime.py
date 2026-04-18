"""Datetime normalization — coerce any common timestamp shape to aware UTC ``datetime``."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

# Unix timestamps expressed in milliseconds are >= 10^12 (approx year 2001 in ms).
_UNIX_MS_THRESHOLD = 10**12


def normalize_datetime(value: Any) -> datetime | None:
    """Parse ``value`` into an aware UTC ``datetime`` or return ``None``.

    Accepts:
    - ``datetime`` — returned as UTC (naive inputs are treated as already-UTC).
    - ISO 8601 strings with or without timezone ("Z" suffix, numeric offset, or naive).
    - Unix timestamp integers in seconds (< 10^12) or milliseconds (>= 10^12).
    - Unix timestamp floats (seconds, with fractional microseconds).
    - RFC 2822 strings (e.g. HTTP ``Date`` headers).

    Returns ``None`` for unrecognised input. Never raises.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return _from_unix(float(value))

    if isinstance(value, float):
        return _from_unix(value)

    if isinstance(value, str):
        return _from_string(value)

    return None


def _from_unix(seconds_or_ms: float) -> datetime | None:
    try:
        if abs(seconds_or_ms) >= _UNIX_MS_THRESHOLD:
            return datetime.fromtimestamp(seconds_or_ms / 1000.0, tz=UTC)
        return datetime.fromtimestamp(seconds_or_ms, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _from_string(raw: str) -> datetime | None:
    s = raw.strip()
    if not s:
        return None

    # Numeric string → Unix timestamp.
    if _looks_numeric(s):
        try:
            return _from_unix(float(s))
        except ValueError:
            return None

    # ISO 8601 — tolerate trailing "Z" by mapping to "+00:00".
    iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is not None:
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    # RFC 2822 (HTTP Date headers, email-style).
    try:
        rfc = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if rfc is None:
        return None
    return rfc.astimezone(UTC) if rfc.tzinfo else rfc.replace(tzinfo=UTC)


def _looks_numeric(s: str) -> bool:
    if not s:
        return False
    body = s[1:] if s[0] in "+-" else s
    if not body:
        return False
    seen_dot = False
    for ch in body:
        if ch == ".":
            if seen_dot:
                return False
            seen_dot = True
            continue
        if not ch.isdigit():
            return False
    return True
