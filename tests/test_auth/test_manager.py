import httpx
import pytest

from liquid.auth.manager import AuthManager
from liquid.exceptions import VaultError
from liquid.models.schema import AuthRequirement, OAuthConfig


class FakeVault:
    def __init__(self):
        self.store_data: dict[str, str] = {}

    async def store(self, key: str, value: str) -> None:
        self.store_data[key] = value

    async def get(self, key: str) -> str:
        if key not in self.store_data:
            raise VaultError(f"Key not found: {key}")
        return self.store_data[key]

    async def delete(self, key: str) -> None:
        self.store_data.pop(key, None)


class TestAuthManager:
    async def test_store_credentials(self):
        vault = FakeVault()
        mgr = AuthManager(vault)
        prefix = await mgr.store_credentials("adapter-1", {"access_token": "tok123", "refresh_token": "ref456"})
        assert prefix == "liquid/adapter-1"
        assert vault.store_data["liquid/adapter-1/access_token"] == "tok123"
        assert vault.store_data["liquid/adapter-1/refresh_token"] == "ref456"

    async def test_get_bearer_headers(self):
        vault = FakeVault()
        vault.store_data["liquid/a1/access_token"] = "my-token"
        mgr = AuthManager(vault)
        headers = await mgr.get_auth_headers(AuthRequirement(type="bearer", tier="A"), "liquid/a1")
        assert headers == {"Authorization": "Bearer my-token"}

    async def test_get_api_key_headers(self):
        vault = FakeVault()
        vault.store_data["liquid/a1/api_key"] = "key-abc"
        mgr = AuthManager(vault)
        headers = await mgr.get_auth_headers(AuthRequirement(type="api_key", tier="C"), "liquid/a1")
        assert headers == {"X-API-Key": "key-abc"}

    async def test_get_basic_headers(self):
        vault = FakeVault()
        vault.store_data["liquid/a1/username"] = "user"
        vault.store_data["liquid/a1/password"] = "pass"
        mgr = AuthManager(vault)
        headers = await mgr.get_auth_headers(AuthRequirement(type="basic", tier="C"), "liquid/a1")
        assert "Basic" in headers["Authorization"]

    async def test_missing_token_raises(self):
        vault = FakeVault()
        mgr = AuthManager(vault)
        with pytest.raises(VaultError):
            await mgr.get_auth_headers(AuthRequirement(type="bearer", tier="A"), "liquid/missing")

    async def test_refresh_oauth_token(self):
        vault = FakeVault()
        vault.store_data.update(
            {
                "liquid/a1/refresh_token": "old-refresh",
                "liquid/a1/client_id": "cid",
                "liquid/a1/client_secret": "csecret",
            }
        )

        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"access_token": "new-tok", "refresh_token": "new-refresh"})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            mgr = AuthManager(vault)
            new_token = await mgr.refresh_oauth_token(
                "liquid/a1",
                OAuthConfig(authorize_url="https://ex.com/auth", token_url="https://ex.com/token"),
                http_client=client,
            )

        assert new_token == "new-tok"
        assert vault.store_data["liquid/a1/access_token"] == "new-tok"
        assert vault.store_data["liquid/a1/refresh_token"] == "new-refresh"

    async def test_delete_credentials(self):
        vault = FakeVault()
        vault.store_data["liquid/a1/access_token"] = "tok"
        vault.store_data["liquid/a1/api_key"] = "key"
        mgr = AuthManager(vault)
        await mgr.delete_credentials("a1")
        assert "liquid/a1/access_token" not in vault.store_data
        assert "liquid/a1/api_key" not in vault.store_data
