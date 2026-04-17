"""Rate limit quota information."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel


class QuotaInfo(BaseModel):
    """Current rate-limit quota for an adapter."""

    remaining: int | None = None
    limit: int | None = None
    reset_at: datetime | None = None
    reset_in_seconds: float | None = None

    @property
    def is_near_limit(self) -> bool:
        """True if fewer than 10% of quota remains."""
        if self.remaining is None or self.limit is None or self.limit == 0:
            return False
        return self.remaining / self.limit < 0.1

    @property
    def is_empty(self) -> bool:
        return self.remaining == 0

    def time_until_reset(self) -> float:
        if self.reset_in_seconds is not None:
            return max(0.0, self.reset_in_seconds)
        if self.reset_at is not None:
            delta = (self.reset_at - datetime.now(UTC)).total_seconds()
            return max(0.0, delta)
        return 0.0
