"""Tests for write-operation extraction in OpenAPI discovery."""

from liquid.discovery.openapi import OpenAPIDiscovery
from liquid.models.schema import EndpointKind


class TestMethodToKind:
    def test_get_is_read(self):
        assert OpenAPIDiscovery._method_to_kind("get") == EndpointKind.READ

    def test_post_is_write(self):
        assert OpenAPIDiscovery._method_to_kind("post") == EndpointKind.WRITE

    def test_put_is_write(self):
        assert OpenAPIDiscovery._method_to_kind("put") == EndpointKind.WRITE

    def test_patch_is_write(self):
        assert OpenAPIDiscovery._method_to_kind("patch") == EndpointKind.WRITE

    def test_delete_is_delete(self):
        assert OpenAPIDiscovery._method_to_kind("delete") == EndpointKind.DELETE

    def test_unknown_defaults_read(self):
        assert OpenAPIDiscovery._method_to_kind("options") == EndpointKind.READ


class TestExtractRequestSchema:
    def setup_method(self):
        self.discovery = OpenAPIDiscovery()

    def test_v3_request_body(self):
        operation = {
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        }
                    }
                }
            }
        }
        result = self.discovery._extract_request_schema(operation, is_v3=True)
        assert result == {"type": "object", "properties": {"name": {"type": "string"}}}

    def test_v3_no_request_body(self):
        result = self.discovery._extract_request_schema({}, is_v3=True)
        assert result is None

    def test_v2_body_parameter(self):
        operation = {
            "parameters": [
                {
                    "in": "body",
                    "name": "body",
                    "schema": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                    },
                }
            ]
        }
        result = self.discovery._extract_request_schema(operation, is_v3=False)
        assert result["properties"]["email"]["type"] == "string"

    def test_v2_no_body_param(self):
        operation = {"parameters": [{"in": "query", "name": "q"}]}
        result = self.discovery._extract_request_schema(operation, is_v3=False)
        assert result is None


class TestDetectIdempotency:
    def test_known_header(self):
        operation = {
            "parameters": [
                {"in": "header", "name": "Idempotency-Key"},
            ]
        }
        assert OpenAPIDiscovery._detect_idempotency(operation) == "Idempotency-Key"

    def test_shopify_header(self):
        operation = {
            "parameters": [
                {"in": "header", "name": "X-Shopify-Idempotency-Token"},
            ]
        }
        assert OpenAPIDiscovery._detect_idempotency(operation) == "X-Shopify-Idempotency-Token"

    def test_no_idempotency(self):
        operation = {
            "parameters": [
                {"in": "header", "name": "Authorization"},
            ]
        }
        assert OpenAPIDiscovery._detect_idempotency(operation) is None

    def test_no_parameters(self):
        assert OpenAPIDiscovery._detect_idempotency({}) is None


class TestEndpointExtraction:
    def test_full_spec_extracts_write_endpoints(self):
        discovery = OpenAPIDiscovery()
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API"},
            "paths": {
                "/orders": {
                    "get": {
                        "summary": "List orders",
                        "responses": {"200": {"description": "OK"}},
                    },
                    "post": {
                        "summary": "Create order",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"amount": {"type": "number"}},
                                    }
                                }
                            }
                        },
                        "responses": {"201": {"description": "Created"}},
                    },
                },
                "/orders/{id}": {
                    "delete": {
                        "summary": "Delete order",
                        "parameters": [{"in": "path", "name": "id", "required": True}],
                        "responses": {"204": {"description": "Deleted"}},
                    },
                },
            },
        }
        endpoints = discovery._extract_endpoints(spec, is_v3=True)

        get_ep = next(e for e in endpoints if e.method == "GET")
        assert get_ep.kind == EndpointKind.READ
        assert get_ep.request_schema is None

        post_ep = next(e for e in endpoints if e.method == "POST")
        assert post_ep.kind == EndpointKind.WRITE
        assert post_ep.request_schema is not None
        assert "amount" in post_ep.request_schema["properties"]

        delete_ep = next(e for e in endpoints if e.method == "DELETE")
        assert delete_ep.kind == EndpointKind.DELETE
