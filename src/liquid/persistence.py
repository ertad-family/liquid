"""File-backed Vault and AdapterRegistry for self-hosted single-node use.

The in-memory defaults lose everything on restart — fine for tests, useless for
a long-running self-hosted MCP server. These persist to a directory (default
``~/.liquid``) so connected adapters and stored credentials survive restarts.

``FileVault`` writes a JSON file with ``0600`` permissions. It is **not
encrypted at rest** — keep the file on a trusted host (or point a production
deployment at an encrypted vault). Override the location with
``LIQUID_HOME`` / ``LIQUID_VAULT_PATH`` / ``LIQUID_ADAPTERS_DIR``.
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


def _liquid_home() -> Path:
    return Path(os.environ.get("LIQUID_HOME") or (Path.home() / ".liquid"))


class FileVault:
    """JSON-file vault (0600 perms). Single-node, not encrypted at rest."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("LIQUID_VAULT_PATH") or (_liquid_home() / "vault.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, str] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text() or "{}")
            except (ValueError, OSError) as e:
                logger.warning("FileVault: could not read %s (%s); starting empty", self.path, e)
        else:
            logger.warning("FileVault stores secrets unencrypted at %s (0600). Use a trusted host.", self.path)

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
        self.path.write_text(json.dumps(self._data))
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)


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
