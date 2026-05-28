"""Integration tests against real third-party APIs.

Marked ``integration`` so they're deselected in CI by default (`-m "not
integration"`) and self-skip when the service is unreachable — they're a smoke
test of the real discovery path, not a unit dependency.
"""

import httpx
import pytest

from liquid.discovery.openapi import OpenAPIDiscovery


@pytest.mark.integration
async def test_discover_real_petstore():
    """OpenAPIDiscovery against the live Swagger Petstore returns a valid schema."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            schema = await OpenAPIDiscovery(http_client=client).discover("https://petstore.swagger.io/v2")
    except Exception as e:  # network flakiness shouldn't fail the suite
        pytest.skip(f"petstore.swagger.io unreachable: {e}")

    if schema is None:
        pytest.skip("no OpenAPI spec found at petstore.swagger.io")

    assert schema.discovery_method == "openapi"
    assert schema.service_name  # a non-empty service name was inferred
    assert len(schema.endpoints) > 0
    assert schema.auth is not None
