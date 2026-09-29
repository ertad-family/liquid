import pytest

from liquid.discovery.can import CANDiscovery


@pytest.mark.asyncio
async def test_can_discovery_builds_generic_frame_endpoint():
    schema = await CANDiscovery().discover("can://vcan0")

    assert schema is not None
    assert schema.discovery_method == "can"
    assert schema.service_name == "vcan0"

    assert len(schema.endpoints) == 1

    endpoint = schema.endpoints[0]

    assert endpoint.path == "/frames"
    assert endpoint.protocol == "can"
    assert endpoint.transport_meta["channel"] == "vcan0"
    assert endpoint.transport_meta["interface"] == "socketcan"

    properties = endpoint.response_schema["properties"]

    assert "arbitration_id" in properties
    assert "data" in properties
    assert "timestamp" in properties


@pytest.mark.asyncio
async def test_can_discovery_ignores_other_protocols():
    schema = await CANDiscovery().discover("https://example.com")

    assert schema is None


@pytest.mark.asyncio
async def test_can_discovery_rejects_missing_channel():
    schema = await CANDiscovery().discover("can://")

    assert schema is None
