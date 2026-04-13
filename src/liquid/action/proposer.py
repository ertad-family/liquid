from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from liquid.exceptions import MappingError
from liquid.models.action import ActionMapping

if TYPE_CHECKING:
    from liquid.models.adapter import FieldMapping
    from liquid.models.schema import Endpoint
    from liquid.protocols import KnowledgeStore, LLMBackend

logger = logging.getLogger(__name__)


class ActionProposer:
    """Proposes action (write) mappings using inversion, knowledge, then LLM."""

    def __init__(self, llm: LLMBackend, knowledge: KnowledgeStore | None = None) -> None:
        self.llm = llm
        self.knowledge = knowledge

    async def propose(
        self,
        endpoint: Endpoint,
        agent_model: dict[str, Any],
        existing_read_mappings: list[FieldMapping] | None = None,
    ) -> list[ActionMapping]:
        """Propose action mappings for a write endpoint.

        Strategy:
        1. Invert existing read mappings (high confidence).
        2. Check knowledge store for known action mappings.
        3. Fall back to LLM.
        """
        # Step 1: Invert read mappings
        if existing_read_mappings:
            inverted = self._invert_read_mappings(existing_read_mappings, agent_model)
            if inverted:
                logger.info(
                    "Inverted %d read mappings for %s %s",
                    len(inverted),
                    endpoint.method,
                    endpoint.path,
                )
                return inverted

        # Step 2: Knowledge store
        if self.knowledge:
            action_key = f"action:{endpoint.method}:{endpoint.path}"
            known = await self.knowledge.find_mapping(action_key, json.dumps(agent_model))
            if known:
                logger.info("Found known action mappings for %s %s", endpoint.method, endpoint.path)
                # Convert FieldMappings from knowledge store to ActionMappings
                return [
                    ActionMapping(
                        source_field=fm.target_field,
                        target_path=fm.source_path,
                        transform=fm.transform,
                        confidence=fm.confidence,
                    )
                    for fm in known
                ]

        # Step 3: LLM
        return await self._propose_with_llm(endpoint, agent_model)

    def _invert_read_mappings(
        self,
        read_mappings: list[FieldMapping],
        agent_model: dict[str, Any],
    ) -> list[ActionMapping]:
        """Invert read mappings: if read maps source → target, write maps target → source."""
        inverted: list[ActionMapping] = []
        for rm in read_mappings:
            # Only invert if the target_field exists in the agent model
            if rm.target_field not in agent_model:
                continue

            # Strip array brackets from source_path for the target_path
            # e.g., "orders[].total_price" → "total_price"
            target_path = re.sub(r"\w+\[\]\.", "", rm.source_path)

            inverted.append(
                ActionMapping(
                    source_field=rm.target_field,
                    target_path=target_path,
                    transform=rm.transform,
                    confidence=0.95,
                )
            )
        return inverted

    async def _propose_with_llm(
        self,
        endpoint: Endpoint,
        agent_model: dict[str, Any],
    ) -> list[ActionMapping]:
        from liquid.models.llm import Message

        request_schema_str = json.dumps(endpoint.request_schema or {}, indent=2)

        messages = [
            Message(
                role="system",
                content=(
                    "You are a data mapping expert. Given an API write endpoint's request schema "
                    "and an agent's data model, propose action mappings. Respond with a JSON array "
                    "of objects, each with: source_field (string, agent field), target_path (string, "
                    "dot-notation API request field), transform (string expression or null), "
                    "confidence (float 0-1)."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"Endpoint: {endpoint.method} {endpoint.path}\n"
                    f"Description: {endpoint.description}\n\n"
                    f"Request schema:\n{request_schema_str}\n\n"
                    f"Agent model:\n{json.dumps(agent_model, indent=2)}\n\n"
                    "Propose action mappings as a JSON array."
                ),
            ),
        ]

        try:
            response = await self.llm.chat(messages)
            return self._parse_action_mappings(response.content or "[]")
        except Exception as e:
            raise MappingError(f"LLM action mapping proposal failed: {e}") from e

    def _parse_action_mappings(self, content: str) -> list[ActionMapping]:
        try:
            start = content.find("[")
            end = content.rfind("]") + 1
            if start == -1 or end == 0:
                return []
            raw = json.loads(content[start:end])
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM action mapping response")
            return []

        mappings: list[ActionMapping] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if "source_field" not in item or "target_path" not in item:
                continue
            try:
                mappings.append(
                    ActionMapping(
                        source_field=item["source_field"],
                        target_path=item["target_path"],
                        transform=item.get("transform"),
                        confidence=float(item.get("confidence", 0.5)),
                    )
                )
            except (ValueError, TypeError):
                continue

        return mappings
