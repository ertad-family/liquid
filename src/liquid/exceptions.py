from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from liquid.sync.quota import QuotaInfo


class ToolCall(BaseModel):
    """Suggested tool invocation for recovery.

    Distinct from ``liquid.models.llm.ToolCall`` (an LLM-side tool invocation with id).
    This ``ToolCall`` describes *what an agent should do next* to recover from an error:
    a canonical tool name (e.g. ``"repair_adapter"``, ``"store_credentials"``), the
    arguments it should pass, and a human-readable description.
    """

    tool: str
    """Canonical tool name, e.g. ``"repair_adapter"`` or ``"store_credentials"``."""

    args: dict[str, Any] = Field(default_factory=dict)
    """Arguments the agent should pass when invoking ``tool``."""

    description: str = ""
    """Human-readable explanation of what this call does."""


class Recovery(BaseModel):
    """Structured recovery metadata — agent can execute ``next_action`` directly.

    Replaces the ad-hoc ``recovery_hint: str`` pattern with a machine-executable
    plan. ``hint`` remains available for humans (and for backward compatibility
    with code that reads ``LiquidError.recovery_hint``).
    """

    hint: str
    """Free-text description (backward-compat with legacy ``recovery_hint``)."""

    next_action: ToolCall | None = None
    """Executable action the agent can dispatch without parsing free text."""

    retry_safe: bool = False
    """Whether retrying the failed operation as-is is safe (idempotent)."""

    retry_after_seconds: float | None = None
    """If the retry is time-gated (e.g. 429), how long to wait before retrying."""


class LiquidError(Exception):
    """Base exception with optional structured recovery metadata for agents."""

    def __init__(
        self,
        message: str = "",
        *,
        recovery_hint: str | None = None,
        recovery: Recovery | None = None,
        auto_repair_available: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.recovery = recovery
        # Backward compat: derive hint from recovery if only recovery is set.
        self.recovery_hint = recovery_hint or (recovery.hint if recovery else None)
        # Derive auto_repair_available from next_action if not explicitly set.
        self.auto_repair_available = auto_repair_available or (
            recovery is not None and recovery.next_action is not None
        )
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON API responses."""
        return {
            "type": type(self).__name__,
            "message": self.message,
            "recovery_hint": self.recovery_hint,
            "recovery": self.recovery.model_dump() if self.recovery else None,
            "auto_repair_available": self.auto_repair_available,
            "details": self.details,
        }


class DiscoveryError(LiquidError):
    pass


class AuthSetupError(LiquidError):
    pass


class MappingError(LiquidError):
    pass


class SyncRuntimeError(LiquidError):
    pass


class FieldNotFoundError(SyncRuntimeError):
    pass


class AuthError(SyncRuntimeError):
    pass


class RateLimitError(SyncRuntimeError):
    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: float | None = None,
        *,
        quota_info: QuotaInfo | None = None,
        recovery_hint: str | None = None,
        recovery: Recovery | None = None,
        auto_repair_available: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        # Build hint if not provided (and recovery is not providing one)
        if recovery_hint is None and recovery is None:
            if retry_after:
                recovery_hint = f"Retry after {retry_after:.0f} seconds"
            elif quota_info and quota_info.reset_in_seconds:
                recovery_hint = f"Quota resets in {quota_info.reset_in_seconds:.0f}s"
            else:
                recovery_hint = "Wait and retry, or check adapter.schema_.rate_limits"

        super().__init__(
            message,
            recovery_hint=recovery_hint,
            recovery=recovery,
            auto_repair_available=auto_repair_available,
            details=details,
        )
        self.retry_after = retry_after
        self.quota_info = quota_info


class ServiceDownError(SyncRuntimeError):
    pass


class EndpointGoneError(SyncRuntimeError):
    @classmethod
    def from_response(
        cls,
        message: str,
        suggested_path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> EndpointGoneError:
        """Create with auto-generated recovery hint."""
        if suggested_path:
            hint = f"Try {suggested_path} (endpoint may have moved)"
            return cls(
                message,
                recovery=Recovery(
                    hint=hint,
                    next_action=ToolCall(
                        tool="repair_adapter",
                        description=f"Re-run discovery; suggested replacement path {suggested_path}",
                    ),
                    retry_safe=False,
                ),
                auto_repair_available=True,
                details=details,
            )
        return cls(
            message,
            recovery=Recovery(
                hint="Endpoint removed — run liquid.repair_adapter() to re-discover",
                next_action=ToolCall(
                    tool="repair_adapter",
                    description="Re-run discovery to find new endpoint",
                ),
                retry_safe=False,
            ),
            auto_repair_available=True,
            details=details,
        )


class VaultError(LiquidError):
    pass


class ActionNotVerifiedError(LiquidError):
    pass
