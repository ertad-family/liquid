"""Reusable OAuth2 token acquisition / refresh, decoupled from any transport.

The HTTP auth flow (:class:`~liquid.auth.schemes._OAuth2RequestAuth`) refreshes a
token reactively inside httpx's ``async_auth_flow``. Non-HTTP transports — IMAP /
SMTP authenticating with the ``XOAUTH2`` SASL mechanism — need the *same* refresh
and vault-storage semantics but outside httpx, before opening a socket. This module
holds that logic once so both paths share it.

Tokens live in the vault under ``{vault_key}/{field}`` (e.g.
``liquid/<adapter>/access_token``), matching how :class:`AuthManager` stores them.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.parse import urlencode

import httpx

if TYPE_CHECKING:
    from liquid.protocols import Vault


@runtime_checkable
class OAuth2Config(Protocol):
    """The OAuth2 knobs the provider reads — satisfied structurally by ``OAuth2Auth``."""

    token_url: str | None
    grant_type: str
    scope: str | None
    audience: str | None
    client_auth_method: str
    access_token_field: str
    refresh_token_field: str
    client_id_field: str
    client_secret_field: str


class OAuth2TokenProvider:
    """Read the current access token and refresh it against the token endpoint.

    Stateless beyond its (vault, key, config) handles: ``access_token`` reads the
    stored token; ``refresh`` performs the grant, persists the new access (and
    rotated refresh) token, and returns it — or ``None`` if the endpoint declined.
    """

    def __init__(self, vault: Vault, vault_key: str, cfg: OAuth2Config) -> None:
        self._vault = vault
        self._vault_key = vault_key
        self._cfg = cfg

    async def access_token(self) -> str | None:
        """The currently stored access token, or ``None`` if absent."""
        try:
            token = await self._vault.get(f"{self._vault_key}/{self._cfg.access_token_field}")
        except Exception:
            return None
        return token or None

    async def refresh(self) -> str | None:
        """Run the configured grant, store the result, and return the new token.

        Returns ``None`` (rather than raising) when no ``token_url`` is configured
        or the endpoint responds non-2xx / without an ``access_token`` — callers
        treat that as "auth still failing" and surface the original error.
        """
        cfg = self._cfg
        if not cfg.token_url:
            return None

        data: dict[str, str] = {"grant_type": cfg.grant_type}
        if cfg.grant_type == "refresh_token":
            data["refresh_token"] = await self._vault.get(f"{self._vault_key}/{cfg.refresh_token_field}")
        if cfg.scope:
            data["scope"] = cfg.scope
        if cfg.audience:
            data["audience"] = cfg.audience

        client_id = await self._vault.get(f"{self._vault_key}/{cfg.client_id_field}")
        client_secret = await self._vault.get(f"{self._vault_key}/{cfg.client_secret_field}")

        headers: dict[str, str] = {"Content-Type": "application/x-www-form-urlencoded"}
        if cfg.client_auth_method == "client_secret_basic":
            encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        else:
            data["client_id"] = client_id
            data["client_secret"] = client_secret

        async with httpx.AsyncClient() as client:
            resp = await client.post(cfg.token_url, content=urlencode(data).encode("ascii"), headers=headers)
        if not resp.is_success:
            return None
        payload = resp.json()
        access = payload.get("access_token")
        if not access:
            return None
        await self._vault.store(f"{self._vault_key}/{cfg.access_token_field}", access)
        if "refresh_token" in payload:
            await self._vault.store(f"{self._vault_key}/{cfg.refresh_token_field}", payload["refresh_token"])
        return access
