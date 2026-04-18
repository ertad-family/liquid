from __future__ import annotations

from datetime import UTC, datetime

from liquid.normalize import normalize_datetime


class TestISO8601:
    def test_iso_with_tz(self):
        dt = normalize_datetime("2024-01-15T12:30:45+00:00")
        assert dt == datetime(2024, 1, 15, 12, 30, 45, tzinfo=UTC)

    def test_iso_z_suffix(self):
        dt = normalize_datetime("2024-01-15T12:30:45Z")
        assert dt == datetime(2024, 1, 15, 12, 30, 45, tzinfo=UTC)

    def test_iso_no_tz_assumed_utc(self):
        dt = normalize_datetime("2024-01-15T12:30:45")
        assert dt == datetime(2024, 1, 15, 12, 30, 45, tzinfo=UTC)

    def test_iso_with_microseconds(self):
        dt = normalize_datetime("2024-01-15T12:30:45.123456Z")
        assert dt == datetime(2024, 1, 15, 12, 30, 45, 123456, tzinfo=UTC)

    def test_iso_non_utc_offset_converted(self):
        dt = normalize_datetime("2024-01-15T14:30:45+02:00")
        assert dt == datetime(2024, 1, 15, 12, 30, 45, tzinfo=UTC)

    def test_iso_date_only(self):
        dt = normalize_datetime("2024-01-15")
        assert dt == datetime(2024, 1, 15, tzinfo=UTC)


class TestUnixTimestamps:
    def test_unix_seconds_int(self):
        dt = normalize_datetime(1_705_320_645)
        assert dt is not None
        assert dt.tzinfo is UTC
        assert dt.year == 2024

    def test_unix_milliseconds(self):
        # Should detect ms (>= 10^12).
        dt = normalize_datetime(1_705_320_645_000)
        assert dt is not None
        assert dt.year == 2024
        assert dt.tzinfo is UTC

    def test_unix_numeric_string(self):
        dt = normalize_datetime("1705320645")
        assert dt is not None
        assert dt.year == 2024

    def test_unix_float_seconds(self):
        dt = normalize_datetime(1_705_320_645.5)
        assert dt is not None
        assert dt.microsecond == 500_000


class TestRFC2822:
    def test_rfc_2822_with_tz(self):
        dt = normalize_datetime("Mon, 15 Jan 2024 12:30:45 +0000")
        assert dt == datetime(2024, 1, 15, 12, 30, 45, tzinfo=UTC)

    def test_rfc_2822_gmt(self):
        dt = normalize_datetime("Mon, 15 Jan 2024 12:30:45 GMT")
        assert dt is not None
        assert dt.tzinfo is UTC
        assert dt.year == 2024


class TestBadInput:
    def test_none(self):
        assert normalize_datetime(None) is None

    def test_empty_string(self):
        assert normalize_datetime("") is None

    def test_garbage(self):
        assert normalize_datetime("not a date") is None

    def test_bool(self):
        assert normalize_datetime(True) is None

    def test_dict(self):
        assert normalize_datetime({"year": 2024}) is None


class TestDatetimePassthrough:
    def test_aware_datetime(self):
        original = datetime(2024, 1, 15, 12, 30, 45, tzinfo=UTC)
        assert normalize_datetime(original) == original

    def test_naive_datetime_becomes_utc(self):
        naive = datetime(2024, 1, 15, 12, 30, 45)
        dt = normalize_datetime(naive)
        assert dt is not None
        assert dt.tzinfo is UTC
        assert dt.hour == 12
