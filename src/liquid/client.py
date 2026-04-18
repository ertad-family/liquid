"""Liquid — the main orchestrator tying all phases together."""

from __future__ import annotations

import json
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
from liquid.exceptions import ActionNotVerifiedError
from liquid.mapping.learning import MappingLearner
from liquid.mapping.proposer import MappingProposer
from liquid.mapping.reviewer import MappingReview
from liquid.models.action import ActionConfig, ActionResult
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema, EndpointKind, SchemaDiff
from liquid.sync.engine import SyncEngine
from liquid.sync.fetcher import Fetcher
from liquid.sync.mapper import RecordMapper

if TYPE_CHECKING:
    from collections.abc import Callable

    from liquid.action.batch import BatchResult
    from liquid.action.reviewer import ActionReview
    from liquid.events import EventHandler
    from liquid.models.response import FetchResponse
    from liquid.models.schema import Endpoint
    from liquid.models.sync import SyncResult
    from liquid.protocols import AdapterRegistry, CacheStore, DataSink, KnowledgeStore, LLMBackend, Vault
    from liquid.sync.quota import QuotaInfo
    from liquid.sync.rate_limiter import RateLimiter
    from liquid.sync.retry import RetryPolicy
    from liquid.telemetry import TelemetryCollector


class Liquid:
    """Main entry point for the Liquid library.

    Connects AI agents to any API: discover → map → fetch.
    Like Zapier, but for AI agents — and the integrations maintain themselves.
    """

    def __init__(
        self,
        llm: LLMBackend,
        vault: Vault,
        sink: DataSink,
        knowledge: KnowledgeStore | None = None,
        registry: AdapterRegistry | None = None,
        event_handler: EventHandler | None = None,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
        cache: CacheStore | None = None,
        rate_limiter: RateLimiter | None = None,
        contribute_telemetry: bool = False,
        telemetry_endpoint: str | None = None,
        normalize_output: bool = False,
        normalize_hints: dict[str, Any] | None = None,
    ) -> None:
        self.llm = llm
        self.vault = vault
        self.sink = sink
        self.knowledge = knowledge
        self.registry = registry
        self.event_handler = event_handler
        self._http_client = http_client
        self._retry_policy = retry_policy
        self.cache = cache
        self.rate_limiter = rate_limiter
        self.normalize_output = normalize_output
        self.normalize_hints = normalize_hints

        self.telemetry: TelemetryCollector | None = None
        if contribute_telemetry:
            from liquid.telemetry import TelemetryCollector

            self.telemetry = TelemetryCollector(
                endpoint=telemetry_endpoint or "https://liquid.ertad.family/v1/telemetry",
            )

        self._auth_classifier = AuthClassifier()
        self._auth_manager = AuthManager(vault)
        self._mapping_proposer = MappingProposer(llm, knowledge)
        self._mapping_learner = MappingLearner(knowledge)

        from liquid.action.proposer import ActionProposer

        self._action_proposer = ActionProposer(llm, knowledge)

    def _maybe_normalize(self, data: Any) -> Any:
        """Apply output normalization when the ``normalize_output`` flag is on.

        No-op otherwise — returns ``data`` unchanged.
        """
        if not self.normalize_output:
            return data
        from liquid.normalize import normalize_response

        return normalize_response(data, hints=self.normalize_hints)

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
        actions: list[ActionConfig] | None = None,
    ) -> AdapterConfig:
        """Phase 3b: Create the final adapter config after human approval."""
        from datetime import UTC, datetime

        return AdapterConfig(
            schema=schema,
            auth_ref=auth_ref,
            mappings=mappings,
            sync=sync_config,
            actions=actions or [],
            verified_by=verified_by,
            verified_at=datetime.now(UTC) if verified_by else None,
        )

    async def _ensure_rate_limit_seeded(
        self,
        config: AdapterConfig,
        endpoint_path: str | None = None,
    ) -> None:
        """Seed rate limiter with known limits on first use.

        Priority:
        1. schema.rate_limits (declared by discovery)
        2. STATIC_KNOWN_LIMITS (hostname match)
        3. CATEGORY_DEFAULTS (fallback)

        Observed response headers still take precedence (seed doesn't overwrite).
        """
        if self.rate_limiter is None:
            return

        from liquid.sync.known_limits import infer_limits

        limits = config.schema_.rate_limits
        if limits is None:
            limits = infer_limits(config.schema_.source_url, category=None)

        key = f"{config.config_id}:{endpoint_path}" if endpoint_path else config.config_id
        await self.rate_limiter.seed(key, limits)

    async def sync(self, config: AdapterConfig, cursor: str | None = None) -> SyncResult:
        """Phase 4: Run a deterministic sync cycle."""
        for ep in config.sync.endpoints:
            await self._ensure_rate_limit_seeded(config, ep)
        client = self._http_client or httpx.AsyncClient()
        try:
            fetcher = Fetcher(
                http_client=client,
                vault=self.vault,
                adapter_id=config.config_id,
                rate_limiter=self.rate_limiter,
                telemetry=self.telemetry,
            )
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

    async def get_or_create(
        self,
        url: str,
        target_model: dict[str, Any],
        credentials: dict[str, Any] | None = None,
        auto_approve: bool = False,
        confidence_threshold: float = 0.8,
        include_actions: bool = False,
        action_model: dict[str, Any] | None = None,
    ) -> AdapterConfig | MappingReview:
        """Connect to a service — reuse existing integration or create a new one.

        This is the primary entry point for AI agents. The agent says
        "I need Shopify data shaped like this model" and Liquid handles the rest:
        - Checks registry for existing integration
        - If found and healthy → returns it
        - If not found → discovers API, proposes mappings, creates adapter
        - If auto_approve=True and confidence is high → returns ready AdapterConfig
        - Otherwise → returns MappingReview for human approval

        Requires registry to be set on the Liquid instance.
        """
        if not self.registry:
            msg = "AdapterRegistry is required for get_or_create(). Pass registry= to Liquid()."
            raise ValueError(msg)

        target_key = json.dumps(target_model, sort_keys=True)

        # Step 1: Exact match (same URL + same model) → free
        existing = await self.registry.get(url, target_key)
        if existing is not None:
            return existing

        # Step 2: Service match (same service, different model) → re-map only
        from liquid.discovery.utils import infer_service_name

        service_hint = infer_service_name(url)
        service_matches = await self.registry.get_by_service(service_hint)
        if service_matches:
            template = service_matches[0]
            proposals = await self._mapping_proposer.propose(template.schema_, target_model)
            review = MappingReview(proposals)
            if auto_approve and all(m.confidence >= confidence_threshold for m in proposals):
                review.approve_all()
                actions = (
                    await self._build_auto_actions(
                        template.schema_,
                        action_model or target_model,
                        review.finalize(),
                        confidence_threshold,
                    )
                    if include_actions
                    else []
                )
                config = AdapterConfig(
                    schema=template.schema_,
                    auth_ref=template.auth_ref,
                    mappings=review.finalize(),
                    sync=SyncConfig(endpoints=[ep.path for ep in template.schema_.endpoints]),
                    actions=actions,
                )
                await self.registry.save(config, target_key)
                return config
            return review

        # Step 3: Full discovery (expensive)
        schema = await self.discover(url)

        if credentials:
            auth_ref = await self.store_credentials(schema.service_name, credentials)
        else:
            auth_ref = f"liquid/{schema.service_name}"

        proposals = await self._mapping_proposer.propose(schema, target_model)
        review = MappingReview(proposals)

        if auto_approve and all(m.confidence >= confidence_threshold for m in proposals):
            review.approve_all()
            actions = (
                await self._build_auto_actions(
                    schema,
                    action_model or target_model,
                    review.finalize(),
                    confidence_threshold,
                )
                if include_actions
                else []
            )
            config = AdapterConfig(
                schema=schema,
                auth_ref=auth_ref,
                mappings=review.finalize(),
                sync=SyncConfig(endpoints=[ep.path for ep in schema.endpoints]),
                actions=actions,
            )
            await self.registry.save(config, target_key)
            return config

        return review

    async def fetch(
        self,
        config: AdapterConfig,
        endpoint: str | None = None,
        cache: int | str | bool | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch data through an adapter — the primary way agents get data.

        If endpoint is None, fetches from the first endpoint in sync config.
        Returns mapped records as plain dicts.

        Cache behavior:
        - cache=False: bypass cache for this call
        - cache=int: use as TTL seconds for this call
        - cache="5m"/"1h"/...: parsed via parse_ttl
        - cache=None: use SyncConfig.cache_ttl default or Cache-Control header
        """
        from liquid.cache.ttl import parse_ttl
        from liquid.discovery.utils import managed_http_client

        ep_path = endpoint or config.sync.endpoints[0]
        target_ep = next((ep for ep in config.schema_.endpoints if ep.path == ep_path), None)
        if target_ep is None:
            msg = f"Endpoint {ep_path} not found in adapter schema"
            raise ValueError(msg)

        await self._ensure_rate_limit_seeded(config, ep_path)

        # Build per-endpoint TTL override map for this call.
        cache_ttl_override: dict[str, int] = dict(config.sync.cache_ttl)
        cache_store: CacheStore | None = self.cache
        if cache is False:
            # Bypass cache entirely for this call.
            cache_store = None
        elif isinstance(cache, int) and not isinstance(cache, bool):
            cache_ttl_override[ep_path] = max(0, cache)
        elif isinstance(cache, str):
            cache_ttl_override[ep_path] = parse_ttl(cache)

        async with managed_http_client(self._http_client) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=self.vault,
                cache=cache_store,
                adapter_id=config.config_id,
                cache_ttl_override=cache_ttl_override,
                rate_limiter=self.rate_limiter,
                telemetry=self.telemetry,
            )
            result = await fetcher.fetch(
                endpoint=target_ep,
                base_url=config.schema_.source_url,
                auth_ref=config.auth_ref,
            )
            mapper = RecordMapper(config.mappings)
            mapped = mapper.map_batch(result.records, ep_path)
            records = [r.mapped_data for r in mapped]
            if self.normalize_output:
                records = [self._maybe_normalize(r) for r in records]
            return records

    async def fetch_with_meta(
        self,
        config: AdapterConfig,
        endpoint: str | None = None,
        *,
        limit: int | None = None,
        head: int | None = None,
        tail: int | None = None,
        fields: list[str] | None = None,
        summary: bool = False,
        max_tokens: int | None = None,
        cache: int | str | bool | None = None,
    ) -> FetchResponse:
        """Fetch with agent-friendly metadata and context-window controls.

        Parameters mirror ``fetch()`` plus:
        - ``limit`` / ``head``: keep only the first N records (``head`` wins if both given)
        - ``tail``: keep only the last N records
        - ``fields``: drop everything except the named top-level fields
        - ``summary``: return aggregate stats instead of records (no items)
        - ``max_tokens``: truncate the record list to fit a rough token budget

        Returns a ``FetchResponse`` with ``items`` + ``meta`` (total, returned,
        truncated flag, estimated tokens, source) and optional ``summary``.
        """
        from liquid.models.response import FetchMeta, FetchResponse
        from liquid.runtime.windowing import (
            apply_limit,
            apply_token_budget,
            build_summary,
            estimate_tokens,
            select_fields,
        )

        records = await self.fetch(config, endpoint, cache=cache)
        total = len(records)

        if summary:
            return FetchResponse(
                items=[],
                summary=build_summary(records),
                meta=FetchMeta(total_items=total, returned_items=0, truncated=False),
            )

        records = select_fields(records, fields)
        records, truncated_by_limit = apply_limit(records, limit=limit, head=head, tail=tail)

        truncated_by_tokens = False
        if max_tokens is not None:
            records, truncated_by_tokens = apply_token_budget(records, max_tokens)

        truncated = truncated_by_limit or truncated_by_tokens

        return FetchResponse(
            items=records,
            meta=FetchMeta(
                total_items=total,
                returned_items=len(records),
                truncated=truncated,
                source="api",
                estimated_tokens=estimate_tokens(records),
            ),
        )

    async def search(
        self,
        config: AdapterConfig,
        endpoint: str | None = None,
        *,
        where: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        limit: int | None = 100,
        sort: str | None = None,  # Future: "field" or "-field" for desc
    ) -> FetchResponse:
        """Search records with query DSL, returning only matches.

        Works with any API — pushes filters to API when supported,
        applies remaining filters locally, always returns matching records.
        """
        from liquid.models.response import FetchMeta, FetchResponse
        from liquid.query.engine import apply_query
        from liquid.query.translator import translate_to_params
        from liquid.runtime.windowing import apply_limit, estimate_tokens, select_fields

        if not where:
            # No filter — behave like fetch_with_meta
            return await self.fetch_with_meta(config, endpoint, fields=fields, limit=limit)

        ep_path = endpoint or config.sync.endpoints[0]
        target_ep = next((ep for ep in config.schema_.endpoints if ep.path == ep_path), None)
        if target_ep is None:
            msg = f"Endpoint {ep_path} not found in adapter schema"
            raise ValueError(msg)

        # Translate: server-side params + local remainder
        _native_params, remaining = translate_to_params(where, target_ep)

        # Fetch (native param translation is a Phase 2 optimization).
        all_records = await self.fetch(config, endpoint)
        total_scanned = len(all_records)

        # Apply local filter
        matching = apply_query(all_records, remaining) if remaining else all_records

        match_count = len(matching)

        # Field selection
        matching = select_fields(matching, fields)

        # Limit
        if limit is not None:
            matching, _ = apply_limit(matching, limit=limit)

        return FetchResponse(
            items=matching,
            meta=FetchMeta(
                total_items=total_scanned,  # Total scanned
                returned_items=len(matching),
                truncated=limit is not None and match_count > limit,
                estimated_tokens=estimate_tokens(matching),
            ),
        )

    async def search_nl(
        self,
        config: AdapterConfig,
        endpoint: str | None = None,
        query: str = "",
        *,
        limit: int | None = 20,
        fields: list[str] | None = None,
    ) -> FetchResponse:
        """Natural language search. LLM translates query -> DSL -> executes."""
        if not self.llm:
            msg = "search_nl requires llm= on Liquid()"
            raise ValueError(msg)

        dsl = await self._nl_to_dsl(config, endpoint, query)
        return await self.search(config, endpoint, where=dsl, limit=limit, fields=fields)

    async def aggregate(
        self,
        adapter: str | AdapterConfig,
        endpoint: str | None = None,
        *,
        group_by: str | list[str] | None = None,
        agg: dict[str, str] | None = None,
        filter: dict[str, Any] | None = None,
        limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Group + aggregate records on an endpoint without pulling them into the agent.

        Fetches pages, applies an optional ``filter`` (Liquid query DSL),
        buckets by ``group_by`` field(s), and returns per-bucket aggregates
        (``count``, ``sum``, ``avg``, ``min``, ``max``, ``first``, ``last``,
        ``distinct``). Stops early when ``limit`` records have been scanned —
        the default cap is ``10_000`` so a misconfigured call can't burn
        through a huge dataset.
        """
        from liquid.query._paginator import _walk_pages
        from liquid.query.aggregate import aggregate_async

        config = await self._resolve_adapter(adapter)
        ep_path = endpoint or config.sync.endpoints[0]

        page_iter = _walk_pages(self, config, ep_path, params=params)
        return await aggregate_async(
            page_iter,
            group_by=group_by,
            agg=agg,
            filter=filter,
            limit=limit,
        )

    async def text_search(
        self,
        adapter: str | AdapterConfig,
        endpoint: str | None = None,
        query: str = "",
        *,
        fields: list[str] | None = None,
        limit: int = 50,
        scan_limit: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Rank records by relevance to a free-text ``query``.

        Walks the endpoint's pages, scores every record with a lightweight
        BM25-style scorer across ``fields`` (or every string field when
        unspecified), and returns the top ``limit`` matches — each with a
        normalized score in ``[0, 1]`` and the list of fields that matched.
        """
        from liquid.query._paginator import _walk_pages
        from liquid.query.text_search import search_async

        config = await self._resolve_adapter(adapter)
        ep_path = endpoint or config.sync.endpoints[0]

        page_iter = _walk_pages(self, config, ep_path, params=params)
        return await search_async(
            page_iter,
            query,
            fields=fields,
            limit=limit,
            scan_limit=scan_limit,
        )

    async def _resolve_adapter(self, adapter: str | AdapterConfig) -> AdapterConfig:
        """Accept either an AdapterConfig or a registered service name."""
        if isinstance(adapter, AdapterConfig):
            return adapter
        if not isinstance(adapter, str):
            raise TypeError(f"adapter must be AdapterConfig or str, got {type(adapter).__name__}")

        if self.registry is None:
            raise ValueError(
                "Resolving adapters by name requires a registry — either pass an AdapterConfig "
                "directly or construct Liquid(registry=...).",
            )

        # Prefer the registry's own service lookup when present.
        if hasattr(self.registry, "get_by_service"):
            matches = await self.registry.get_by_service(adapter)
            if matches:
                return matches[0]

        # Fall back to scanning list_all() for a case-insensitive match.
        if hasattr(self.registry, "list_all"):
            all_configs = await self.registry.list_all()
            name_lower = adapter.lower()
            for cfg in all_configs:
                if cfg.schema_.service_name.lower() == name_lower:
                    return cfg

        raise ValueError(f"No adapter named {adapter!r} is registered")

    async def _nl_to_dsl(
        self,
        config: AdapterConfig,
        endpoint: str | None,
        nl_query: str,
    ) -> dict[str, Any]:
        """Use LLM to translate natural-language query to DSL."""
        from liquid.models.llm import Message

        ep_path = endpoint or config.sync.endpoints[0]
        target_ep = next((ep for ep in config.schema_.endpoints if ep.path == ep_path), None)

        # Build schema summary
        schema_fields: list[str] = []
        if target_ep and target_ep.response_schema:
            props = target_ep.response_schema.get("properties", {})
            if target_ep.response_schema.get("type") == "array":
                items = target_ep.response_schema.get("items", {})
                props = items.get("properties", {}) if isinstance(items, dict) else {}
            schema_fields = list(props.keys())[:20]

        prompt = (
            f"Translate this natural-language query into a Liquid query DSL (MongoDB-style):\n\n"
            f"Query: {nl_query}\n"
            f"Available fields: {schema_fields}\n\n"
            "Operators: $eq, $ne, $gt, $gte, $lt, $lte, $in, $nin, "
            "$contains, $icontains, $startswith, $endswith, $regex, $exists, "
            "$and, $or, $not.\n\n"
            "Respond ONLY with a JSON object (the DSL query). No prose. Example: "
            '{"total_cents": {"$gt": 10000}}'
        )

        response = await self.llm.chat([Message(role="user", content=prompt)])
        text = response.content or "{}"

        # Extract JSON
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            return {}
        try:
            result = json.loads(text[start:end])
        except json.JSONDecodeError:
            return {}
        return result if isinstance(result, dict) else {}

    async def remaining_quota(
        self,
        config: AdapterConfig,
        endpoint: str | None = None,
    ) -> QuotaInfo:
        """Return current rate-limit quota observed for an adapter / endpoint.

        Returns an empty QuotaInfo if no RateLimiter is configured or no
        observations have been recorded yet for this key.
        """
        from liquid.sync.quota import QuotaInfo

        if self.rate_limiter is None:
            return QuotaInfo()
        key = f"{config.config_id}:{endpoint}" if endpoint else config.config_id
        return await self.rate_limiter.quota(key)

    async def invalidate_cache(
        self,
        config: AdapterConfig,
        endpoint: str | None = None,
    ) -> None:
        """Invalidate cache entries for an adapter.

        If endpoint is provided: delete the specific cache key for that endpoint.
        If endpoint is None: no-op (InMemoryCache does not support pattern delete;
        cloud implementations with key scanning may override this behavior).
        """
        if self.cache is None or endpoint is None:
            return

        from liquid.cache.key import compute_cache_key

        target_ep = next((ep for ep in config.schema_.endpoints if ep.path == endpoint), None)
        method = target_ep.method if target_ep is not None else "GET"
        key = compute_cache_key(
            adapter_id=config.config_id,
            endpoint_path=endpoint,
            params={},
            method=method,
        )
        await self.cache.delete(key)

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

        new_schema = await self.discover(config.schema_.source_url)
        diff = diff_schemas(config.schema_, new_schema)

        # Repair action mappings affected by schema changes
        repaired_actions = _repair_actions(config.actions, diff, new_schema)

        if not diff.has_breaking_changes:
            updated = config.model_copy(
                update={
                    "schema_": new_schema,
                    "actions": repaired_actions,
                    "version": config.version + 1,
                }
            )
            await self._emit_repair_event(config.config_id, diff)
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
            updated = AdapterConfig(
                config_id=config.config_id,
                schema=new_schema,
                auth_ref=config.auth_ref,
                mappings=review.finalize(),
                sync=config.sync,
                actions=repaired_actions,
                verified_by=config.verified_by,
                version=config.version + 1,
            )
            await self._emit_repair_event(config.config_id, diff)
            return updated

        return review

    async def _emit_repair_event(self, adapter_id: str, diff: SchemaDiff) -> None:
        if self.event_handler:
            from liquid.events import AdapterRepaired

            await self.event_handler.handle(AdapterRepaired(adapter_id=adapter_id, diff=diff, auto_approved=True))

    async def execute(
        self,
        config: AdapterConfig,
        action_id: str,
        data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Execute a write action by action_id.

        This is the primary way agents WRITE data through Liquid.

        Requires the action to have been verified (verified_by set).
        """
        action = next((a for a in config.actions if a.action_id == action_id), None)
        if action is None:
            msg = f"Action {action_id} not found in adapter config"
            raise ValueError(msg)

        if action.verified_by is None:
            raise ActionNotVerifiedError(
                f"Action {action_id} has not been verified. Call create_adapter() with verified actions to approve."
            )

        await self._ensure_rate_limit_seeded(config, action.endpoint_path)

        from liquid.action.executor import ActionExecutor
        from liquid.discovery.utils import managed_http_client
        from liquid.sync.retry import WRITE_RETRY_DEFAULTS

        async with managed_http_client(self._http_client) as client:
            executor = ActionExecutor(
                http_client=client,
                vault=self.vault,
                retry_policy=self._retry_policy or WRITE_RETRY_DEFAULTS,
                rate_limiter=self.rate_limiter,
                adapter_id=config.config_id,
            )
            result = await executor.execute(
                action=action,
                data=data,
                schema=config.schema_,
                auth_ref=config.auth_ref,
                idempotency_key=idempotency_key,
            )

        if self.normalize_output and result.response_body is not None:
            result = result.model_copy(update={"response_body": self._maybe_normalize(result.response_body)})

        await self._emit_action_event(config.config_id, result)
        return result

    async def execute_intent(
        self,
        config: AdapterConfig,
        intent_name: str,
        data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ActionResult | list[dict[str, Any]]:
        """Execute an intent using its canonical schema.

        Looks up the adapter's binding for this intent, translates the canonical
        input into adapter-specific fields, then executes via :meth:`execute`
        (writes) or :meth:`fetch` (reads).
        """
        from liquid.intent.executor import compile_to_action_data, resolve_intent
        from liquid.intent.registry import get_intent

        # Validate intent exists canonically
        canonical = get_intent(intent_name)
        if canonical is None:
            msg = f"Unknown canonical intent: {intent_name}"
            raise ValueError(msg)

        # Find adapter's binding
        intent_config = resolve_intent(config, intent_name)
        if intent_config is None:
            msg = f"Adapter does not implement intent: {intent_name}"
            raise ValueError(msg)

        # Write intent (has action_id)
        if intent_config.action_id:
            action_data = compile_to_action_data(intent_config, data)
            return await self.execute(
                config,
                intent_config.action_id,
                action_data,
                idempotency_key=idempotency_key,
            )

        # Read intent (has endpoint_path)
        if intent_config.endpoint_path:
            return await self.fetch(config, intent_config.endpoint_path)

        msg = f"Intent config for {intent_name} has neither action_id nor endpoint_path"
        raise ValueError(msg)

    def list_intents(self, config: AdapterConfig) -> list[str]:
        """List canonical intent names this adapter implements."""
        return [ic.intent_name for ic in config.intents]

    async def execute_action(
        self,
        config: AdapterConfig,
        action: str,
        data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Convenience: find action by 'METHOD /path' string and execute.

        Example:
            result = await liquid.execute_action(
                config=shopify_adapter,
                action="POST /orders",
                data={"amount": 99.99},
            )
        """
        parts = action.split(" ", 1)
        if len(parts) != 2:
            msg = f"Action must be in 'METHOD /path' format, got: {action}"
            raise ValueError(msg)
        method, path = parts

        matched = next(
            (a for a in config.actions if a.endpoint_method == method and a.endpoint_path == path),
            None,
        )
        if matched is None:
            msg = f"Action '{action}' not found in adapter config"
            raise ValueError(msg)

        return await self.execute(config, matched.action_id, data, idempotency_key)

    async def execute_batch(
        self,
        config: AdapterConfig,
        action_id: str,
        items: list[dict[str, Any]],
        on_error: str = "continue",
        concurrency: int = 5,
    ) -> BatchResult:
        """Execute a write action for each item in a batch.

        Supports concurrency control and rate-limit-aware scheduling.
        The on_error policy can be "continue" (default) or "abort".
        """
        action = next((a for a in config.actions if a.action_id == action_id), None)
        if action is None:
            msg = f"Action {action_id} not found in adapter config"
            raise ValueError(msg)

        if action.verified_by is None:
            raise ActionNotVerifiedError(
                f"Action {action_id} has not been verified. Call create_adapter() with verified actions to approve."
            )

        await self._ensure_rate_limit_seeded(config, action.endpoint_path)

        from liquid.action.batch import BatchErrorPolicy, BatchExecutor
        from liquid.action.executor import ActionExecutor
        from liquid.discovery.utils import managed_http_client
        from liquid.sync.retry import WRITE_RETRY_DEFAULTS

        error_policy = BatchErrorPolicy(on_error)

        async with managed_http_client(self._http_client) as client:
            executor = ActionExecutor(
                http_client=client,
                vault=self.vault,
                retry_policy=self._retry_policy or WRITE_RETRY_DEFAULTS,
                rate_limiter=self.rate_limiter,
                adapter_id=config.config_id,
            )
            batch_executor = BatchExecutor(
                executor=executor,
                concurrency=concurrency,
                rate_limit=config.schema_.rate_limits,
            )
            result = await batch_executor.execute_batch(
                action=action,
                items=items,
                schema=config.schema_,
                auth_ref=config.auth_ref,
                on_error=error_policy,
            )

        if self.normalize_output:
            normalized_results = [
                r.model_copy(update={"response_body": self._maybe_normalize(r.response_body)})
                if r.response_body is not None
                else r
                for r in result.results
            ]
            result = result.model_copy(update={"results": normalized_results})

        for action_result in result.results:
            await self._emit_action_event(config.config_id, action_result)

        return result

    async def _emit_action_event(self, adapter_id: str, result: ActionResult) -> None:
        if not self.event_handler:
            return
        if result.success:
            from liquid.events import ActionExecuted

            await self.event_handler.handle(
                ActionExecuted(
                    adapter_id=adapter_id,
                    action_id=result.action_id,
                    endpoint_path=result.endpoint_path,
                    method=result.method,
                    success=True,
                    status_code=result.status_code,
                )
            )
        else:
            from liquid.events import ActionFailed

            await self.event_handler.handle(
                ActionFailed(
                    adapter_id=adapter_id,
                    action_id=result.action_id,
                    error=result.error,
                )
            )

    async def propose_actions(
        self,
        schema: APISchema,
        agent_model: dict[str, Any],
        endpoint_filter: Callable[[Endpoint], bool] | None = None,
        existing_read_mappings: list[FieldMapping] | None = None,
    ) -> dict[str, ActionReview]:
        """Propose action mappings for all write endpoints.

        Returns dict of "METHOD /path" -> ActionReview.
        """
        from liquid.action.reviewer import ActionReview as _ActionReview

        results: dict[str, ActionReview] = {}

        for ep in schema.endpoints:
            if endpoint_filter is not None:
                if not endpoint_filter(ep):
                    continue
            elif ep.kind not in (EndpointKind.WRITE, EndpointKind.DELETE):
                continue

            proposals = await self._action_proposer.propose(
                endpoint=ep,
                agent_model=agent_model,
                existing_read_mappings=existing_read_mappings,
            )
            key = f"{ep.method} {ep.path}"
            results[key] = _ActionReview(proposals)

        return results

    async def learn_from_action_review(
        self,
        schema: APISchema,
        agent_model: dict[str, Any],
        reviews: dict[str, ActionReview],
    ) -> None:
        """Record corrections from action reviews for future learning."""
        for key, review in reviews.items():
            corrections = review.corrections()
            if corrections and self.knowledge:
                # Convert ActionMapping corrections to FieldMapping for storage
                field_corrections: list[tuple[FieldMapping, FieldMapping]] = []
                for original, corrected in corrections:
                    field_corrections.append(
                        (
                            FieldMapping(
                                source_path=original.target_path,
                                target_field=original.source_field,
                                transform=original.transform,
                                confidence=original.confidence,
                            ),
                            FieldMapping(
                                source_path=corrected.target_path,
                                target_field=corrected.source_field,
                                transform=corrected.transform,
                                confidence=corrected.confidence,
                            ),
                        )
                    )
                # Parse method and path from key
                parts = key.split(" ", 1)
                if len(parts) == 2:
                    method, path = parts
                    action_key = f"action:{method}:{path}"
                    await self._mapping_learner.record_corrections(
                        action_key,
                        json.dumps(agent_model),
                        field_corrections,
                    )

    async def _build_auto_actions(
        self,
        schema: APISchema,
        agent_model: dict[str, Any],
        read_mappings: list[FieldMapping],
        confidence_threshold: float,
    ) -> list[ActionConfig]:
        """Build ActionConfigs for write endpoints when auto_approve is on."""
        actions: list[ActionConfig] = []
        for ep in schema.endpoints:
            if ep.kind not in (EndpointKind.WRITE, EndpointKind.DELETE):
                continue

            proposals = await self._action_proposer.propose(
                endpoint=ep,
                agent_model=agent_model,
                existing_read_mappings=read_mappings,
            )
            if proposals and all(m.confidence >= confidence_threshold for m in proposals):
                actions.append(
                    ActionConfig(
                        endpoint_path=ep.path,
                        endpoint_method=ep.method,
                        mappings=proposals,
                        verified_by="auto",
                    )
                )
        return actions

    async def learn_from_review(
        self,
        schema: APISchema,
        target_model: dict[str, Any],
        review: MappingReview,
    ) -> None:
        """Record corrections from a mapping review for future learning."""
        corrections = review.corrections()
        if corrections:
            await self._mapping_learner.record_corrections(
                schema.service_name,
                json.dumps(target_model),
                corrections,
            )


def _repair_actions(
    actions: list[ActionConfig],
    diff: SchemaDiff,
    new_schema: APISchema,
) -> list[ActionConfig]:
    """Repair action configs based on schema diff.

    - For removed write endpoints: mark affected actions as unverified (broken)
    - For modified request schemas: reset verification so they get re-reviewed
    """
    if not actions:
        return actions

    removed_paths = set(diff.removed_write_endpoints)
    modified_paths = set(diff.modified_request_schemas)

    repaired: list[ActionConfig] = []
    for action in actions:
        if action.endpoint_path in removed_paths:
            # Endpoint removed — mark as unverified/broken
            repaired.append(action.model_copy(update={"verified_by": None, "verified_at": None}))
        elif action.endpoint_path in modified_paths:
            # Request schema changed — invalidate verification for re-review
            repaired.append(action.model_copy(update={"verified_by": None, "verified_at": None}))
        else:
            repaired.append(action)

    return repaired
