"""Cache key generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_cache_key(
    adapter_id: str,
    endpoint_path: str,
    params: dict[str, Any] | None = None,
    method: str = "GET",
) -> str:
    """Compute stable cache key for a request.

    Key is deterministic: same inputs yield the same key.
    Parameters are sorted to ensure order-independence.
    """
    canonical = {
        "adapter": adapter_id,
        "method": method.upper(),
        "path": endpoint_path,
        "params": _canonicalize(params or {}),
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _canonicalize(obj: Any) -> Any:
    """Convert object to canonical form for stable hashing."""
    if isinstance(obj, dict):
        return {k: _canonicalize(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list | tuple):
        return [_canonicalize(x) for x in obj]
    return obj
