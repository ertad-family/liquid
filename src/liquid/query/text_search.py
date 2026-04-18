"""Full-text search across records.

Tiny BM25-lite scorer: we tokenize the query and each candidate string field,
award one point per query-token hit, and normalize by the square root of the
field length so short high-signal fields (``subject``, ``name``) outrank long
fields (``body``) on equal matches. No heavy dependency required.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

# Safety cap — same rationale as aggregate: bounded scan unless the caller
# explicitly asks for more.
DEFAULT_SCAN_LIMIT = 10_000
DEFAULT_RESULT_LIMIT = 50

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [tok.lower() for tok in _TOKEN_RE.findall(text)]


def _collect_strings(
    record: dict,
    fields: list[str] | None,
    prefix: str = "",
) -> dict[str, str]:
    """Return ``{field_path: text}`` for every string-typed field we should search.

    If ``fields`` is None we auto-discover all top-level string fields. If
    ``fields`` is provided we honour dot-notation paths so agents can pinpoint
    nested fields (e.g. ``"author.name"``).
    """
    if fields is not None:
        out: dict[str, str] = {}
        for field in fields:
            value = _get_path(record, field)
            if isinstance(value, str):
                out[field] = value
        return out

    # Auto-discover: top-level string fields only. We avoid recursing into
    # nested dicts/lists because agents usually care about a document's
    # headline fields and recursion risks ballooning the match set.
    _ = prefix
    return {k: v for k, v in record.items() if isinstance(v, str)}


def _get_path(record: dict, path: str) -> Any:
    current: Any = record
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _score_field(query_tokens: list[str], field_text: str) -> float:
    """Return a weight in [0, +inf): token hits divided by sqrt(field length).

    Longer fields get softly penalized so a hit in a 4-word subject ranks
    higher than a hit in a 400-word body. Returns 0 when nothing matches.
    """
    field_tokens = _tokenize(field_text)
    if not field_tokens:
        return 0.0

    field_set = set(field_tokens)
    hits = sum(1 for qt in query_tokens if qt in field_set)
    if hits == 0:
        return 0.0

    # Normalize by sqrt length — BM25-style length dampening.
    length_factor = math.sqrt(len(field_tokens))
    return hits / length_factor


def _score_record(
    query_tokens: list[str],
    record: dict,
    fields: list[str] | None,
) -> tuple[float, list[str]]:
    """Return (total_score, matched_field_paths) for a single record."""
    field_texts = _collect_strings(record, fields)
    if not field_texts:
        return 0.0, []

    total = 0.0
    matched: list[str] = []
    for path, text in field_texts.items():
        score = _score_field(query_tokens, text)
        if score > 0:
            total += score
            matched.append(path)
    return total, matched


def search_records(
    records: Iterable[dict],
    query: str,
    *,
    fields: list[str] | None = None,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> list[dict[str, Any]]:
    """Rank records by relevance to ``query``. Pure — no I/O.

    Returns up to ``limit`` matches, each shaped as
    ``{"record": {...}, "score": float, "matched_fields": [...]}``.
    Records with zero score are dropped.
    """
    if not isinstance(query, str) or not query.strip():
        return []

    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    # Normalize to max_score = 1.0 so scores are comparable across calls.
    scored: list[tuple[float, list[str], dict]] = []
    for record in records:
        score, matched = _score_record(query_tokens, record, fields)
        if score > 0:
            scored.append((score, matched, dict(record)))

    if not scored:
        return []

    max_score = max(s for s, _, _ in scored) or 1.0
    scored.sort(key=lambda item: item[0], reverse=True)

    results: list[dict[str, Any]] = []
    for score, matched, record in scored[:limit]:
        results.append(
            {
                "record": record,
                "score": round(score / max_score, 4),
                "matched_fields": matched,
            }
        )
    return results


async def search_async(
    page_iter: AsyncIterator[list[dict]],
    query: str,
    *,
    fields: list[str] | None = None,
    limit: int = DEFAULT_RESULT_LIMIT,
    scan_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Walk an async page iterator, score, and return top-N matches."""
    effective_scan_limit = DEFAULT_SCAN_LIMIT if scan_limit is None else scan_limit
    if effective_scan_limit is not None and effective_scan_limit <= 0:
        raise ValueError("scan_limit must be positive")

    scanned: list[dict] = []
    async for page in page_iter:
        for record in page:
            if effective_scan_limit is not None and len(scanned) >= effective_scan_limit:
                break
            scanned.append(record)
        if effective_scan_limit is not None and len(scanned) >= effective_scan_limit:
            break

    return search_records(scanned, query, fields=fields, limit=limit)
