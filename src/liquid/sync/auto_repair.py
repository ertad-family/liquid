"""Auto-repair handler that triggers adapter repair on persistent failures."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable  # noqa: TC003
from typing import TYPE_CHECKING, Any

from liquid.events import Event, ReDiscoveryNeeded

if TYPE_CHECKING:
    from liquid.client import Liquid
    from liquid.mapping.reviewer import MappingReview
    from liquid.models.adapter import AdapterConfig

logger = logging.getLogger(__name__)


class AutoRepairHandler:
    """Event handler that automatically repairs adapters on ReDiscoveryNeeded.

    Usage:
        handler = AutoRepairHandler(
            liquid=liquid_client,
            target_model={"amount": "float", "date": "datetime"},
            config_provider=lambda: current_config,
            on_repair=my_repair_callback,
        )
    """

    def __init__(
        self,
        liquid: Liquid,
        target_model: dict[str, Any],
        config_provider: Callable[[], AdapterConfig],
        on_repair: Callable[[AdapterConfig | MappingReview], Awaitable[None]],
        auto_approve: bool = False,
        confidence_threshold: float = 0.8,
    ) -> None:
        self.liquid = liquid
        self.target_model = target_model
        self.config_provider = config_provider
        self.on_repair = on_repair
        self.auto_approve = auto_approve
        self.confidence_threshold = confidence_threshold

    async def handle(self, event: Event) -> None:
        if not isinstance(event, ReDiscoveryNeeded):
            return

        config = self.config_provider()
        logger.info("Auto-repair triggered for adapter %s: %s", config.config_id, event.reason)

        try:
            result = await self.liquid.repair_adapter(
                config=config,
                target_model=self.target_model,
                auto_approve=self.auto_approve,
                confidence_threshold=self.confidence_threshold,
            )
            await self.on_repair(result)
            logger.info("Auto-repair completed for adapter %s", config.config_id)
        except Exception:
            logger.exception("Auto-repair failed for adapter %s", config.config_id)
