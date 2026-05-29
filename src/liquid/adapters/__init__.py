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

__all__ = ["list_bundled_adapters", "load_bundled_adapter"]


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
