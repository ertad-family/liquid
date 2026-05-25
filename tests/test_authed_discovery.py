"""Tests for authed discovery + enveloped-fetch support.

Covers the building blocks that let Liquid connect to auth-walled, spec-less
APIs (e.g. cloud providers): probe auth headers, envelope/record-path
detection, credential-derived auth schemes, the envelope-aware selector, and
mapping normalization against a discovered record_path.
"""

from __future__ import annotations

from liquid.auth.schemes import ApiKeyAuth, BasicAuth, BearerAuth, scheme_from_credentials
from liquid.client import _identity_fallback_mappings, _normalize_mappings_to_record
from liquid.discovery.utils import (
    build_probe_auth_headers,
    detect_record_envelope,
    schema_from_record,
)
from liquid.models.adapter import FieldMapping
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.sync.selector import EnvelopeSelector


class TestProbeAuthHeaders:
    def test_token_becomes_bearer(self):
        assert build_probe_auth_headers({"token": "abc"}) == {"Authorization": "Bearer abc"}

    def test_access_token_becomes_bearer(self):
        assert build_probe_auth_headers({"access_token": "xyz"}) == {"Authorization": "Bearer xyz"}

    def test_api_key_sets_both_headers(self):
        headers = build_probe_auth_headers({"api_key": "k1"})
        assert headers["X-API-Key"] == "k1"
        assert headers["Authorization"] == "Bearer k1"

    def test_custom_header_field(self):
        assert build_probe_auth_headers({"xi-api-key": "v"}) == {"xi-api-key": "v"}

    def test_basic_from_username_password(self):
        h = build_probe_auth_headers({"username": "u", "password": "p"})
        assert h["Authorization"].startswith("Basic ")

    def test_auth_directive_is_ignored_as_value(self):
        # the reserved ``auth`` key is not treated as a credential value
        assert build_probe_auth_headers({"auth": {"x": 1}, "token": "t"}) == {"Authorization": "Bearer t"}

    def test_empty(self):
        assert build_probe_auth_headers(None) == {}
        assert build_probe_auth_headers({}) == {}


class TestDetectRecordEnvelope:
    def test_named_envelope(self):
        path, rec = detect_record_envelope({"instances": [{"id": 1}], "meta": {"total": 1}})
        assert path == "instances"
        assert rec == {"id": 1}

    def test_known_key_data(self):
        path, _rec = detect_record_envelope({"data": [{"a": 1}]})
        assert path == "data"

    def test_bare_list(self):
        path, rec = detect_record_envelope([{"a": 1}, {"a": 2}])
        assert path is None
        assert rec == {"a": 1}

    def test_single_object(self):
        path, rec = detect_record_envelope({"id": 1, "name": "x"})
        assert path is None
        assert rec == {"id": 1, "name": "x"}

    def test_ambiguous_multiple_lists(self):
        path, _ = detect_record_envelope({"a": [1], "b": [2]})
        assert path is None  # can't disambiguate → treat as single record


class TestSchemaFromRecord:
    def test_types(self):
        schema = schema_from_record({"id": "x", "n": 3, "ok": True, "items": []})
        props = schema["properties"]
        assert props["id"]["type"] == "string"
        assert props["n"]["type"] == "integer"
        assert props["ok"]["type"] == "boolean"
        assert props["items"]["type"] == "array"


class TestSchemeFromCredentials:
    def test_token_to_bearer(self):
        scheme = scheme_from_credentials("bearer", {"token": "t"})
        assert isinstance(scheme, BearerAuth)
        assert scheme.token_field == "token"  # matches store_credentials key

    def test_api_key(self):
        scheme = scheme_from_credentials("api_key", {"api_key": "k"})
        assert isinstance(scheme, ApiKeyAuth)
        assert scheme.key_field == "api_key"

    def test_basic(self):
        scheme = scheme_from_credentials("basic", {"username": "u", "password": "p"})
        assert isinstance(scheme, BasicAuth)

    def test_custom_header(self):
        scheme = scheme_from_credentials("api_key", {"xi-api-key": "k"})
        assert isinstance(scheme, ApiKeyAuth)
        assert scheme.header_name == "xi-api-key"
        assert scheme.key_field == "xi-api-key"

    def test_none(self):
        assert scheme_from_credentials("bearer", None) is None


class TestEnvelopeSelector:
    def test_autodetect_single_list_key(self):
        sel = EnvelopeSelector()
        assert sel.select({"instances": [{"id": 1}], "meta": {}}) == [{"id": 1}]

    def test_known_key(self):
        assert EnvelopeSelector().select({"data": [{"x": 1}]}) == [{"x": 1}]

    def test_explicit_path_wins(self):
        sel = EnvelopeSelector("instances")
        assert sel.select({"instances": [{"id": 1}], "data": [{"id": 99}]}) == [{"id": 1}]

    def test_bare_list(self):
        assert EnvelopeSelector().select([{"a": 1}]) == [{"a": 1}]

    def test_single_object(self):
        assert EnvelopeSelector().select({"id": 1}) == [{"id": 1}]


class TestNormalizeMappings:
    def _schema_with_record_path(self, rp: str | None) -> APISchema:
        return APISchema(
            source_url="https://api.example.com",
            service_name="X",
            discovery_method="rest_heuristic",
            endpoints=[Endpoint(path="/things", method="GET", record_path=rp)],
            auth=AuthRequirement(type="bearer", tier="A"),
        )

    def test_strips_envelope_prefix(self):
        schema = self._schema_with_record_path("instances")
        mappings = [
            FieldMapping(source_path="instances[].id", target_field="id"),
            FieldMapping(source_path="instances[].region.id", target_field="region"),
            FieldMapping(source_path="instances.label", target_field="label"),
        ]
        out = {m.target_field: m.source_path for m in _normalize_mappings_to_record(mappings, schema)}
        assert out == {"id": "id", "region": "region.id", "label": "label"}

    def test_no_record_path_is_noop(self):
        schema = self._schema_with_record_path(None)
        mappings = [FieldMapping(source_path="instances[].id", target_field="id")]
        out = _normalize_mappings_to_record(mappings, schema)
        assert out[0].source_path == "instances[].id"


class TestIdentityFallback:
    def _schema_with_fields(self, fields: list[str]) -> APISchema:
        return APISchema(
            source_url="https://api.example.com",
            service_name="X",
            discovery_method="rest_heuristic",
            endpoints=[
                Endpoint(
                    path="/health",
                    method="GET",
                    response_schema={"type": "object", "properties": {f: {"type": "string"} for f in fields}},
                )
            ],
            auth=AuthRequirement(type="bearer", tier="A"),
        )

    def test_fills_missing_target_fields(self):
        # proposer returned zero mappings (the Apollo case); discovery captured
        # the record's fields → identity mappings fill the gap.
        schema = self._schema_with_fields(["healthy", "is_logged_in"])
        out = _identity_fallback_mappings([], {"healthy": "bool", "is_logged_in": "bool"}, schema)
        assert {(m.source_path, m.target_field) for m in out} == {
            ("healthy", "healthy"),
            ("is_logged_in", "is_logged_in"),
        }

    def test_keeps_existing_and_adds_missing(self):
        # proposer mapped 1 of 2 (the GitHub case) → only the missing one is added.
        schema = self._schema_with_fields(["name", "language"])
        existing = [FieldMapping(source_path="name", target_field="name")]
        out = _identity_fallback_mappings(existing, {"name": "str", "language": "str"}, schema)
        assert {m.target_field for m in out} == {"name", "language"}

    def test_does_not_invent_unknown_fields(self):
        schema = self._schema_with_fields(["name"])
        out = _identity_fallback_mappings([], {"name": "str", "nonexistent": "str"}, schema)
        assert {m.target_field for m in out} == {"name"}  # nonexistent not in record → skipped
