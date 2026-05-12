"""Pilot: rank French judicial real-estate auctions by liquidity vs market.

Pipeline
--------
1.  Scrape Licitor TJ audience pages -> list of detail-page URLs (one per lot).
2.  Scrape each detail page -> mise a prix, surface, statut (libre/occupe),
    commune, address, tribunal, sale date, on-page Licitor benchmark.
3.  Geocode commune+address via the Base Adresse Nationale (BAN, free) to
    obtain the INSEE commune code, postcode and population.
4.  Pull the official DVF (Demandes de Valeurs Foncieres) CSV for that commune
    and recent year(s), filter comparables (same type_local, surface within
    +/-30 %), compute median EUR/m^2.
5.  Score each lot: discount (mise a prix vs DVF-estimated market value),
    market depth, type, statut, commune population. Print a ranked table.

Avoventes (`avoventes.fr/ventes-passees`) is a client-side SPA: its 2 MB HTML
is a JS shell with no embedded lot data, so it would require Playwright. The
pilot leaves it as a stub - results from past auctions can be sourced from
Licitor's own adjudication archive (visible on the same TJ schedule pages,
shown as e.g. "06-05-2026 : 163 000 EUR" next to each lot).

Run
---
    pip install httpx selectolax pydantic
    python examples/20_judicial_pilot.py

Optional CLI args:
    --tj tj-versailles --date mercredi-6-mai-2026
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import math
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from selectolax.parser import HTMLParser

LICITOR_BASE = "https://www.licitor.com"
BAN_BASE = "https://api-adresse.data.gouv.fr"
DVF_BASE = "https://files.data.gouv.fr/geo-dvf/latest/csv"
DVF_CACHE = Path("/tmp/dvf_cache")
HTML_CACHE = Path("/tmp/licitor_cache")
DVF_CACHE.mkdir(exist_ok=True)
HTML_CACHE.mkdir(exist_ok=True)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) liquid-judicial-pilot/0.1"
DVF_YEARS = (2024, 2023, 2022)

# Licitor throttle: serialise requests + minimum delay between them.
_LICITOR_LOCK = asyncio.Lock()
_LICITOR_LAST_T = 0.0
_LICITOR_MIN_GAP = 0.7  # seconds


# --------------------------------------------------------------------------- #
# Data models                                                                 #
# --------------------------------------------------------------------------- #


Statut = Literal["libre", "occupe", "unknown"]


class Lot(BaseModel):
    """Parsed Licitor lot, enriched with DVF analysis."""

    lot_id: str
    detail_url: str
    tribunal: str | None = None
    auction_date: str | None = None
    department: str | None = None
    commune: str | None = None
    address: str | None = None
    type_label: str | None = None  # raw "Un appartement"
    type_dvf: str | None = None  # mapped DVF type_local
    surface_m2: float | None = None
    statut: Statut = "unknown"
    mise_a_prix_eur: float | None = None
    licitor_bench_min: float | None = None
    licitor_bench_mean: float | None = None
    licitor_bench_max: float | None = None
    insee_commune: str | None = None
    postcode: str | None = None
    population: int | None = None

    # Analysis
    n_comparables: int = 0
    median_price_per_m2: float | None = None
    estimated_market_eur: float | None = None
    discount: float | None = None  # 1 - mise_a_prix / estimated_market
    liquidity_score: float = 0.0
    verdict: str = ""

    notes: list[str] = Field(default_factory=list)


@dataclass
class DVFRow:
    date: str
    value_eur: float
    surface_m2: float
    rooms: int
    type_local: str
    code_commune: str


# --------------------------------------------------------------------------- #
# Licitor scraping                                                            #
# --------------------------------------------------------------------------- #

_RE_MISE = re.compile(r"[Mm]ise\s+à\s+prix\s*[:\-]?\s*([\d\s ]+)\s*€")  # noqa: RUF001
_RE_SURFACE_CARREZ = re.compile(r"Surface\s+Loi\s+Carrez\s*:\s*(\d+[,.]?\d*)\s*m²", re.IGNORECASE)
_RE_SURFACE_GENERIC = re.compile(r"(\d+[,.]?\d*)\s*m²")
_RE_BENCH = re.compile(r"Prix\s+(min|moyen|max)\.\s*([\d\s ]+)\s*€/m²", re.IGNORECASE)  # noqa: RUF001
_RE_TITLE = re.compile(
    r"Annonce\s+n°(\d+)[^\n]*?:\s*([^\n]+?)\s+à\s+([^,()\n]+?)\s*\(([^)\n]+)\)\s*,\s*"
    r"mise\s+à\s+prix\s*:\s*([\d\s ]+)\s*€",  # noqa: RUF001
    re.IGNORECASE,
)
_RE_DATE = re.compile(
    r"((?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+\d+\s+\w+\s+\d{4}(?:\s+à\s+\d+h\d*)?)",
    re.IGNORECASE,
)


def _parse_eur(raw: str | None) -> float | None:
    if not raw:
        return None
    clean = raw.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def _normalize(html: str) -> tuple[HTMLParser, str]:
    tree = HTMLParser(html)
    for node in tree.css("script, style, noscript"):
        node.decompose()
    text = tree.body.text(separator="\n") if tree.body else ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return tree, text


def _cache_key(url: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", url)[-180:]
    return HTML_CACHE / f"{safe}.html"


async def _throttle_licitor() -> None:
    global _LICITOR_LAST_T
    async with _LICITOR_LOCK:
        import time

        gap = time.monotonic() - _LICITOR_LAST_T
        if gap < _LICITOR_MIN_GAP:
            await asyncio.sleep(_LICITOR_MIN_GAP - gap)
        _LICITOR_LAST_T = time.monotonic()


async def fetch(client: httpx.AsyncClient, url: str, *, retries: int = 3) -> str:
    cache = _cache_key(url)
    if cache.exists() and cache.stat().st_size > 2000:
        return cache.read_text(encoding="utf-8")

    is_licitor = "licitor.com" in url
    delay = 1.0
    last_status = 0
    for attempt in range(retries + 1):
        if is_licitor:
            await _throttle_licitor()
        r = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=20.0)
        last_status = r.status_code
        # Licitor sometimes returns 200 but with a stripped page (anti-bot).
        too_short = is_licitor and len(r.text) < 5000
        if r.status_code < 500 and r.status_code != 429 and not too_short:
            r.raise_for_status()
            cache.write_text(r.text, encoding="utf-8")
            return r.text
        if attempt == retries:
            break
        await asyncio.sleep(delay)
        delay *= 2
    raise httpx.HTTPStatusError(
        f"fetch failed: status={last_status}",
        request=None,
        response=r,  # type: ignore[arg-type]
    )


async def scrape_audience(client: httpx.AsyncClient, url: str) -> list[str]:
    """Return absolute URLs of all lot detail pages on a TJ audience page."""
    html = await fetch(client, url)
    tree = HTMLParser(html)
    urls: list[str] = []
    seen: set[str] = set()
    for a in tree.css("a"):
        href = a.attributes.get("href") or ""
        if href.startswith("/annonce/") and href.endswith(".html") and href not in seen:
            seen.add(href)
            urls.append(LICITOR_BASE + href)
    return urls


def _extract_address(text: str, commune: str | None) -> str | None:
    if not commune:
        return None
    pattern = re.compile(rf"{re.escape(commune)}\s*\n\s*([^\n]+?)(?:\n|Afficher le plan)", re.IGNORECASE)
    m = pattern.search(text)
    return m.group(1).strip() if m else None


_RE_URL_SLUG = re.compile(r"/annonce/\d+/\d+/\d+/[^/]+/([^/]+)/([^/]+)/([^/]+)/(\d+)\.html$")


def _clean_commune(slug: str) -> str:
    s = slug.replace("-", " ")
    # Paris arrondissement: "paris 13eme" -> "Paris 13e"
    m = re.match(r"^paris\s+(\d+)(?:e|er|eme|ème|me)?\s*$", s, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        return f"Paris {n}{'er' if n == 1 else 'e'}"
    # Lyon / Marseille arrondissements share the same convention.
    m = re.match(r"^(lyon|marseille)\s+(\d+)(?:e|er|eme|ème|me)?\s*$", s, re.IGNORECASE)
    if m:
        city, n = m.group(1).title(), int(m.group(2))
        return f"{city} {n}{'er' if n == 1 else 'e'}"
    return s.title()


def _from_url_slug(url: str) -> dict[str, str]:
    """Extract type / commune / department / id from a Licitor URL.

    URL shape: /annonce/AA/BB/CC/vente-aux-encheres/<type>/<commune>/<dep>/<id>.html
    """
    m = _RE_URL_SLUG.search(url)
    if not m:
        return {}
    type_slug, commune_slug, dep_slug, lot_id = m.groups()
    return {
        "lot_id": lot_id,
        "type_slug": type_slug.replace("-", " "),
        "commune_slug": _clean_commune(commune_slug),
        "dep_slug": dep_slug.replace("-", " ").title(),
    }


async def scrape_lot_detail(client: httpx.AsyncClient, url: str) -> Lot:
    slug = _from_url_slug(url)
    html = await fetch(client, url)
    _, text = _normalize(html)

    lot = Lot(lot_id=slug.get("lot_id") or url, detail_url=url)
    # URL slug is always-on; title parsing may override with cleaner values.
    if slug:
        lot.type_label = slug["type_slug"].lower()
        lot.commune = slug["commune_slug"]
        lot.department = slug["dep_slug"]

    if m := _RE_TITLE.search(text):
        lot.type_label = m.group(2).strip().lower()
        lot.commune = m.group(3).strip()
        lot.department = m.group(4).strip()
        lot.mise_a_prix_eur = _parse_eur(m.group(5))

    if m := re.search(r"Tribunal\s+Judiciaire\s+de\s+\w+[^\n]*", text):
        lot.tribunal = m.group(0).strip()

    # Filter date lines: pick the one paired with an hour (= auction date).
    for cand in _RE_DATE.findall(text):
        if "h" in cand.lower():
            lot.auction_date = cand
            break

    # Prefer Carrez surface when present (commercial / co-ownership lots).
    m = _RE_SURFACE_CARREZ.search(text) or _RE_SURFACE_GENERIC.search(text)
    if m:
        lot.surface_m2 = float(m.group(1).replace(",", "."))

    statut_match = re.search(r"(Occup[ée]s?|Libre\b)", text, re.IGNORECASE)
    if statut_match:
        s = statut_match.group(1).lower()
        lot.statut = "libre" if s.startswith("libre") else "occupe"

    if m := _RE_MISE.search(text):
        lot.mise_a_prix_eur = _parse_eur(m.group(1))

    for kind, raw in _RE_BENCH.findall(text):
        val = _parse_eur(raw)
        k = kind.lower()
        if k == "min":
            lot.licitor_bench_min = val
        elif k == "moyen":
            lot.licitor_bench_mean = val
        elif k == "max":
            lot.licitor_bench_max = val

    lot.address = _extract_address(text, lot.commune)
    lot.type_dvf = _map_type(lot.type_label)
    return lot


# Licitor wording -> DVF `type_local` value (from the official schema).
_TYPE_MAP = {
    "appartement": "Appartement",
    "studio": "Appartement",
    "maison": "Maison",
    "villa": "Maison",
    "pavillon": "Maison",
    "local commercial": "Local industriel. commercial ou assimilé",
    "local d'activité": "Local industriel. commercial ou assimilé",
    "bureau": "Local industriel. commercial ou assimilé",
    "garage": "Dépendance",
    "parking": "Dépendance",
    "cave": "Dépendance",
}


def _map_type(label: str | None) -> str | None:
    if not label:
        return None
    low = label.lower()
    for key, dvf in _TYPE_MAP.items():
        if key in low:
            return dvf
    return None


# --------------------------------------------------------------------------- #
# BAN geocoding                                                               #
# --------------------------------------------------------------------------- #


async def geocode(client: httpx.AsyncClient, lot: Lot) -> None:
    """Populate insee_commune, postcode, population on the lot."""
    query = lot.commune or ""
    if lot.address:
        query = f"{lot.address}, {lot.commune}" if lot.commune else lot.address
    if not query.strip():
        lot.notes.append("geocode: empty query")
        return
    r = await client.get(
        f"{BAN_BASE}/search/",
        params={"q": query, "limit": 1, "type": "municipality" if not lot.address else None},
        headers={"User-Agent": USER_AGENT},
        timeout=10.0,
    )
    if r.status_code != 200:
        lot.notes.append(f"geocode: HTTP {r.status_code}")
        return
    data = r.json()
    feats = data.get("features") or []
    if not feats and lot.address:
        # Fallback: try commune-only lookup
        r2 = await client.get(
            f"{BAN_BASE}/search/",
            params={"q": lot.commune, "limit": 1, "type": "municipality"},
            headers={"User-Agent": USER_AGENT},
            timeout=10.0,
        )
        feats = r2.json().get("features") or []
    if not feats:
        lot.notes.append("geocode: no result")
        return
    props = feats[0]["properties"]
    lot.insee_commune = props.get("citycode")
    lot.postcode = props.get("postcode")
    pop = props.get("population")
    if pop is not None:
        lot.population = int(pop)


# --------------------------------------------------------------------------- #
# DVF fetch + comparables                                                     #
# --------------------------------------------------------------------------- #


async def fetch_dvf_commune(client: httpx.AsyncClient, year: int, dep: str, commune: str) -> list[DVFRow]:
    """Fetch and cache the DVF CSV for a given year+commune. Returns rows."""
    cache_file = DVF_CACHE / f"{year}_{dep}_{commune}.csv"
    if cache_file.exists():
        raw = cache_file.read_text(encoding="utf-8")
    else:
        url = f"{DVF_BASE}/{year}/communes/{dep}/{commune}.csv"
        r = await client.get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        raw = r.text
        cache_file.write_text(raw, encoding="utf-8")

    rows: list[DVFRow] = []
    for rec in csv.DictReader(io.StringIO(raw)):
        if rec.get("nature_mutation") != "Vente":
            continue
        try:
            val = float(rec["valeur_fonciere"])
            surf = float(rec.get("surface_reelle_bati") or 0)
            rooms = int(rec.get("nombre_pieces_principales") or 0)
        except (TypeError, ValueError):
            continue
        if val < 1000 or surf <= 0:
            continue
        rows.append(
            DVFRow(
                date=rec.get("date_mutation", ""),
                value_eur=val,
                surface_m2=surf,
                rooms=rooms,
                type_local=rec.get("type_local") or "",
                code_commune=rec.get("code_commune") or "",
            )
        )
    return rows


def _depcode_from_insee(insee: str) -> str:
    # DGFiP DVF uses 2A/2B for Corsica; otherwise first 2 digits, or 3 for 97x/98x.
    if insee.startswith(("97", "98")):
        return insee[:3]
    if insee.startswith("2A") or insee.startswith("2B"):
        return insee[:2]
    return insee[:2]


async def load_comparables_for_lot(client: httpx.AsyncClient, lot: Lot) -> list[DVFRow]:
    if not lot.insee_commune or not lot.type_dvf:
        return []
    dep = _depcode_from_insee(lot.insee_commune)
    rows: list[DVFRow] = []
    for year in DVF_YEARS:
        rows.extend(await fetch_dvf_commune(client, year, dep, lot.insee_commune))
        if len(rows) >= 200:
            break
    # Filter by type
    same_type = [r for r in rows if r.type_local == lot.type_dvf]
    if not same_type:
        return []
    if lot.surface_m2 is None or lot.surface_m2 <= 0:
        return same_type
    lo, hi = lot.surface_m2 * 0.7, lot.surface_m2 * 1.3
    band = [r for r in same_type if lo <= r.surface_m2 <= hi]
    return band if len(band) >= 3 else same_type  # widen if too few


# --------------------------------------------------------------------------- #
# Analysis & scoring                                                          #
# --------------------------------------------------------------------------- #


def analyse(lot: Lot, comparables: list[DVFRow]) -> None:
    lot.n_comparables = len(comparables)
    if comparables:
        prices_m2 = [r.value_eur / r.surface_m2 for r in comparables]
        lot.median_price_per_m2 = statistics.median(prices_m2)
    elif lot.licitor_bench_mean:
        # Fallback to Licitor's on-page benchmark when DVF has nothing.
        lot.median_price_per_m2 = lot.licitor_bench_mean
        lot.notes.append("using Licitor on-page benchmark (DVF empty)")

    if lot.median_price_per_m2 and lot.surface_m2:
        lot.estimated_market_eur = lot.median_price_per_m2 * lot.surface_m2
        if lot.mise_a_prix_eur:
            lot.discount = 1 - lot.mise_a_prix_eur / lot.estimated_market_eur

    lot.liquidity_score = _liquidity_score(lot)
    lot.verdict = _verdict(lot)


def _liquidity_score(lot: Lot) -> float:
    score = 0.0
    # Market depth via commune population (log scale).
    if lot.population:
        score += max(0.0, min(3.0, math.log10(lot.population) - 2))
    # Type preference.
    if lot.type_dvf == "Appartement":
        score += 2.0
    elif lot.type_dvf == "Maison":
        score += 1.5
    elif lot.type_dvf == "Dépendance":
        score += 0.5
    # Vacant beats occupied.
    if lot.statut == "libre":
        score += 2.0
    elif lot.statut == "occupe":
        score += 0.0
    # Comparables density.
    if lot.n_comparables >= 10:
        score += 1.5
    elif lot.n_comparables >= 5:
        score += 1.0
    elif lot.n_comparables >= 2:
        score += 0.5
    # Discount.
    if lot.discount is not None:
        if lot.discount >= 0.4:
            score += 1.5
        elif lot.discount >= 0.25:
            score += 1.0
        elif lot.discount >= 0.1:
            score += 0.5
    return round(min(score, 10.0), 1)


def _verdict(lot: Lot) -> str:
    if lot.mise_a_prix_eur is None:
        return "INCOMPLETE: no mise a prix"
    if lot.estimated_market_eur is None:
        return "NO_BENCH: cannot price"
    if lot.liquidity_score >= 6.5 and (lot.discount or 0) >= 0.3 and lot.statut == "libre":
        cap = lot.estimated_market_eur * 0.85  # leave 15% for fees + margin
        return f"BUY  (cap bid <= {cap:,.0f} EUR)"
    if lot.liquidity_score >= 5 and (lot.discount or 0) >= 0.2:
        return "REVIEW: read cahier des charges"
    return "SKIP"


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #


async def process_audience(client: httpx.AsyncClient, audience_url: str, *, sem: asyncio.Semaphore) -> list[Lot]:
    lot_urls = await scrape_audience(client, audience_url)
    print(f"  found {len(lot_urls)} lot(s) on {audience_url}", file=sys.stderr)

    async def one(u: str) -> Lot | None:
        async with sem:
            try:
                lot = await scrape_lot_detail(client, u)
                await geocode(client, lot)
                comps = await load_comparables_for_lot(client, lot)
                analyse(lot, comps)
                return lot
            except Exception as exc:
                print(f"  ! {u}: {exc!r}", file=sys.stderr)
                return None

    results = await asyncio.gather(*[one(u) for u in lot_urls])
    return [r for r in results if r is not None]


def print_table(lots: list[Lot]) -> None:
    lots = sorted(lots, key=lambda x: x.liquidity_score, reverse=True)
    cols = [
        ("id", 7),
        ("commune", 18),
        ("type", 12),
        ("m2", 6),
        ("statut", 8),
        ("mise a prix", 12),
        ("EUR/m2 mkt", 11),
        ("est. mkt", 12),
        ("discount", 9),
        ("score", 6),
        ("verdict", 38),
    ]
    header = "  ".join(name.ljust(w) for name, w in cols)
    print(header)
    print("-" * len(header))
    for lot in lots:
        row = [
            lot.lot_id,
            (lot.commune or "?")[:18],
            (lot.type_dvf or lot.type_label or "?")[:12],
            f"{lot.surface_m2:.1f}" if lot.surface_m2 else "?",
            lot.statut,
            f"{lot.mise_a_prix_eur:,.0f}" if lot.mise_a_prix_eur else "?",
            f"{lot.median_price_per_m2:,.0f}" if lot.median_price_per_m2 else "?",
            f"{lot.estimated_market_eur:,.0f}" if lot.estimated_market_eur else "?",
            f"{lot.discount * 100:.0f}%" if lot.discount is not None else "?",
            f"{lot.liquidity_score:.1f}",
            lot.verdict[:38],
        ]
        print("  ".join(str(v).ljust(w) for v, (_, w) in zip(row, cols, strict=True)))


# Default demo audiences (a few TJs on different dates so the pilot returns
# something even when one is empty).
DEFAULT_AUDIENCES = [
    "/ventes-judiciaires-immobilieres/tj-versailles/mercredi-6-mai-2026.html",
    "/ventes-judiciaires-immobilieres/tj-paris/jeudi-7-mai-2026.html",
    "/ventes-judiciaires-immobilieres/tj-nanterre/jeudi-7-mai-2026.html",
]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Judicial real-estate liquidity pilot")
    parser.add_argument("--audience", action="append", default=None, help="Licitor audience path or URL")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args()

    audiences = args.audience or DEFAULT_AUDIENCES
    audiences = [a if a.startswith("http") else LICITOR_BASE + a for a in audiences]

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(http2=False, follow_redirects=True) as client:
        all_lots: list[Lot] = []
        for url in audiences:
            print(f"== {url}", file=sys.stderr)
            lots = await process_audience(client, url, sem=sem)
            all_lots.extend(lots)

    print()
    print_table(all_lots)
    print()
    print(f"Total: {len(all_lots)} lots analysed.")
    buys = [lot for lot in all_lots if lot.verdict.startswith("BUY")]
    print(f"BUY:    {len(buys)}")
    print(f"REVIEW: {sum(1 for lot in all_lots if lot.verdict.startswith('REVIEW'))}")
    print(f"SKIP:   {sum(1 for lot in all_lots if lot.verdict.startswith('SKIP'))}")


if __name__ == "__main__":
    asyncio.run(main())
