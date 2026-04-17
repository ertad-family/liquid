from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from liquid.sync.quota import QuotaInfo


class LiquidError(Exception):
    """Base exception with optional recovery metadata for agents."""

    def __init__(
        self,
        message: str = "",
        *,
        recovery_hint: str | None = None,
        auto_repair_available: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.recovery_hint = recovery_hint
        self.auto_repair_available = auto_repair_available
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON API responses."""
        return {
            "type": type(self).__name__,
            "message": self.message,
            "recovery_hint": self.recovery_hint,
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
        auto_repair_available: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        # Build hint if not provided
        if recovery_hint is None:
            if retry_after:
                recovery_hint = f"Retry after {retry_after:.0f} seconds"
            elif quota_info and quota_info.reset_in_seconds:
                recovery_hint = f"Quota resets in {quota_info.reset_in_seconds:.0f}s"
            else:
                recovery_hint = "Wait and retry, or check adapter.schema_.rate_limits"

        super().__init__(
            message,
            recovery_hint=recovery_hint,
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
                recovery_hint=hint,
                auto_repair_available=True,
                details=details,
            )
        return cls(
            message,
            recovery_hint="Endpoint removed — run liquid.repair_adapter() to re-discover",
            auto_repair_available=True,
            details=details,
        )


class VaultError(LiquidError):
    pass


class ActionNotVerifiedError(LiquidError):
    pass
