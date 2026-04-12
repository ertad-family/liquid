from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from liquid.models.adapter import FieldMapping
    from liquid.protocols import KnowledgeStore

logger = logging.getLogger(__name__)


class MappingLearner:
    """Records corrections and retrieves known mappings for learning."""

    def __init__(self, knowledge: KnowledgeStore | None = None) -> None:
        self.knowledge = knowledge

    async def record_corrections(
        self,
        service: str,
        target_model: str,
        corrections: list[tuple[FieldMapping, FieldMapping]],
    ) -> None:
        """Store corrected mappings for future use."""
        if not self.knowledge or not corrections:
            return

        corrected_mappings = [corrected for _original, corrected in corrections]
        existing = await self.knowledge.find_mapping(service, target_model)

        merged = self._merge_mappings(existing, corrected_mappings) if existing else corrected_mappings

        await self.knowledge.store_mapping(service, target_model, merged)
        logger.info(
            "Stored %d corrected mappings for %s -> %s",
            len(corrected_mappings),
            service,
            target_model,
        )

    async def get_known_mappings(
        self,
        service: str,
        target_model: str,
    ) -> list[FieldMapping] | None:
        if not self.knowledge:
            return None
        return await self.knowledge.find_mapping(service, target_model)

    def _merge_mappings(
        self,
        existing: list[FieldMapping],
        new: list[FieldMapping],
    ) -> list[FieldMapping]:
        """Merge new corrections into existing mappings, preferring corrections."""
        by_target: dict[str, FieldMapping] = {}
        for m in existing:
            by_target[m.target_field] = m
        for m in new:
            by_target[m.target_field] = m
        return list(by_target.values())
