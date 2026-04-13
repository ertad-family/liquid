"""Liquid — the main orchestrator tying all phases together."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from liquid.auth.classifier import AuthClassifier, EscalationInfo
from liquid.auth.manager import AuthManager
from liquid.discovery.base import DiscoveryPipeline
from liquid.discovery.browser import BrowserDiscovery
from liquid.discovery.diff import diff_schemas
from liquid.discovery.graphql import GraphQLDiscovery
from liquid.discovery.mcp import MCPDiscovery
from liquid.discovery.openapi import OpenAPIDiscovery
from liquid.discovery.rest_heuristic import RESTHeuristicDiscovery
from liquid.mapping.learning import MappingLearner
from liquid.mapping.proposer import MappingProposer
from liquid.mapping.reviewer import MappingReview
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema  # noqa: TC001
from liquid.sync.engine import SyncEngine
from liquid.sync.fetcher import Fetcher
from liquid.sync.mapper import RecordMapper

if TYPE_CHECKING:
    from liquid.events import EventHandler
    from liquid.models.sync import SyncResult
    from liquid.protocols import DataSink, KnowledgeStore, LLMBackend, Vault
    from liquid.sync.retry import RetryPolicy


class Liquid:
    """Main entry point for the Liquid library.

    Orchestrates: discover → classify auth → propose mappings → sync.
    """

    def __init__(
        self,
        llm: LLMBackend,
        vault: Vault,
        sink: DataSink,
        knowledge: KnowledgeStore | None = None,
        event_handler: EventHandler | None = None,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.llm = llm
        self.vault = vault
        self.sink = sink
        self.knowledge = knowledge
        self.event_handler = event_handler
        self._http_client = http_client
        self._retry_policy = retry_policy

        self._auth_classifier = AuthClassifier()
        self._auth_manager = AuthManager(vault)
        self._mapping_proposer = MappingProposer(llm, knowledge)
        self._mapping_learner = MappingLearner(knowledge)

    async def discover(self, url: str) -> APISchema:
        """Phase 1: Discover the API at the given URL."""
        client = self._http_client or httpx.AsyncClient()
        try:
            pipeline = DiscoveryPipeline(
                [
                    MCPDiscovery(),
                    OpenAPIDiscovery(http_client=client),
                    GraphQLDiscovery(http_client=client),
                    RESTHeuristicDiscovery(llm=self.llm, http_client=client),
                    BrowserDiscovery(llm=self.llm),
                ]
            )
            return await pipeline.discover(url)
        finally:
            if not self._http_client:
                await client.aclose()

    def classify_auth(self, schema: APISchema) -> EscalationInfo:
        """Phase 2: Classify auth requirements and return escalation info."""
        return self._auth_classifier.classify(schema.auth)

    async def store_credentials(self, adapter_id: str, credentials: dict[str, Any]) -> str:
        """Phase 2b: Store credentials after human provides them."""
        return await self._auth_manager.store_credentials(adapter_id, credentials)

    async def propose_mappings(
        self,
        schema: APISchema,
        target_model: dict[str, Any],
    ) -> MappingReview:
        """Phase 3: AI proposes field mappings for human review."""
        proposals = await self._mapping_proposer.propose(schema, target_model)
        return MappingReview(proposals)

    async def create_adapter(
        self,
        schema: APISchema,
        auth_ref: str,
        mappings: list[FieldMapping],
        sync_config: SyncConfig,
        verified_by: str | None = None,
    ) -> AdapterConfig:
        """Phase 3b: Create the final adapter config after human approval."""
        from datetime import UTC, datetime

        return AdapterConfig(
            schema=schema,
            auth_ref=auth_ref,
            mappings=mappings,
            sync=sync_config,
            verified_by=verified_by,
            verified_at=datetime.now(UTC) if verified_by else None,
        )

    async def sync(self, config: AdapterConfig, cursor: str | None = None) -> SyncResult:
        """Phase 4: Run a deterministic sync cycle."""
        client = self._http_client or httpx.AsyncClient()
        try:
            fetcher = Fetcher(http_client=client, vault=self.vault)
            mapper = RecordMapper(config.mappings)
            engine = SyncEngine(
                fetcher=fetcher,
                mapper=mapper,
                sink=self.sink,
                event_handler=self.event_handler,
                retry_policy=self._retry_policy,
            )
            return await engine.run(config, cursor)
        finally:
            if not self._http_client:
                await client.aclose()

    async def repair_adapter(
        self,
        config: AdapterConfig,
        target_model: dict[str, Any],
        auto_approve: bool = False,
        confidence_threshold: float = 0.8,
    ) -> AdapterConfig | MappingReview:
        """Re-discover API, diff schemas, selectively re-map broken fields.

        Returns AdapterConfig if auto_approve=True and all mappings are confident,
        otherwise returns MappingReview for human review.
        """
        from liquid.events import AdapterRepaired

        new_schema = await self.discover(config.schema_.source_url)
        diff = diff_schemas(config.schema_, new_schema)

        if not diff.has_breaking_changes:
            updated = config.model_copy(update={"schema_": new_schema, "version": config.version + 1})
            if self.event_handler:
                await self.event_handler.handle(
                    AdapterRepaired(
                        adapter_id=config.config_id,
                        diff=diff,
                        auto_approved=True,
                    )
                )
            return updated

        proposals = await self._mapping_proposer.propose(
            new_schema,
            target_model,
            existing_mappings=config.mappings,
            removed_fields=diff.removed_fields,
        )

        review = MappingReview(proposals)

        if auto_approve and all(m.confidence >= confidence_threshold for m in proposals):
            review.approve_all()
            mappings = review.finalize()
            updated = AdapterConfig(
                config_id=config.config_id,
                schema=new_schema,
                auth_ref=config.auth_ref,
                mappings=mappings,
                sync=config.sync,
                verified_by=config.verified_by,
                version=config.version + 1,
            )
            if self.event_handler:
                await self.event_handler.handle(
                    AdapterRepaired(
                        adapter_id=config.config_id,
                        diff=diff,
                        auto_approved=True,
                    )
                )
            return updated

        return review

    async def learn_from_review(
        self,
        schema: APISchema,
        target_model: dict[str, Any],
        review: MappingReview,
    ) -> None:
        """Record corrections from a mapping review for future learning."""
        import json

        corrections = review.corrections()
        if corrections:
            await self._mapping_learner.record_corrections(
                schema.service_name,
                json.dumps(target_model),
                corrections,
            )
