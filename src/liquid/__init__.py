"""Liquid — AI discovers APIs. Code syncs data. No adapters to write."""

__version__ = "0.1.0"

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
    ReDiscoveryNeededError,
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
from liquid.protocols import DataSink, KnowledgeStore, LLMBackend, Vault

__all__ = [
    "APISchema",
    "AdapterConfig",
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
    "ReDiscoveryNeededError",
    "ServiceDownError",
    "SyncConfig",
    "SyncResult",
    "SyncRuntimeError",
    "Vault",
    "VaultError",
]
