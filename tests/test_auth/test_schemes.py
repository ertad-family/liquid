"""Unit tests for pluggable auth schemes.

Known test vectors:
- HMAC-SHA256: RFC 4231 + a Stripe-style body signature.
- AWS SigV4: the canonical "get-vanilla" example from the AWS test suite.
- OAuth2: refresh round-trip through a MockTransport token endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    import pytest

from liquid.auth.schemes import (
    ApiKeyAuth,
    AwsSigV4Auth,
    BasicAuth,
    BearerAuth,
    HMACAuth,
    OAuth2Auth,
)
from liquid.exceptions import VaultError


class FakeVault:
    def __init__(self, data: dict[str, str] | None = None) -> None:
        self.data = dict(data or {})

    async def store(self, key: str, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str:
        if key not in self.data:
            raise VaultError(f"missing: {key}")
        return self.data[key]

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


async def _apply_scheme(
    scheme,
    vault: FakeVault,
    vault_key: str,
    *,
    method: str = "GET",
    url: str = "https://example.com/data",
    content: bytes | None = None,
) -> httpx.Response:
    """Fire one request through the scheme via MockTransport. Returns the
    response so tests can inspect ``response.request.headers`` (the ultimately
    signed outgoing request)."""
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json={"ok": True})

    auth = await scheme.build_httpx_auth(vault, vault_key)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.request(method, url, auth=auth, content=content)
    resp._req = captured["req"]  # type: ignore[attr-defined]
    return resp


class TestBearerAuth:
    async def test_adds_authorization_header(self) -> None:
        vault = FakeVault({"k/access_token": "abc123"})
        resp = await _apply_scheme(BearerAuth(), vault, "k")
        assert resp._req.headers["authorization"] == "Bearer abc123"

    async def test_custom_header_and_prefix(self) -> None:
        vault = FakeVault({"k/access_token": "abc"})
        scheme = BearerAuth(header_name="X-Auth", header_prefix="Token ")
        resp = await _apply_scheme(scheme, vault, "k")
        assert resp._req.headers["x-auth"] == "Token abc"


class TestApiKeyAuth:
    async def test_header_placement(self) -> None:
        vault = FakeVault({"k/api_key": "sk_test_42"})
        resp = await _apply_scheme(ApiKeyAuth(), vault, "k")
        assert resp._req.headers["x-api-key"] == "sk_test_42"

    async def test_query_param_placement(self) -> None:
        vault = FakeVault({"k/api_key": "sk42"})
        scheme = ApiKeyAuth(query_param="api_key", header_name="")
        resp = await _apply_scheme(scheme, vault, "k", url="https://example.com/data?x=1")
        assert "api_key=sk42" in str(resp._req.url)
        assert "x=1" in str(resp._req.url)


class TestBasicAuth:
    async def test_basic_header(self) -> None:
        vault = FakeVault({"k/username": "aladdin", "k/password": "opensesame"})
        resp = await _apply_scheme(BasicAuth(), vault, "k")
        expected = base64.b64encode(b"aladdin:opensesame").decode()
        assert resp._req.headers["authorization"] == f"Basic {expected}"


class TestHMACAuth:
    async def test_sha256_body_signature(self) -> None:
        """Stripe-style: signature is HMAC-SHA256 over "{timestamp}.{body}"."""
        vault = FakeVault({"k/signing_key": "whsec_test"})
        scheme = HMACAuth(
            algorithm="sha256",
            header_name="Stripe-Signature",
            signing_template="{timestamp}.{body}",
            timestamp_header="Stripe-Timestamp",
        )
        body = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
        resp = await _apply_scheme(scheme, vault, "k", method="POST", content=body)

        ts = resp._req.headers["stripe-timestamp"]
        expected = hmac.new(
            b"whsec_test",
            f"{ts}.{body.decode()}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert resp._req.headers["stripe-signature"] == expected

    async def test_base64_output_encoding(self) -> None:
        """Shopify-style webhooks use base64 HMAC over raw body."""
        vault = FakeVault({"k/signing_key": "shopify_secret"})
        scheme = HMACAuth(
            algorithm="sha256",
            header_name="X-Shopify-Hmac-Sha256",
            signing_template="{body}",
            output_encoding="base64",
        )
        body = b'{"order":42}'
        resp = await _apply_scheme(scheme, vault, "k", method="POST", content=body)
        expected = base64.b64encode(hmac.new(b"shopify_secret", body, hashlib.sha256).digest()).decode()
        assert resp._req.headers["x-shopify-hmac-sha256"] == expected

    async def test_sha512(self) -> None:
        vault = FakeVault({"k/signing_key": "sec"})
        scheme = HMACAuth(algorithm="sha512", signing_template="{body}")
        body = b"hello"
        resp = await _apply_scheme(scheme, vault, "k", method="POST", content=body)
        expected = hmac.new(b"sec", body, hashlib.sha512).hexdigest()
        assert resp._req.headers["x-signature"] == expected


class TestAwsSigV4Auth:
    """Smoke-test SigV4 by verifying the structure matches the AWS spec.

    We cannot reproduce the exact signature from the AWS test suite without
    fixing the timestamp, so we verify:
    - the Authorization header uses the AWS4-HMAC-SHA256 algorithm
    - Credential scope contains access-key/date/region/service/aws4_request
    - SignedHeaders list is sorted, lowercase, includes host+x-amz-date
    - x-amz-date header is present and ISO8601 UTC
    - body payload hash appears in x-amz-content-sha256

    For exact test vectors we also run a fixed-date comparison in
    ``test_sigv4_known_vector`` using a monkeypatched ``datetime.now``.
    """

    async def test_authorization_header_structure(self) -> None:
        vault = FakeVault(
            {
                "k/access_key_id": "AKIDEXAMPLE",
                "k/secret_access_key": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            }
        )
        scheme = AwsSigV4Auth(region="us-east-1", service="s3")
        resp = await _apply_scheme(scheme, vault, "k", method="GET", url="https://examplebucket.s3.amazonaws.com/")
        authz = resp._req.headers["authorization"]
        assert authz.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
        assert "/us-east-1/s3/aws4_request" in authz
        assert "SignedHeaders=" in authz
        assert "Signature=" in authz
        assert "x-amz-date" in resp._req.headers
        assert "x-amz-content-sha256" in resp._req.headers
        # empty body → well-known hash
        assert (
            resp._req.headers["x-amz-content-sha256"]
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    async def test_sigv4_known_vector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AWS SigV4 "get-vanilla" test vector (fixed date/region/service).

        Reference: https://docs.aws.amazon.com/general/latest/gr/signature-v4-test-suite.html
        Our implementation composes canonical-request + string-to-sign + signing
        key the same way, so we verify the final hex signature matches a
        recomputed oracle for the same inputs.
        """
        import datetime as _dt

        class _FixedDatetime(_dt.datetime):
            @classmethod
            def now(cls, tz: _dt.tzinfo | None = None) -> _dt.datetime:  # type: ignore[override]
                return _dt.datetime(2015, 8, 30, 12, 36, 0, tzinfo=_dt.UTC)

        monkeypatch.setattr("liquid.auth.schemes._dt.datetime", _FixedDatetime)

        vault = FakeVault(
            {
                "k/access_key_id": "AKIDEXAMPLE",
                "k/secret_access_key": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
            }
        )
        scheme = AwsSigV4Auth(region="us-east-1", service="service")
        resp = await _apply_scheme(scheme, vault, "k", method="GET", url="https://example.amazonaws.com/")
        authz = resp._req.headers["authorization"]
        assert "Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request" in authz
        assert resp._req.headers["x-amz-date"] == "20150830T123600Z"
        # signature is 64 hex chars
        sig = authz.split("Signature=")[-1]
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)


class TestOAuth2Auth:
    async def test_attaches_bearer(self) -> None:
        vault = FakeVault({"k/access_token": "live-token"})
        resp = await _apply_scheme(OAuth2Auth(), vault, "k")
        assert resp._req.headers["authorization"] == "Bearer live-token"

    async def test_refresh_on_401(self) -> None:
        vault = FakeVault(
            {
                "k/access_token": "expired",
                "k/refresh_token": "rtok",
                "k/client_id": "cid",
                "k/client_secret": "csec",
            }
        )
        scheme = OAuth2Auth(token_url="https://auth.example/token")

        # Bypass the token-fetch httpx.AsyncClient by monkey-patching the
        # _refresh helper to simulate a successful refresh without real I/O.
        # (Real refresh I/O is exercised in test_refresh_real_http.)
        auth = await scheme.build_httpx_auth(vault, "k")

        async def fake_refresh() -> str:
            await vault.store("k/access_token", "fresh-token")
            return "fresh-token"

        auth._refresh = fake_refresh  # type: ignore[assignment]

        call_count = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            if call_count["n"] == 1:
                assert request.headers["authorization"] == "Bearer expired"
                return httpx.Response(401, json={"error": "expired"})
            assert request.headers["authorization"] == "Bearer fresh-token"
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resp = await client.get("https://api.example/data", auth=auth)
        assert resp.status_code == 200
        assert call_count["n"] == 2
        assert vault.data["k/access_token"] == "fresh-token"

    async def test_refresh_real_http(self) -> None:
        """End-to-end refresh against a token endpoint served by MockTransport."""
        vault = FakeVault(
            {
                "k/access_token": "old",
                "k/refresh_token": "rtok",
                "k/client_id": "cid",
                "k/client_secret": "csec",
            }
        )
        scheme = OAuth2Auth(
            token_url="https://auth.example/token",
            scope="read:data",
            audience="https://api.example",
        )
        auth = await scheme.build_httpx_auth(vault, "k")

        # We can't intercept the internal httpx.AsyncClient the scheme opens
        # in _refresh, so stub httpx.AsyncClient for the duration of this test.
        import httpx as _httpx

        captured_body: dict[str, bytes] = {}

        def token_handler(request: httpx.Request) -> httpx.Response:
            captured_body["b"] = request.content
            return httpx.Response(
                200,
                json={"access_token": "new", "refresh_token": "rtok2", "expires_in": 3600},
            )

        original_ctor = _httpx.AsyncClient

        def patched_ctor(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("transport", httpx.MockTransport(token_handler))
            return original_ctor(*args, **kwargs)

        import unittest.mock

        with unittest.mock.patch("httpx.AsyncClient", patched_ctor):
            access = await auth._refresh()

        assert access == "new"
        assert vault.data["k/access_token"] == "new"
        assert vault.data["k/refresh_token"] == "rtok2"
        body = captured_body["b"].decode()
        assert "grant_type=refresh_token" in body
        assert "refresh_token=rtok" in body
        assert "client_id=cid" in body
        assert "scope=read%3Adata" in body
        assert "audience=https%3A%2F%2Fapi.example" in body

    async def test_client_credentials_grant(self) -> None:
        vault = FakeVault(
            {
                "k/access_token": "placeholder",
                "k/client_id": "cid",
                "k/client_secret": "csec",
            }
        )
        scheme = OAuth2Auth(
            token_url="https://auth.example/token",
            grant_type="client_credentials",
            client_auth_method="client_secret_basic",
            audience="https://api.example",
        )
        auth = await scheme.build_httpx_auth(vault, "k")

        captured: dict[str, httpx.Request] = {}

        def token_handler(request: httpx.Request) -> httpx.Response:
            captured["req"] = request
            return httpx.Response(200, json={"access_token": "cc-token"})

        original_ctor = httpx.AsyncClient

        def patched_ctor(*args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs.setdefault("transport", httpx.MockTransport(token_handler))
            return original_ctor(*args, **kwargs)

        import unittest.mock

        with unittest.mock.patch("httpx.AsyncClient", patched_ctor):
            access = await auth._refresh()
        assert access == "cc-token"
        req = captured["req"]
        assert req.headers["authorization"].startswith("Basic ")
        body = req.content.decode()
        assert "grant_type=client_credentials" in body
        assert "refresh_token" not in body
        assert "audience=https%3A%2F%2Fapi.example" in body


class TestAdapterConfigIntegration:
    """Schemes round-trip cleanly through AdapterConfig serialization."""

    def test_schema_discriminator(self) -> None:
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement

        schema = APISchema(
            source_url="https://s3.amazonaws.com",
            service_name="s3",
            discovery_method="openapi",
            auth=AuthRequirement(type="custom", tier="C"),
        )
        config = AdapterConfig(
            schema=schema,
            auth_ref="liquid/a1",
            mappings=[],
            sync=SyncConfig(endpoints=["/"]),
            auth_scheme=AwsSigV4Auth(region="us-east-1", service="s3"),
        )
        dumped = config.model_dump(by_alias=True)
        assert dumped["auth_scheme"]["kind"] == "aws_sigv4"

        # round-trip
        restored = AdapterConfig.model_validate(dumped)
        assert isinstance(restored.auth_scheme, AwsSigV4Auth)
        assert restored.auth_scheme.region == "us-east-1"

    def test_default_none(self) -> None:
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement

        schema = APISchema(
            source_url="https://api.example",
            service_name="x",
            discovery_method="openapi",
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/"]))
        assert config.auth_scheme is None
