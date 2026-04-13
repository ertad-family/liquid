from liquid.models.action import ActionConfig, ActionError, ActionErrorType, ActionMapping, ActionResult
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import DeliveryResult, LLMResponse, MappedRecord, Message, Tool, ToolCall
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    OAuthConfig,
    PaginationType,
    Parameter,
    ParameterLocation,
    RateLimits,
)
from liquid.models.sync import SyncError, SyncErrorType, SyncResult


# Lazy re-export of batch types to avoid circular import
# (action.batch -> action.__init__ -> action.builder -> sync -> models)
def __getattr__(name: str):
    if name in ("BatchErrorPolicy", "BatchResult"):
        from liquid.action.batch import BatchErrorPolicy, BatchResult

        globals()["BatchErrorPolicy"] = BatchErrorPolicy
        globals()["BatchResult"] = BatchResult
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "APISchema",
    "ActionConfig",
    "ActionError",
    "ActionErrorType",
    "ActionMapping",
    "ActionResult",
    "AdapterConfig",
    "AuthRequirement",
    "BatchErrorPolicy",
    "BatchResult",
    "DeliveryResult",
    "Endpoint",
    "EndpointKind",
    "FieldMapping",
    "LLMResponse",
    "MappedRecord",
    "Message",
    "OAuthConfig",
    "PaginationType",
    "Parameter",
    "ParameterLocation",
    "RateLimits",
    "SyncConfig",
    "SyncError",
    "SyncErrorType",
    "SyncResult",
    "Tool",
    "ToolCall",
]
