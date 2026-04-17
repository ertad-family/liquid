from liquid.cache.ttl import parse_cache_control, parse_ttl


class TestParseTTL:
    def test_none(self):
        assert parse_ttl(None) == 0

    def test_int(self):
        assert parse_ttl(300) == 300

    def test_negative_clamped(self):
        assert parse_ttl(-1) == 0

    def test_seconds(self):
        assert parse_ttl("30s") == 30

    def test_minutes(self):
        assert parse_ttl("5m") == 300

    def test_hours(self):
        assert parse_ttl("1h") == 3600

    def test_days(self):
        assert parse_ttl("1d") == 86400

    def test_bare_number_is_seconds(self):
        assert parse_ttl("60") == 60

    def test_invalid_returns_zero(self):
        assert parse_ttl("invalid") == 0

    def test_whitespace_trimmed(self):
        assert parse_ttl("  5m  ") == 300

    def test_uppercase_unit(self):
        assert parse_ttl("5M") == 300


class TestParseCacheControl:
    def test_none(self):
        assert parse_cache_control(None) is None

    def test_empty(self):
        assert parse_cache_control("") is None

    def test_max_age(self):
        assert parse_cache_control("max-age=300") == 300

    def test_no_store(self):
        assert parse_cache_control("no-store") == 0

    def test_no_cache(self):
        assert parse_cache_control("no-cache, max-age=100") == 0

    def test_public_max_age(self):
        assert parse_cache_control("public, max-age=60") == 60

    def test_max_age_with_spaces(self):
        assert parse_cache_control("max-age = 120") == 120

    def test_missing_directive(self):
        assert parse_cache_control("public") is None
