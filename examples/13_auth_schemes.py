"""0.19 Auth schemes — sign any provider with zero glue code.

Three pluggable signers, all applied to the normal fetch path:

  * ``HMACAuth``      — generic HMAC-SHA256 signing (Stripe webhooks, Shopify, custom)
  * ``AwsSigV4Auth``  — full AWS Signature V4 (S3, DynamoDB, SQS, anything on AWS)
  * ``OAuth2Auth``    — bearer token with automatic refresh on 401 (scope + audience)

The scheme attaches to ``AdapterConfig.auth_scheme``; Liquid's fetcher plugs it
into every request via httpx's ``auth=`` slot — so body-aware signatures and
redirect/retry flows are handled for free.
"""

from __future__ import annotations

import asyncio

import httpx

from liquid.auth.schemes import AwsSigV4Auth, HMACAuth, OAuth2Auth
from liquid.exceptions import VaultError


class InMemoryVault:
    """Minimal dict-backed vault for demo purposes."""

    def __init__(self, data: dict[str, str]) -> None:
        self.data = dict(data)

    async def store(self, key: str, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str:
        if key not in self.data:
            raise VaultError(f"missing: {key}")
        return self.data[key]

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


async def sign_with(scheme, vault: InMemoryVault, vault_key: str, **request_kwargs) -> httpx.Request:
    """Build the scheme's httpx.Auth and capture the outgoing signed request."""
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["req"] = request
        return httpx.Response(200, json={"ok": True})

    auth = await scheme.build_httpx_auth(vault, vault_key)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await client.request(auth=auth, **request_kwargs)
    return captured["req"]


async def main() -> None:
    # 1) HMAC — Stripe-style webhook signature over "{timestamp}.{body}"
    hmac_vault = InMemoryVault({"cred/signing_key": "whsec_demo"})
    hmac_scheme = HMACAuth(
        header_name="Stripe-Signature",
        signing_template="{timestamp}.{body}",
        timestamp_header="Stripe-Timestamp",
    )
    req = await sign_with(
        hmac_scheme,
        hmac_vault,
        "cred",
        method="POST",
        url="https://api.example/webhooks",
        content=b'{"id":"evt_1"}',
    )
    print("=== HMAC ===")
    print(f"  {req.headers['stripe-timestamp']=}")
    print(f"  {req.headers['stripe-signature']=}")

    # 2) AWS SigV4 — sign an S3 GET
    aws_vault = InMemoryVault(
        {
            "cred/access_key_id": "AKIDEXAMPLE",
            "cred/secret_access_key": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        }
    )
    req = await sign_with(
        AwsSigV4Auth(region="us-east-1", service="s3"),
        aws_vault,
        "cred",
        method="GET",
        url="https://examplebucket.s3.amazonaws.com/",
    )
    print("\n=== AWS SigV4 ===")
    print(f"  x-amz-date:       {req.headers['x-amz-date']}")
    print(f"  authorization:    {req.headers['authorization'][:80]}...")

    # 3) OAuth2 — Bearer with Auth0-style audience
    oauth_vault = InMemoryVault(
        {
            "cred/access_token": "eyJ-demo",
            "cred/refresh_token": "rtok",
            "cred/client_id": "cid",
            "cred/client_secret": "csec",
        }
    )
    req = await sign_with(
        OAuth2Auth(
            token_url="https://auth.example/token",
            scope="read:orders write:orders",
            audience="https://api.example",
        ),
        oauth_vault,
        "cred",
        method="GET",
        url="https://api.example/orders",
    )
    print("\n=== OAuth2 ===")
    print(f"  {req.headers['authorization']=}")
    print("  (on 401, the scheme will refresh via token_url and retry automatically)")


if __name__ == "__main__":
    asyncio.run(main())
