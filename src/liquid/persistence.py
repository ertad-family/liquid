"""File-backed Vault and AdapterRegistry for self-hosted single-node use.

The in-memory defaults lose everything on restart — fine for tests, useless for
a long-running self-hosted MCP server. These persist to a directory (default
``~/.liquid``) so connected adapters and stored credentials survive restarts.

``FileVault`` stores credentials **encrypted at rest** (Fernet / AES-128-CBC +
HMAC). The encryption key comes from ``LIQUID_VAULT_KEY`` (a Fernet key — best
practice: inject it from a secret manager so no key ever touches disk); if unset,
a key is generated once into a separate ``vault.key`` file (``0600``) next to the
vault. So a leaked/copied/committed ``vault.json`` alone reveals nothing without
the key. Override locations with ``LIQUID_HOME`` / ``LIQUID_VAULT_PATH`` /
``LIQUID_VAULT_KEY_PATH`` / ``LIQUID_ADAPTERS_DIR``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path

from liquid.exceptions import VaultError
from liquid.models.adapter import AdapterConfig

logger = logging.getLogger(__name__)

_VAULT_FORMAT = 2  # encrypted envelope; format 1 (legacy) was a plaintext str->str map


def _liquid_home() -> Path:
    return Path(os.environ.get("LIQUID_HOME") or (Path.home() / ".liquid"))


class FileVault:
    """Encrypted-at-rest JSON vault (Fernet). Single-node, key from env or key file.

    On disk ``vault.json`` is an envelope ``{"liquid_vault": 2, "fernet": "<token>"}``
    whose token encrypts the secret map. A legacy plaintext vault (format 1) is read
    transparently and re-written encrypted on the next write (auto-migration).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("LIQUID_VAULT_PATH") or (_liquid_home() / "vault.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet = self._build_fernet()
        self._data: dict[str, str] = {}
        self._needs_migration = False
        if self.path.exists():
            self._load()

    def _build_fernet(self):
        from cryptography.fernet import Fernet

        env_key = os.environ.get("LIQUID_VAULT_KEY")
        if env_key:
            try:
                return Fernet(env_key.encode() if isinstance(env_key, str) else env_key)
            except Exception as e:  # invalid key material
                raise VaultError(
                    "LIQUID_VAULT_KEY is not a valid Fernet key. Generate one with "
                    '`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.'
                ) from e
        # No env key → use (or create) a sibling key file, kept separate from the ciphertext.
        key_path = Path(os.environ.get("LIQUID_VAULT_KEY_PATH") or (self.path.parent / "vault.key"))
        if key_path.exists():
            key = key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            key_path.write_bytes(key)
            with contextlib.suppress(OSError):
                os.chmod(key_path, 0o600)
            logger.info("FileVault: generated a new encryption key at %s (0600). Back it up.", key_path)
        return Fernet(key)

    def _load(self) -> None:
        from cryptography.fernet import InvalidToken

        try:
            raw = json.loads(self.path.read_text() or "{}")
        except (ValueError, OSError) as e:
            logger.warning("FileVault: could not read %s (%s); starting empty", self.path, e)
            return
        if isinstance(raw, dict) and raw.get("liquid_vault") == _VAULT_FORMAT and "fernet" in raw:
            try:
                self._data = json.loads(self._fernet.decrypt(raw["fernet"].encode()).decode())
            except (InvalidToken, ValueError) as e:
                raise VaultError(
                    f"FileVault: cannot decrypt {self.path} — the key does not match this vault. "
                    "Check LIQUID_VAULT_KEY / vault.key."
                ) from e
        elif isinstance(raw, dict):
            # Legacy plaintext vault (format 1): adopt, then re-encrypt on next write.
            self._data = {k: v for k, v in raw.items() if isinstance(v, str)}
            self._needs_migration = True
            logger.warning("FileVault: migrating legacy plaintext %s to encrypted at rest.", self.path)
            self._flush()  # rewrite encrypted immediately

    async def store(self, key: str, value: str) -> None:
        self._data[key] = value
        self._flush()

    async def get(self, key: str) -> str:
        if key not in self._data:
            raise VaultError(f"Key not found: {key}")
        return self._data[key]

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._flush()

    def _flush(self) -> None:
        token = self._fernet.encrypt(json.dumps(self._data).encode()).decode()
        self.path.write_text(json.dumps({"liquid_vault": _VAULT_FORMAT, "fernet": token}))
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
        self._needs_migration = False


class FileAdapterRegistry:
    """Adapter registry persisted as one JSON file per adapter in a directory."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.dir = Path(directory or os.environ.get("LIQUID_ADAPTERS_DIR") or (_liquid_home() / "adapters"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self._by_id: dict[str, AdapterConfig] = {}
        self._target: dict[str, str] = {}
        for f in self.dir.glob("*.json"):
            try:
                blob = json.loads(f.read_text())
                cfg = AdapterConfig.model_validate(blob["config"])
                self._by_id[cfg.config_id] = cfg
                self._target[cfg.config_id] = blob.get("target_model", "")
            except (ValueError, OSError, KeyError) as e:
                logger.warning("FileAdapterRegistry: skipping %s (%s)", f, e)

    async def get(self, url: str, target_model: str) -> AdapterConfig | None:
        for cid, cfg in self._by_id.items():
            if cfg.schema_.source_url == url and self._target.get(cid) == target_model:
                return cfg
        return None

    async def search(self, query: str) -> list[AdapterConfig]:
        q = query.lower()
        return [
            c for c in self._by_id.values() if q in c.schema_.service_name.lower() or q in c.schema_.source_url.lower()
        ]

    async def get_by_service(self, service_name: str) -> list[AdapterConfig]:
        name = service_name.lower()
        return [c for c in self._by_id.values() if c.schema_.service_name.lower() == name]

    async def save(self, config: AdapterConfig, target_model: str) -> None:
        self._by_id[config.config_id] = config
        self._target[config.config_id] = target_model
        blob = {"config": config.model_dump(by_alias=True, mode="json"), "target_model": target_model}
        (self.dir / f"{config.config_id}.json").write_text(json.dumps(blob))

    async def list_all(self) -> list[AdapterConfig]:
        return list(self._by_id.values())

    async def delete(self, config_id: str) -> None:
        self._by_id.pop(config_id, None)
        self._target.pop(config_id, None)
        p = self.dir / f"{config_id}.json"
        if p.exists():
            p.unlink()
