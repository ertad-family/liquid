"""Bundled, community-contributed adapters — pre-discovered & pre-mapped, shipped
in the wheel so popular public APIs work with **zero discovery and zero LLM**.

Each ``*.json`` here is one verified, secret-free adapter (the same portable
artifact ``Liquid`` produces, ``{"target_model", "config"}``). Load one and use it
directly — no setup, no model call:

    from liquid.adapters import load_bundled_adapter
    glama = load_bundled_adapter("glama")
    data = await liquid.fetch(glama)          # deterministic; llm=None is fine

These are **CC0 / public domain** (see ``LICENSE``), independent of the AGPL code —
free to copy, share, and reuse. Contribute one with a PR: connect the API, export
the adapter (``config.model_dump(by_alias=True, mode="json")`` wrapped as
``{"target_model","config"}``), **scrub any secrets** (no real ``auth_ref`` value,
no credentials in ``source_url``), confirm it fetches live, and drop the JSON here.
Only public / well-known APIs — nothing private or auth-walled.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from liquid.models.adapter import AdapterConfig

__all__ = ["BundledAdapterRegistry", "list_bundled_adapters", "load_bundled_adapter"]


def list_bundled_adapters() -> list[str]:
    """Names of the adapters shipped in this package (``glama``, …), sorted."""
    return sorted(p.name[:-5] for p in resources.files(__name__).iterdir() if p.name.endswith(".json"))


def load_bundled_adapter(name: str) -> AdapterConfig:
    """Load a bundled adapter by name into a ready-to-use :class:`AdapterConfig`.

    Raises ``FileNotFoundError`` if no adapter by that name ships here.
    """
    from liquid.models.adapter import AdapterConfig

    res = resources.files(__name__) / f"{name}.json"
    if not res.is_file():
        available = ", ".join(list_bundled_adapters()) or "(none)"
        raise FileNotFoundError(f"No bundled adapter {name!r}. Available: {available}")
    blob = json.loads(res.read_text(encoding="utf-8"))
    return AdapterConfig.model_validate(blob["config"])


class BundledAdapterRegistry:
    """Read-only :class:`~liquid.protocols.AdapterRegistry` over the bundled adapters.

    Lets the wheel's public-domain adapters serve as a resolution **tier** in
    :meth:`Liquid.get_or_create` — consulted before expensive discovery, after the
    user's writable local registry. Writes are no-ops (the set is immutable; new
    adapters arrive via a PR, not at runtime).
    """

    def __init__(self) -> None:
        from liquid.models.adapter import AdapterConfig

        self._configs: dict[str, AdapterConfig] = {}
        self._target: dict[str, str] = {}
        for name in list_bundled_adapters():
            blob = json.loads((resources.files(__name__) / f"{name}.json").read_text(encoding="utf-8"))
            cfg = AdapterConfig.model_validate(blob["config"])
            self._configs[cfg.config_id] = cfg
            self._target[cfg.config_id] = blob.get("target_model", "")

    async def get(self, url: str, target_model: str) -> AdapterConfig | None:
        for cid, cfg in self._configs.items():
            if cfg.schema_.source_url == url and self._target.get(cid) == target_model:
                return cfg
        return None

    async def get_by_service(self, service_name: str) -> list[AdapterConfig]:
        name = service_name.lower()
        return [c for c in self._configs.values() if c.schema_.service_name.lower() == name]

    async def search(self, query: str) -> list[AdapterConfig]:
        q = query.lower()
        return [
            c
            for c in self._configs.values()
            if q in c.schema_.service_name.lower() or q in c.schema_.source_url.lower()
        ]

    async def list_all(self) -> list[AdapterConfig]:
        return list(self._configs.values())

    async def save(self, config: AdapterConfig, target_model: str) -> None:
        # Immutable tier — contributions arrive via PR, not at runtime.
        return

    async def delete(self, config_id: str) -> None:
        return
