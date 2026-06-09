"""Shared HTML-grid extraction engine — the modality-agnostic core under the
``html_scrape`` driver and discovery.

A *grid* is any repeating list of records rendered as HTML: a news feed, a
product catalogue, a job board, a search-results table. Discovery describes one
with a :class:`GridSchema` (CSS selectors + a field map); this module turns a
page into ``list[dict]`` records deterministically — no LLM, no protocol
knowledge. The driver above it only orchestrates HTTP (the feed page and, when a
record's detail lives on its own page, the N+1 detail fetches).

The schema is intentionally **field-generic**: ``fields`` is an open mapping of
``name -> FieldSpec``, so the same engine extracts ``{title, body, published_at}``
from a news article and ``{title, price, sku, image}`` from a product card. The
news-specific shape produced by the n8n "Scraper Architect" (``heading_selector``,
``text_content_selector``, …) is accepted too and normalized into the generic
form by :func:`normalize_schema`, so existing scrape schemas load unchanged.

BeautifulSoup (the ``scrape`` extra) is imported lazily so the core stays
dependency-free, matching the database drivers' stance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

if TYPE_CHECKING:
    from bs4 import BeautifulSoup, Tag

# Attributes that always hold a URL — resolved against the page URL so a record's
# link/image comes back absolute. ``content`` is deliberately excluded: a
# ``<meta content>`` is a URL only for og:image/og:url and is plain text (a
# datetime, a description) everywhere else, so it's resolved by value-shape below
# instead of by name.
_LINK_ATTRS = frozenset({"href", "src", "data-src", "data-original"})

# Leading markers of a relative URL — a value starting like this is resolved
# regardless of which attribute carried it (covers og:image content="/img.jpg").
_RELATIVE_PREFIXES = ("/", "./", "../")

# Fallback strategies a field can request via ``FieldSpec.fallback`` when its
# primary selector finds nothing — the self-healing seam. ``og:*`` reads the
# OpenGraph meta tag (which the Architect prompt already prefers for images);
# ``h1`` grabs the first heading. These cover the empirically most fragile fields
# (a tightly-classed ``h1`` selector breaks first as a site re-skins) without a
# full re-discovery round. See :func:`_apply_fallback`.


@dataclass(slots=True)
class FieldSpec:
    """How to extract one named field from a record.

    ``scope`` decides where the selector runs: ``"row"`` against the record's
    container in the grid (cheap — no extra request), ``"detail"`` against the
    record's own page (needs the N+1 fetch). ``multi`` joins every match (a body
    split across ``<p>``s). ``fallback`` names a recovery selector when the
    primary yields nothing.
    """

    selector: str | None
    attr: str | None = None
    scope: str = "detail"
    multi: bool = False
    fallback: str | None = None


@dataclass(slots=True)
class GridSchema:
    """A deterministic description of a list/detail HTML source."""

    fields: dict[str, FieldSpec]
    row_selector: str | None = None
    link_selector: str | None = None
    detail: bool = True
    base_url: str | None = None
    max_rows: int | None = 50
    cron: str | None = None

    @property
    def has_detail_fields(self) -> bool:
        return self.detail and any(f.scope == "detail" for f in self.fields.values())


# ---------------------------------------------------------------------------
# parsing


def parse_html(text: str, *, xml: bool = False) -> BeautifulSoup:
    """Parse markup into a BeautifulSoup tree (lazy ``scrape`` extra).

    Prefers the ``lxml`` parser when installed (faster, more lenient) and falls
    back to the stdlib ``html.parser`` so the only hard requirement is
    ``beautifulsoup4``. ``xml=True`` parses a sitemap/Atom document.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError as e:  # pragma: no cover - exercised via the install hint
        raise ImportError(
            "HTML scraping needs the 'beautifulsoup4' package. Install it: pip install 'liquid-api[scrape]'"
        ) from e

    if xml:
        try:
            return BeautifulSoup(text, "lxml-xml")
        except Exception:  # lxml not installed
            return BeautifulSoup(text, "html.parser")
    try:
        return BeautifulSoup(text, "lxml")
    except Exception:
        return BeautifulSoup(text, "html.parser")


def select_one(node: Any, selector: str | None) -> Tag | None:
    if not selector:
        return None
    try:
        return node.select_one(selector)
    except Exception:
        # A malformed/unsupported selector must not crash a whole fetch — treat
        # it as "matched nothing" so the field simply comes back empty/healable.
        return None


def select_all(node: Any, selector: str | None) -> list[Tag]:
    if not selector:
        return []
    try:
        return node.select(selector)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# field extraction


def _read(el: Tag, attr: str | None, page_url: str | None) -> str | None:
    """Read an attribute (resolving URL-ish attrs to absolute) or the text."""
    if attr:
        raw = el.get(attr.strip())
        if raw is None:
            return None
        val = " ".join(raw) if isinstance(raw, list) else str(raw)
        val = val.strip()
        if not val:
            return None
        return _resolve_url(val, attr.strip().lower(), page_url)
    txt = el.get_text(" ", strip=True)
    return txt or None


def _resolve_url(val: str, attr: str, page_url: str | None) -> str:
    """Make a value absolute iff it's actually a (relative) URL.

    Resolves when the attribute always carries a link (href/src/…) or the value
    looks relative (``/x``, ``./x``, ``../x``). Leaves already-absolute URLs and
    non-URL text (datetimes, descriptions in a ``content`` attr) untouched.
    """
    if not page_url:
        return val
    if "://" in val[:16]:  # already absolute (has a scheme)
        return val
    if attr in _LINK_ATTRS or val.startswith(_RELATIVE_PREFIXES):
        return urljoin(page_url, val)
    return val


def _apply_fallback(page: Any, kind: str | None, page_url: str | None) -> str | None:
    if not kind:
        return None
    if kind == "h1":
        el = select_one(page, "h1")
        return _read(el, None, page_url) if el else None
    if kind.startswith("og:"):
        el = select_one(page, f'meta[property="{kind}"]') or select_one(page, f'meta[name="{kind}"]')
        if el is None:
            return None
        # Only og:image (and og:url) carry a URL to resolve; og:title /
        # og:description content is plain text and must not be urljoin'd.
        url_attr = page_url if kind in ("og:image", "og:url") else None
        return _read(el, "content", url_attr)
    return None


def extract_field(
    spec: FieldSpec,
    *,
    row_node: Any,
    detail_node: Any | None,
    row_url: str | None,
    detail_url: str | None,
    treat_as_row: bool,
) -> Any:
    """Extract one field, applying scope routing and self-healing fallback.

    ``treat_as_row`` collapses detail-scope fields onto the row node — used when
    the schema declares ``detail=False`` (a pure single-page grid) so a
    detail-scoped field still resolves against the only node available.
    """
    use_detail = spec.scope == "detail" and not treat_as_row and detail_node is not None
    node = detail_node if use_detail else row_node
    page_url = detail_url if use_detail else row_url

    if spec.multi:
        els = select_all(node, spec.selector)
        parts = [v for el in els if (v := _read(el, spec.attr, page_url))]
        if parts:
            return "\n".join(parts)
    else:
        el = select_one(node, spec.selector)
        if el is not None:
            val = _read(el, spec.attr, page_url)
            if val:
                return val

    # Primary selector found nothing — try the field's fallback against the
    # widest node we have (the detail page if fetched, else the row).
    fb_node = detail_node if (detail_node is not None and not treat_as_row) else node
    fb_url = detail_url if (detail_node is not None and not treat_as_row) else page_url
    return _apply_fallback(fb_node, spec.fallback, fb_url)


# ---------------------------------------------------------------------------
# schema normalization (generic form + legacy news form)

# Legacy "Scraper Architect" news schema → generic field map. Each entry is
# (field name, legacy-selector-key, legacy-attr-key, multi, fallback).
_LEGACY_FIELDS = (
    ("title", "heading_selector", None, False, "og:title"),
    ("category", "category_selector", None, False, None),
    ("body", "text_content_selector", None, True, None),
    ("image", "image_selector", "image_selector_attribute", False, "og:image"),
    ("image_credit", "image_credit", None, False, None),
    ("published_at", "published_time_selector", "published_time_attribute", False, None),
)


def _is_legacy(meta: dict) -> bool:
    return "fields" not in meta and any(
        k in meta for k in ("heading_selector", "text_content_selector", "link_selector")
    )


def normalize_schema(meta: dict[str, Any]) -> GridSchema:
    """Build a :class:`GridSchema` from transport metadata.

    Accepts either the generic grid form (with a ``fields`` map) or the legacy
    news form (flat ``*_selector`` keys), so schemas authored by the n8n
    Architect load without conversion.
    """
    if _is_legacy(meta):
        fields: dict[str, FieldSpec] = {}
        for name, sel_key, attr_key, multi, fallback in _LEGACY_FIELDS:
            selector = meta.get(sel_key)
            if not selector:
                continue
            fields[name] = FieldSpec(
                selector=selector,
                attr=meta.get(attr_key) if attr_key else None,
                scope="detail",
                multi=multi,
                fallback=fallback,
            )
        return GridSchema(
            fields=fields,
            row_selector=None,
            link_selector=meta.get("link_selector"),
            detail=True,
            base_url=meta.get("base_url"),
            max_rows=meta.get("max_rows", 50),
            cron=meta.get("cron_frequency"),
        )

    raw_fields = meta.get("fields") or {}
    fields = {}
    for name, spec in raw_fields.items():
        if isinstance(spec, str):  # shorthand: just a selector, detail-scoped text
            fields[name] = FieldSpec(selector=spec)
            continue
        fields[name] = FieldSpec(
            selector=spec.get("selector"),
            attr=spec.get("attr"),
            scope=spec.get("scope", "detail"),
            multi=bool(spec.get("multi", False)),
            fallback=spec.get("fallback"),
        )
    return GridSchema(
        fields=fields,
        row_selector=meta.get("row_selector"),
        link_selector=meta.get("link_selector"),
        detail=bool(meta.get("detail", True)),
        base_url=meta.get("base_url"),
        max_rows=meta.get("max_rows", 50),
        cron=meta.get("cron_frequency") or meta.get("cron"),
    )


# ---------------------------------------------------------------------------
# row enumeration


@dataclass(slots=True)
class Row:
    """One record location in the grid: its container node and detail link."""

    node: Any
    url: str | None = None
    row_fields: dict[str, Any] = field(default_factory=dict)


def enumerate_rows(page: Any, schema: GridSchema, page_url: str) -> list[Row]:
    """Find each record on the grid page and resolve its detail link.

    Two modes:

    * **grid** (``row_selector`` set) — each match is a record container; the
      detail link is the first ``link_selector`` match inside it (or the row
      itself when it's an ``<a>``).
    * **feed** (no ``row_selector``) — the legacy news shape: ``link_selector``
      matches the anchors directly and each anchor is a one-element record.
    """
    base = schema.base_url or page_url
    rows: list[Row] = []

    if schema.row_selector:
        for node in select_all(page, schema.row_selector):
            link = None
            anchor = select_one(node, schema.link_selector) if schema.link_selector else None
            if anchor is None and getattr(node, "name", None) == "a":
                anchor = node
            if anchor is not None and (href := anchor.get("href")):
                link = urljoin(base, href if isinstance(href, str) else href[0])
            rows.append(Row(node=node, url=link))
    else:
        for anchor in select_all(page, schema.link_selector):
            href = anchor.get("href")
            link = urljoin(base, href if isinstance(href, str) else href[0]) if href else None
            rows.append(Row(node=anchor, url=link))

    # De-duplicate by resolved link, preserving order (feeds repeat links).
    seen: set[str] = set()
    unique: list[Row] = []
    for r in rows:
        key = r.url or ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(r)

    if schema.max_rows:
        return unique[: schema.max_rows]
    return unique
