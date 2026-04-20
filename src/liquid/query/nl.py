"""Natural language -> query DSL compiler with LRU cache.

Agents type "orders over $100 from last week"; Liquid compiles it to a
query DSL dict and feeds the existing :func:`liquid.query.engine.apply_query`
pipeline. LLM calls are cached by (adapter, endpoint, query text, schema
fingerprint) so repeat compilations are free.

The cache is intentionally simple — an in-process LRU with a 1000-entry
cap and a one-week TTL. Persistence is out of scope; the cache warms up
again on cold start.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from liquid.exceptions import LiquidError, Recovery

if TYPE_CHECKING:
    from liquid.protocols import LLMBackend

__all__ = [
    "CACHE_CAPACITY",
    "CACHE_TTL_SECONDS",
    "NLCompilationCache",
    "NLCompileError",
    "build_cache_key",
    "build_prompt",
    "compile_nl_to_dsl",
    "extract_dsl_from_text",
    "schema_fingerprint",
]


CACHE_CAPACITY = 1000
CACHE_TTL_SECONDS = 60 * 60 * 24 * 7  # 1 week


class NLCompileError(LiquidError):
    """Raised when the LLM output is not valid query DSL."""


class NLCompilationCache:
    """Bounded TTL LRU keyed by (adapter, endpoint, query, schema fingerprint).

    Thread-safety note: Liquid itself is async-single-threaded per instance,
    but multiple instances or framework-side runners can share the global
    cache. Insertions / reads are O(1) and the worst case on contention is
    a double compile — we eat it rather than take a lock.
    """

    def __init__(self, capacity: int = CACHE_CAPACITY, ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._entries: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        dsl, expires_at = entry
        if expires_at < time.time():
            # Expired — drop and miss.
            self._entries.pop(key, None)
            return None
        # Promote most-recently-used.
        self._entries.move_to_end(key)
        return dict(dsl)  # return a copy so callers can't mutate cache state

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._entries[key] = (dict(value), time.time() + self.ttl_seconds)
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


# Module-level default cache — callers wanting isolation can construct their
# own :class:`NLCompilationCache` and pass it explicitly.
_DEFAULT_CACHE = NLCompilationCache()


def schema_fingerprint(fields: list[str]) -> str:
    """Stable 12-char fingerprint over a sorted list of field names."""
    payload = json.dumps(sorted(set(fields)), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def build_cache_key(adapter_id: str, endpoint: str, query: str, fingerprint: str) -> str:
    return f"{adapter_id}::{endpoint}::{fingerprint}::{query.strip().lower()}"


def build_prompt(query: str, endpoint: str, fields: list[str]) -> str:
    """Render the system prompt handed to the LLM."""
    field_list = ", ".join(sorted(fields)[:40]) if fields else "(unknown — infer reasonable field names)"
    return (
        "Translate this natural-language query to a MongoDB-style query DSL for "
        f"endpoint {endpoint}.\n"
        f"Schema fields: {field_list}\n"
        f"Query: {query}\n\n"
        "Operators: $eq, $ne, $gt, $gte, $lt, $lte, $in, $nin, $contains, $icontains, "
        "$startswith, $endswith, $regex, $exists, $and, $or, $not.\n"
        'Respond ONLY with a JSON object (no prose). Example: {"total_cents": {"$gt": 10000}}'
    )


def extract_dsl_from_text(text: str) -> dict[str, Any]:
    """Slice the JSON object out of an LLM response.

    Tolerant of preamble / trailing prose — finds the first ``{`` and last
    ``}`` and parses the slice. Raises :class:`NLCompileError` with a
    structured recovery hint when the slice is missing or malformed.
    """
    if not text:
        raise NLCompileError(
            "LLM returned empty response for NL query",
            recovery=Recovery(
                hint="Retry the call or rephrase the query in plainer terms.",
                retry_safe=True,
            ),
        )
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise NLCompileError(
            f"LLM response did not contain a JSON object: {text[:200]!r}",
            recovery=Recovery(
                hint="The LLM produced prose instead of JSON. Retry or tighten the prompt.",
                retry_safe=True,
            ),
        )
    try:
        parsed = json.loads(text[start:end])
    except json.JSONDecodeError as exc:
        raise NLCompileError(
            f"LLM produced invalid JSON: {exc}",
            recovery=Recovery(
                hint="Retry the call. If the failure persists, simplify the query text.",
                retry_safe=True,
            ),
        ) from exc
    if not isinstance(parsed, dict):
        raise NLCompileError(
            f"LLM produced non-object DSL: {type(parsed).__name__}",
            recovery=Recovery(
                hint="Expected a JSON object for the DSL. Retry the call or add more query context.",
                retry_safe=True,
            ),
        )
    return parsed


async def compile_nl_to_dsl(
    *,
    llm: LLMBackend,
    adapter_id: str,
    endpoint: str,
    query: str,
    fields: list[str],
    cache: NLCompilationCache | None = None,
) -> tuple[dict[str, Any], bool]:
    """Compile ``query`` to a DSL dict, using ``cache`` to skip the LLM.

    Returns ``(dsl, from_cache)``. Raises :class:`NLCompileError` when the
    LLM output can't be parsed as JSON.
    """
    from liquid.models.llm import Message

    # Use ``is None`` — an empty :class:`NLCompilationCache` is falsy via
    # its ``__len__`` and would otherwise be replaced by the module default.
    active_cache = _DEFAULT_CACHE if cache is None else cache
    fingerprint = schema_fingerprint(fields)
    key = build_cache_key(adapter_id, endpoint, query, fingerprint)

    cached = active_cache.get(key)
    if cached is not None:
        return cached, True

    prompt = build_prompt(query, endpoint, fields)
    response = await llm.chat([Message(role="user", content=prompt)])
    text = response.content or ""
    dsl = extract_dsl_from_text(text)

    active_cache.set(key, dsl)
    return dsl, False
