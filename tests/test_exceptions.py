import pytest

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


def test_hierarchy_liquid_error_catches_all():
    all_errors = [
        DiscoveryError,
        AuthSetupError,
        MappingError,
        SyncRuntimeError,
        ReDiscoveryNeededError,
        VaultError,
    ]
    for exc_class in all_errors:
        with pytest.raises(LiquidError):
            raise exc_class("test")


def test_sync_runtime_error_catches_subtypes():
    for exc_class in [FieldNotFoundError, AuthError, RateLimitError, ServiceDownError, EndpointGoneError]:
        with pytest.raises(SyncRuntimeError):
            raise exc_class("test")


def test_rate_limit_error_retry_after():
    err = RateLimitError("slow down", retry_after=30.0)
    assert err.retry_after == 30.0
    assert "slow down" in str(err)


def test_rate_limit_error_no_retry_after():
    err = RateLimitError()
    assert err.retry_after is None
