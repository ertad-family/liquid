"""Pluggable auth schemes.

Declarative Pydantic models describe how to sign outgoing requests. Each
scheme produces an ``httpx.Auth`` at fetch time via
:meth:`AuthScheme.build_httpx_auth`, so signing integrates with the standard
httpx request lifecycle (body-aware, redirects, retries).

The schemes are a pure superset of the legacy ``AuthRequirement.type`` dispatch
in :class:`~liquid.auth.manager.AuthManager`; adapters without ``auth_scheme``
keep the old Bearer-only behaviour.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import time
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from liquid.protocols import Vault


class _BaseScheme(BaseModel):
    """Shared config. Schemes are immutable at runtime once built."""

    model_config = {"frozen": True}


class BearerAuth(_BaseScheme):
    """Static bearer token from vault (``{vault_key}/access_token``)."""

    kind: Literal["bearer"] = "bearer"
    token_field: str = "access_token"
    header_name: str = "Authorization"
    header_prefix: str = "Bearer "

    async def build_httpx_auth(self, vault: Vault, vault_key: str) -> httpx.Auth:
        token = await vault.get(f"{vault_key}/{self.token_field}")
        return _StaticHeaderAuth({self.header_name: f"{self.header_prefix}{token}"})


class ApiKeyAuth(_BaseScheme):
    """API key in a named header (or query string)."""

    kind: Literal["api_key"] = "api_key"
    header_name: str = "X-API-Key"
    query_param: str | None = None
    key_field: str = "api_key"
    prefix: str = ""

    async def build_httpx_auth(self, vault: Vault, vault_key: str) -> httpx.Auth:
        key = await vault.get(f"{vault_key}/{self.key_field}")
        value = f"{self.prefix}{key}"
        if self.query_param:
            return _QueryParamAuth(self.query_param, value)
        return _StaticHeaderAuth({self.header_name: value})


class BasicAuth(_BaseScheme):
    """HTTP Basic auth (``username:password`` → base64)."""

    kind: Literal["basic"] = "basic"
    username_field: str = "username"
    password_field: str = "password"

    async def build_httpx_auth(self, vault: Vault, vault_key: str) -> httpx.Auth:
        user = await vault.get(f"{vault_key}/{self.username_field}")
        pw = await vault.get(f"{vault_key}/{self.password_field}")
        encoded = base64.b64encode(f"{user}:{pw}".encode()).decode()
        return _StaticHeaderAuth({"Authorization": f"Basic {encoded}"})


class HMACAuth(_BaseScheme):
    """Generic HMAC request signing.

    The signing string is built by substituting ``{method}``, ``{path}``,
    ``{query}``, ``{body}``, ``{timestamp}`` placeholders inside
    :attr:`signing_template`. Common patterns:

    - Stripe-style webhooks verify: ``"{timestamp}.{body}"``
    - GitHub-style: ``"{body}"``
    - Shopify-style: ``"{body}"`` over raw body with base64 hex
    - Custom header signing: ``"{method}\\n{path}\\n{timestamp}\\n{body}"``
    """

    kind: Literal["hmac"] = "hmac"
    algorithm: Literal["sha256", "sha1", "sha512"] = "sha256"
    header_name: str = "X-Signature"
    header_prefix: str = ""
    signing_template: str = "{method}\n{path}\n{body}"
    timestamp_header: str | None = None
    timestamp_field: str = "timestamp"
    signing_key_field: str = "signing_key"
    output_encoding: Literal["hex", "base64"] = "hex"

    async def build_httpx_auth(self, vault: Vault, vault_key: str) -> httpx.Auth:
        secret = await vault.get(f"{vault_key}/{self.signing_key_field}")
        return _HMACRequestAuth(secret.encode("utf-8"), self)


class AwsSigV4Auth(_BaseScheme):
    """AWS Signature Version 4 signer.

    Signs the request body and headers according to the SigV4 spec. Supports
    S3/DynamoDB/SQS/etc. via the ``service`` and ``region`` fields. For S3 the
    canonical URI must not be URL-encoded twice — pass the path exactly as the
    service expects.
    """

    kind: Literal["aws_sigv4"] = "aws_sigv4"
    region: str
    service: str
    access_key_field: str = "access_key_id"
    secret_key_field: str = "secret_access_key"
    session_token_field: str = "session_token"
    payload_hash_override: str | None = None

    async def build_httpx_auth(self, vault: Vault, vault_key: str) -> httpx.Auth:
        access = await vault.get(f"{vault_key}/{self.access_key_field}")
        secret = await vault.get(f"{vault_key}/{self.secret_key_field}")
        session = None
        try:
            session = await vault.get(f"{vault_key}/{self.session_token_field}")
        except Exception:
            session = None
        return _AwsSigV4RequestAuth(access, secret, session, self)


class OAuth2Auth(_BaseScheme):
    """OAuth2 bearer with automatic refresh on 401.

    On first use, sends ``Bearer {access_token}`` from vault. If the server
    returns 401 **and** :attr:`token_url` is set, attempts a
    ``refresh_token``-grant call, stores the new access token, and retries the
    original request. ``scope`` and ``audience`` are forwarded to the token
    endpoint when provided (audience required by e.g. Auth0).
    """

    kind: Literal["oauth2"] = "oauth2"
    token_url: str | None = None
    grant_type: Literal["refresh_token", "client_credentials"] = "refresh_token"
    scope: str | None = None
    audience: str | None = None
    client_auth_method: Literal["client_secret_post", "client_secret_basic"] = "client_secret_post"
    access_token_field: str = "access_token"
    refresh_token_field: str = "refresh_token"
    client_id_field: str = "client_id"
    client_secret_field: str = "client_secret"

    async def build_httpx_auth(self, vault: Vault, vault_key: str) -> httpx.Auth:
        access = await vault.get(f"{vault_key}/{self.access_token_field}")
        return _OAuth2RequestAuth(vault, vault_key, access, self)


AuthScheme = BearerAuth | ApiKeyAuth | BasicAuth | HMACAuth | AwsSigV4Auth | OAuth2Auth
AuthSchemeField = Field(discriminator="kind")


class _StaticHeaderAuth(httpx.Auth):
    requires_request_body = False

    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        for name, value in self._headers.items():
            request.headers[name] = value
        yield request


class _QueryParamAuth(httpx.Auth):
    requires_request_body = False

    def __init__(self, param: str, value: str) -> None:
        self._param = param
        self._value = value

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        params = dict(request.url.params)
        params[self._param] = self._value
        request.url = request.url.copy_with(params=params)
        yield request


class _HMACRequestAuth(httpx.Auth):
    requires_request_body = True

    def __init__(self, secret: bytes, cfg: HMACAuth) -> None:
        self._secret = secret
        self._cfg = cfg

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        body_bytes: bytes = request.content or b""
        body = body_bytes.decode("utf-8", errors="replace")
        timestamp = str(int(time.time()))
        if self._cfg.timestamp_header:
            request.headers[self._cfg.timestamp_header] = timestamp

        signing_string = self._cfg.signing_template.format(
            method=request.method.upper(),
            path=request.url.raw_path.decode("ascii"),
            query=request.url.query.decode("ascii") if request.url.query else "",
            body=body,
            timestamp=timestamp,
        )
        digest = hmac.new(self._secret, signing_string.encode("utf-8"), getattr(hashlib, self._cfg.algorithm))
        if self._cfg.output_encoding == "hex":
            sig = digest.hexdigest()
        else:
            sig = base64.b64encode(digest.digest()).decode("ascii")
        request.headers[self._cfg.header_name] = f"{self._cfg.header_prefix}{sig}"
        yield request


class _AwsSigV4RequestAuth(httpx.Auth):
    requires_request_body = True

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        session_token: str | None,
        cfg: AwsSigV4Auth,
    ) -> None:
        self._access = access_key
        self._secret = secret_key
        self._session = session_token
        self._cfg = cfg

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        now = _dt.datetime.now(_dt.UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        body: bytes = request.content or b""
        payload_hash = self._cfg.payload_hash_override or hashlib.sha256(body).hexdigest()

        request.headers["x-amz-date"] = amz_date
        request.headers["x-amz-content-sha256"] = payload_hash
        request.headers.setdefault("host", request.url.host)
        if self._session:
            request.headers["x-amz-security-token"] = self._session

        canonical_uri = quote(request.url.path or "/", safe="/-_.~")
        canonical_querystring = _canonical_query(request.url.query.decode("ascii") if request.url.query else "")
        signed_headers_list = ["host", "x-amz-content-sha256", "x-amz-date"]
        if self._session:
            signed_headers_list.append("x-amz-security-token")
        signed_headers_list.sort()
        canonical_headers = "".join(f"{h}:{request.headers[h].strip()}\n" for h in signed_headers_list)
        signed_headers = ";".join(signed_headers_list)

        canonical_request = (
            f"{request.method.upper()}\n"
            f"{canonical_uri}\n"
            f"{canonical_querystring}\n"
            f"{canonical_headers}\n"
            f"{signed_headers}\n"
            f"{payload_hash}"
        )

        credential_scope = f"{date_stamp}/{self._cfg.region}/{self._cfg.service}/aws4_request"
        string_to_sign = (
            f"AWS4-HMAC-SHA256\n{amz_date}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )

        signing_key = _derive_sigv4_key(self._secret, date_stamp, self._cfg.region, self._cfg.service)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

        request.headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._access}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        yield request


def _canonical_query(raw: str) -> str:
    if not raw:
        return ""
    pairs: list[tuple[str, str]] = []
    for part in raw.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        pairs.append((quote(k, safe="-_.~"), quote(v, safe="-_.~")))
    pairs.sort()
    return "&".join(f"{k}={v}" for k, v in pairs)


def _derive_sigv4_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = hmac.new(f"AWS4{secret}".encode(), date_stamp.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


class _OAuth2RequestAuth(httpx.Auth):
    """Attach Bearer token; on 401, try one refresh then retry the original."""

    requires_response_body = True

    def __init__(self, vault: Vault, vault_key: str, access_token: str, cfg: OAuth2Auth) -> None:
        self._vault = vault
        self._vault_key = vault_key
        self._access = access_token
        self._cfg = cfg

    async def async_auth_flow(self, request: httpx.Request) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {self._access}"
        response = yield request
        if response.status_code != 401 or not self._cfg.token_url:
            return

        new_access = await self._refresh()
        if new_access is None:
            return
        self._access = new_access
        request.headers["Authorization"] = f"Bearer {new_access}"
        yield request

    async def _refresh(self) -> str | None:
        data: dict[str, str] = {"grant_type": self._cfg.grant_type}
        if self._cfg.grant_type == "refresh_token":
            data["refresh_token"] = await self._vault.get(f"{self._vault_key}/{self._cfg.refresh_token_field}")
        if self._cfg.scope:
            data["scope"] = self._cfg.scope
        if self._cfg.audience:
            data["audience"] = self._cfg.audience

        client_id = await self._vault.get(f"{self._vault_key}/{self._cfg.client_id_field}")
        client_secret = await self._vault.get(f"{self._vault_key}/{self._cfg.client_secret_field}")

        headers: dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
        if self._cfg.client_auth_method == "client_secret_basic":
            encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        else:
            data["client_id"] = client_id
            data["client_secret"] = client_secret

        async with httpx.AsyncClient() as client:
            resp = await client.post(self._cfg.token_url, content=urlencode(data).encode("ascii"), headers=headers)
        if not resp.is_success:
            return None
        payload = resp.json()
        access = payload.get("access_token")
        if not access:
            return None
        await self._vault.store(f"{self._vault_key}/{self._cfg.access_token_field}", access)
        if "refresh_token" in payload:
            await self._vault.store(f"{self._vault_key}/{self._cfg.refresh_token_field}", payload["refresh_token"])
        return access
