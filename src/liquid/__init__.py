"""Liquid — Zapier for AI agents. Connect to any API on the fly."""

__version__ = "0.9.0"

from liquid.cache import InMemoryCache
from liquid.client import Liquid
from liquid.exceptions import (
    ActionNotVerifiedError,
    AuthError,
    AuthSetupError,
    DiscoveryError,
    EndpointGoneError,
    FieldNotFoundError,
    LiquidError,
    MappingError,
    RateLimitError,
    ServiceDownError,
    SyncRuntimeError,
    VaultError,
)
from liquid.models import (
    ActionConfig,
    ActionError,
    ActionErrorType,
    ActionMapping,
    ActionResult,
    AdapterConfig,
    APISchema,
    AuthRequirement,
    BatchErrorPolicy,
    BatchResult,
    DeliveryResult,
    Endpoint,
    EndpointKind,
    FieldMapping,
    MappedRecord,
    SyncConfig,
    SyncResult,
)
from liquid.models.response import FetchMeta, FetchResponse
from liquid.protocols import AdapterRegistry, CacheStore, DataSink, KnowledgeStore, LLMBackend, Vault
from liquid.sync.known_limits import infer_limits, lookup_known_limits
from liquid.sync.quota import QuotaInfo
from liquid.sync.rate_limiter import RateLimiter
from liquid.tools import adapter_to_tools

__all__ = [
    "APISchema",
    "ActionConfig",
    "ActionError",
    "ActionErrorType",
    "ActionMapping",
    "ActionNotVerifiedError",
    "ActionResult",
    "AdapterConfig",
    "AdapterRegistry",
    "AuthError",
    "AuthRequirement",
    "AuthSetupError",
    "BatchErrorPolicy",
    "BatchResult",
    "CacheStore",
    "DataSink",
    "DeliveryResult",
    "DiscoveryError",
    "Endpoint",
    "EndpointGoneError",
    "EndpointKind",
    "FetchMeta",
    "FetchResponse",
    "FieldMapping",
    "FieldNotFoundError",
    "InMemoryCache",
    "KnowledgeStore",
    "LLMBackend",
    "Liquid",
    "LiquidError",
    "MappedRecord",
    "MappingError",
    "QuotaInfo",
    "RateLimitError",
    "RateLimiter",
    "ServiceDownError",
    "SyncConfig",
    "SyncResult",
    "SyncRuntimeError",
    "Vault",
    "VaultError",
    "adapter_to_tools",
    "infer_limits",
    "lookup_known_limits",
]
