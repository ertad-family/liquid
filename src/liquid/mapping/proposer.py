from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from liquid.exceptions import MappingError
from liquid.models.adapter import FieldMapping

if TYPE_CHECKING:
    from liquid.models.schema import APISchema
    from liquid.protocols import KnowledgeStore, LLMBackend

logger = logging.getLogger(__name__)


class MappingProposer:
    """Proposes field mappings using KnowledgeStore (if available) then LLM."""

    def __init__(self, llm: LLMBackend, knowledge: KnowledgeStore | None = None) -> None:
        self.llm = llm
        self.knowledge = knowledge

    async def propose(
        self,
        schema: APISchema,
        target_model: dict[str, Any],
        existing_mappings: list[FieldMapping] | None = None,
        removed_fields: list[str] | None = None,
    ) -> list[FieldMapping]:
        if existing_mappings and removed_fields is not None:
            return await self._selective_repropose(
                schema,
                target_model,
                existing_mappings,
                set(removed_fields),
            )

        if self.knowledge:
            known = await self.knowledge.find_mapping(schema.service_name, json.dumps(target_model))
            if known:
                logger.info("Found existing mappings for %s in knowledge store", schema.service_name)
                return known

        return await self._propose_with_llm(schema, target_model)

    async def _selective_repropose(
        self,
        schema: APISchema,
        target_model: dict[str, Any],
        existing: list[FieldMapping],
        removed: set[str],
    ) -> list[FieldMapping]:
        kept: list[FieldMapping] = []
        broken_targets: list[str] = []

        for m in existing:
            if m.source_path in removed:
                broken_targets.append(m.target_field)
                logger.info("Mapping %s → %s dropped (field removed)", m.source_path, m.target_field)
            else:
                kept.append(
                    FieldMapping(
                        source_path=m.source_path,
                        target_field=m.target_field,
                        transform=m.transform,
                        confidence=1.0,
                    )
                )

        if broken_targets:
            new_proposals = await self._propose_with_llm(schema, target_model)
            for proposal in new_proposals:
                if proposal.target_field in broken_targets and not any(
                    k.target_field == proposal.target_field for k in kept
                ):
                    kept.append(proposal)

        return kept

    async def _propose_with_llm(
        self,
        schema: APISchema,
        target_model: dict[str, Any],
    ) -> list[FieldMapping]:
        from liquid.models.llm import Message

        endpoints_desc = "\n".join(f"- {ep.method} {ep.path}: {ep.description}" for ep in schema.endpoints[:20])
        response_schemas = "\n".join(
            f"  {ep.path}: {json.dumps(ep.response_schema)[:300]}" for ep in schema.endpoints[:10] if ep.response_schema
        )

        messages = [
            Message(
                role="system",
                content=(
                    "You are a data mapping expert. Given an API schema and a target data model, "
                    "propose field mappings. Respond with a JSON array of objects, each with: "
                    "source_path (string, dot-notation), target_field (string), "
                    "transform (string expression or null), confidence (float 0-1)."
                ),
            ),
            Message(
                role="user",
                content=(
                    f"API: {schema.service_name}\n"
                    f"Endpoints:\n{endpoints_desc}\n\n"
                    f"Response schemas:\n{response_schemas}\n\n"
                    f"Target model:\n{json.dumps(target_model, indent=2)}\n\n"
                    "Propose field mappings as a JSON array."
                ),
            ),
        ]

        try:
            response = await self.llm.chat(messages)
            return self._parse_mappings(response.content or "[]")
        except Exception as e:
            raise MappingError(f"LLM mapping proposal failed: {e}") from e

    def _parse_mappings(self, content: str) -> list[FieldMapping]:
        try:
            start = content.find("[")
            end = content.rfind("]") + 1
            if start == -1 or end == 0:
                return []
            raw = json.loads(content[start:end])
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM mapping response")
            return []

        mappings: list[FieldMapping] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if "source_path" not in item or "target_field" not in item:
                continue
            try:
                mappings.append(
                    FieldMapping(
                        source_path=item["source_path"],
                        target_field=item["target_field"],
                        transform=item.get("transform"),
                        confidence=float(item.get("confidence", 0.5)),
                    )
                )
            except (ValueError, TypeError):
                continue

        return mappings
