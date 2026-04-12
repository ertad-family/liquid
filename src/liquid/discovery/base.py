from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from liquid.exceptions import DiscoveryError
from liquid.models.schema import APISchema  # noqa: TC001

logger = logging.getLogger(__name__)


@runtime_checkable
class DiscoveryStrategy(Protocol):
    async def discover(self, url: str) -> APISchema | None:
        """Try to discover the API at the given URL.

        Returns APISchema on success, None if this strategy doesn't apply.
        Raises DiscoveryError on unexpected failures.
        """
        ...


class DiscoveryPipeline:
    """Tries discovery strategies in order, returns first success."""

    def __init__(self, strategies: list[DiscoveryStrategy]) -> None:
        self.strategies = strategies

    async def discover(self, url: str) -> APISchema:
        errors: list[tuple[str, Exception]] = []

        for strategy in self.strategies:
            strategy_name = type(strategy).__name__
            logger.info("Trying discovery strategy: %s for %s", strategy_name, url)
            try:
                result = await strategy.discover(url)
                if result is not None:
                    logger.info("Discovery succeeded with %s", strategy_name)
                    return result
                logger.debug("Strategy %s returned None, trying next", strategy_name)
            except DiscoveryError as e:
                logger.warning("Strategy %s failed: %s", strategy_name, e)
                errors.append((strategy_name, e))
            except Exception as e:
                logger.warning("Strategy %s unexpected error: %s", strategy_name, e)
                errors.append((strategy_name, e))

        error_summary = "; ".join(f"{name}: {err}" for name, err in errors)
        raise DiscoveryError(
            f"All discovery strategies failed for {url}. Errors: {error_summary}"
            if errors
            else f"No discovery strategy could handle {url}"
        )
