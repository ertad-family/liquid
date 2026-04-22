"""Pure functions that extract evolution signals from HTTP response headers.

No I/O, no state — callable from Fetcher / Streaming / webhook paths alike.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Mapping


class EvolutionKind(StrEnum):
    DEPRECATED = "deprecated"
    SUNSET_SCHEDULED = "sunset_scheduled"
    VERSION_DRIFT = "version_drift"


class EvolutionSignal(BaseModel):
    kind: EvolutionKind
    severity: str = "warn"  # "info" | "warn" | "critical"
    message: str
    endpoint: str | None = None
    deprecation: str | None = None  # raw Deprecation header value
    sunset_at: datetime | None = None  # parsed Sunset header
    expected_version: str | None = None
    observed_version: str | None = None
    observed_at: datetime


_VERSION_HEADER_CANDIDATES = (
    "api-version",
    "x-api-version",
    "openai-version",
    "stripe-version",
    "github-version",
    "x-ms-api-version",
)


def _normalise(headers: Mapping[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _parse_sunset(raw: str) -> datetime | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_deprecation(raw: str) -> tuple[str, datetime | None]:
    """Per RFC 9745 the value can be `true`, a Unix timestamp, or an
    IMF-fixdate. We normalise to (raw, datetime_or_None)."""
    v = raw.strip()
    if not v or v.lower() == "true":
        return raw, None
    if v.isdigit():
        try:
            return raw, datetime.fromtimestamp(int(v), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return raw, None
    parsed = _parse_sunset(v)
    return raw, parsed


def extract_signals(
    headers: Mapping[str, str],
    *,
    endpoint: str | None = None,
    expected_version: str | None = None,
    now: datetime | None = None,
) -> list[EvolutionSignal]:
    """Return zero or more signals from the given response headers.

    ``expected_version`` is typically taken from the adapter schema at the
    discovery time; when it differs from whatever the provider reports in a
    known version header we emit :class:`EvolutionKind.VERSION_DRIFT`.
    """
    h = _normalise(headers)
    observed_at = now or datetime.now(UTC)
    signals: list[EvolutionSignal] = []

    if "deprecation" in h:
        raw, when = _parse_deprecation(h["deprecation"])
        in_future = when is not None and when > observed_at
        signals.append(
            EvolutionSignal(
                kind=EvolutionKind.DEPRECATED,
                severity="info" if in_future else "warn",
                message=f"Provider reports endpoint deprecated (Deprecation: {raw})",
                endpoint=endpoint,
                deprecation=raw,
                sunset_at=when if in_future else None,
                observed_at=observed_at,
            )
        )

    if "sunset" in h:
        when = _parse_sunset(h["sunset"])
        if when is not None:
            severity = "critical" if when <= observed_at else "warn"
            signals.append(
                EvolutionSignal(
                    kind=EvolutionKind.SUNSET_SCHEDULED,
                    severity=severity,
                    message=f"Provider will remove endpoint at {when.isoformat()}",
                    endpoint=endpoint,
                    sunset_at=when,
                    observed_at=observed_at,
                )
            )

    if expected_version:
        observed_version: str | None = None
        for key in _VERSION_HEADER_CANDIDATES:
            if h.get(key):
                observed_version = h[key]
                break
        if observed_version and observed_version != expected_version:
            signals.append(
                EvolutionSignal(
                    kind=EvolutionKind.VERSION_DRIFT,
                    severity="warn",
                    message=(f"Provider version drifted: expected {expected_version}, observed {observed_version}"),
                    endpoint=endpoint,
                    expected_version=expected_version,
                    observed_version=observed_version,
                    observed_at=observed_at,
                )
            )

    return signals
