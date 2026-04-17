from liquid.telemetry.anonymize import ALLOWED_HEADERS, anonymize_event, extract_hostname


def test_extract_hostname_strips_path():
    assert extract_hostname("https://api.stripe.com/v1/charges?foo=bar") == "api.stripe.com"


def test_extract_hostname_case_insensitive():
    assert extract_hostname("https://API.STRIPE.COM/v1") == "api.stripe.com"


def test_anonymize_drops_non_whitelisted_headers():
    event = anonymize_event(
        url="https://api.stripe.com/v1/charges?id=123",
        status_code=200,
        headers={
            "X-RateLimit-Limit": "100",
            "Authorization": "Bearer secret",
            "Cookie": "session=xyz",
        },
        response_time_ms=42.3,
        timestamp_iso="2026-04-17T00:00:00Z",
    )
    # Allowed
    assert "X-RateLimit-Limit" in event["rate_limit_headers"]
    # Forbidden
    assert "Authorization" not in event["rate_limit_headers"]
    assert "Cookie" not in event["rate_limit_headers"]


def test_anonymize_strips_query_params():
    event = anonymize_event(
        url="https://api.example.com/v1/things?secret=abc",
        status_code=200,
        headers={},
        response_time_ms=10.0,
        timestamp_iso="2026-04-17T00:00:00Z",
    )
    assert event["hostname"] == "api.example.com"
    # hostname shouldn't contain query
    assert "secret" not in event["hostname"]


def test_anonymize_preserves_standard_fields():
    event = anonymize_event(
        url="https://api.test.com",
        status_code=429,
        headers={"Retry-After": "60"},
        response_time_ms=100.5,
        timestamp_iso="2026-04-17T00:00:00Z",
    )
    assert event["status_code"] == 429
    assert event["rate_limit_headers"]["Retry-After"] == "60"
    assert event["response_time_ms"] == 100.5


def test_allowed_headers_contains_standard_names():
    # Sanity check that the whitelist has the common rate-limit headers.
    assert "x-ratelimit-limit" in ALLOWED_HEADERS
    assert "retry-after" in ALLOWED_HEADERS
