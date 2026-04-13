from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any

import httpx

from liquid.exceptions import AuthError, VaultError

if TYPE_CHECKING:
    from liquid.models.schema import AuthRequirement, OAuthConfig
    from liquid.protocols import Vault

logger = logging.getLogger(__name__)


class AuthManager:
    """Manages credentials storage and auth header generation."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    async def store_credentials(self, adapter_id: str, credentials: dict[str, Any]) -> str:
        """Store credentials in vault with per-adapter isolation. Returns vault key prefix."""
        prefix = f"liquid/{adapter_id}"
        try:
            for key, value in credentials.items():
                await self.vault.store(f"{prefix}/{key}", str(value))
        except Exception as e:
            raise VaultError(f"Failed to store credentials for {adapter_id}: {e}") from e
        return prefix

    async def get_auth_headers(self, auth: AuthRequirement, vault_key: str) -> dict[str, str]:
        """Build HTTP headers for the given auth type."""
        try:
            match auth.type:
                case "bearer" | "oauth2":
                    token = await self.vault.get(f"{vault_key}/access_token")
                    return {"Authorization": f"Bearer {token}"}
                case "api_key":
                    key = await self.vault.get(f"{vault_key}/api_key")
                    return {"X-API-Key": key}
                case "basic":
                    username = await self.vault.get(f"{vault_key}/username")
                    password = await self.vault.get(f"{vault_key}/password")
                    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
                    return {"Authorization": f"Basic {encoded}"}
                case _:
                    token = await self.vault.get(f"{vault_key}/token")
                    return {"Authorization": f"Bearer {token}"}
        except VaultError:
            raise
        except Exception as e:
            raise AuthError(f"Failed to build auth headers: {e}") from e

    async def refresh_oauth_token(
        self,
        vault_key: str,
        oauth_config: OAuthConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> str:
        """Refresh an OAuth2 access token using the stored refresh token."""
        try:
            refresh_token = await self.vault.get(f"{vault_key}/refresh_token")
            client_id = await self.vault.get(f"{vault_key}/client_id")
            client_secret = await self.vault.get(f"{vault_key}/client_secret")
        except Exception as e:
            raise AuthError(f"Missing OAuth credentials for refresh: {e}") from e

        client = http_client or httpx.AsyncClient()
        try:
            resp = await client.post(
                oauth_config.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )

            if not resp.is_success:
                raise AuthError(f"Token refresh failed ({resp.status_code}): {resp.text[:200]}")

            token_data = resp.json()
            new_access_token = token_data["access_token"]
            await self.vault.store(f"{vault_key}/access_token", new_access_token)

            if "refresh_token" in token_data:
                await self.vault.store(f"{vault_key}/refresh_token", token_data["refresh_token"])

            logger.info("OAuth token refreshed for %s", vault_key)
            return new_access_token
        finally:
            if not http_client:
                await client.aclose()

    async def delete_credentials(self, adapter_id: str, keys: list[str] | None = None) -> None:
        """Remove stored credentials for an adapter."""
        prefix = f"liquid/{adapter_id}"
        key_names = keys or ["access_token", "refresh_token", "client_id", "client_secret", "api_key"]
        import contextlib

        for key in key_names:
            with contextlib.suppress(Exception):
                await self.vault.delete(f"{prefix}/{key}")
