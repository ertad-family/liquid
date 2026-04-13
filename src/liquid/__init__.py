"""Liquid — Zapier for AI agents. Connect to any API on the fly."""

__version__ = "0.4.0"

from liquid.client import Liquid
from liquid.exceptions import (
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
    AdapterConfig,
    APISchema,
    AuthRequirement,
    DeliveryResult,
    Endpoint,
    FieldMapping,
    MappedRecord,
    SyncConfig,
    SyncResult,
)
from liquid.protocols import AdapterRegistry, DataSink, KnowledgeStore, LLMBackend, Vault

__all__ = [
    "APISchema",
    "AdapterConfig",
    "AdapterRegistry",
    "AuthError",
    "AuthRequirement",
    "AuthSetupError",
    "DataSink",
    "DeliveryResult",
    "DiscoveryError",
    "Endpoint",
    "EndpointGoneError",
    "FieldMapping",
    "FieldNotFoundError",
    "KnowledgeStore",
    "LLMBackend",
    "Liquid",
    "LiquidError",
    "MappedRecord",
    "MappingError",
    "RateLimitError",
    "ServiceDownError",
    "SyncConfig",
    "SyncResult",
    "SyncRuntimeError",
    "Vault",
    "VaultError",
]
