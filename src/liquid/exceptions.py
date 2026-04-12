from __future__ import annotations


class LiquidError(Exception):
    pass


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
    def __init__(self, message: str = "Rate limit exceeded", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServiceDownError(SyncRuntimeError):
    pass


class EndpointGoneError(SyncRuntimeError):
    pass


class ReDiscoveryNeededError(LiquidError):
    pass


class VaultError(LiquidError):
    pass
