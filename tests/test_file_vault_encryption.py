"""FileVault encrypts at rest: ciphertext on disk, round-trips, migrates legacy
plaintext, honors LIQUID_VAULT_KEY, and fails clearly on a wrong key."""

from __future__ import annotations

import json

import pytest

from liquid.exceptions import VaultError
from liquid.persistence import FileVault


async def test_stores_ciphertext_not_plaintext(tmp_path, monkeypatch):
    monkeypatch.delenv("LIQUID_VAULT_KEY", raising=False)
    vp = tmp_path / "vault.json"
    v = FileVault(path=vp)
    await v.store("liquid/glama", "AIzaSy-SUPER-SECRET")
    blob = vp.read_text()
    assert "AIzaSy-SUPER-SECRET" not in blob  # secret is not on disk in the clear
    env = json.loads(blob)
    assert env["liquid_vault"] == 2 and "fernet" in env
    assert (tmp_path / "vault.key").exists()  # auto key file, separate from ciphertext
    assert await v.get("liquid/glama") == "AIzaSy-SUPER-SECRET"


async def test_reopen_decrypts_with_same_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("LIQUID_VAULT_KEY", raising=False)
    vp = tmp_path / "vault.json"
    await FileVault(path=vp).store("k", "v")
    v2 = FileVault(path=vp)  # fresh instance, reads key file
    assert await v2.get("k") == "v"


async def test_migrates_legacy_plaintext(tmp_path, monkeypatch):
    monkeypatch.delenv("LIQUID_VAULT_KEY", raising=False)
    vp = tmp_path / "vault.json"
    vp.write_text(json.dumps({"old/key": "legacy-secret"}))  # format-1 plaintext map
    v = FileVault(path=vp)  # load → migrate → re-encrypt
    assert await v.get("old/key") == "legacy-secret"
    blob = vp.read_text()
    assert "legacy-secret" not in blob
    assert json.loads(blob)["liquid_vault"] == 2


async def test_env_key_used_and_no_key_file(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("LIQUID_VAULT_KEY", key)
    vp = tmp_path / "vault.json"
    v = FileVault(path=vp)
    await v.store("k", "v")
    assert not (tmp_path / "vault.key").exists()  # env key → nothing written to disk
    # A new instance with the same env key decrypts; a different key fails.
    assert await FileVault(path=vp).get("k") == "v"
    monkeypatch.setenv("LIQUID_VAULT_KEY", Fernet.generate_key().decode())
    with pytest.raises(VaultError, match="cannot decrypt"):
        FileVault(path=vp)


async def test_invalid_env_key_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("LIQUID_VAULT_KEY", "not-a-valid-fernet-key")
    with pytest.raises(VaultError, match="not a valid Fernet key"):
        FileVault(path=tmp_path / "vault.json")
