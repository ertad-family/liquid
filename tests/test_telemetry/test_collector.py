import pytest

from liquid.telemetry.collector import TelemetryCollector


@pytest.mark.asyncio
async def test_collector_buffers():
    collector = TelemetryCollector(endpoint="http://fake", flush_threshold=100)
    await collector.record("https://api.test.com", 200, {}, 10.0)
    assert len(collector._buffer) == 1


@pytest.mark.asyncio
async def test_collector_anonymizes():
    collector = TelemetryCollector(endpoint="http://fake", flush_threshold=100)
    await collector.record(
        "https://api.test.com/path?secret=x",
        200,
        {"Authorization": "Bearer xxx", "X-RateLimit-Limit": "100"},
        10.0,
    )
    event = collector._buffer[0]
    assert event["hostname"] == "api.test.com"
    assert "Authorization" not in event["rate_limit_headers"]
    assert "X-RateLimit-Limit" in event["rate_limit_headers"]
