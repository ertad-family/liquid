"""Baseline commercial-rent benchmark for the judicial-auctions pilot.

There is no open API in France that returns asking rent EUR/m²/year for
arbitrary commercial addresses (loyer-commerce.com / Data-B are paid;
the open `Carte des loyers` dataset on data.gouv.fr covers housing only).

So we ship a static lookup table extracted from publicly published
broker market reports as of Q1 2026:

  * CBRE France `prix-des-loyers-de-bureaux-ile-de-france` blog
    (snapshot 1 Jan 2026, "neuf/restructuré" tier).
  * CBRE Marketview Office regional metros, H2 2025.
  * Cushman & Wakefield Main Streets across the World 2025 for prime retail.

The numbers are EUR/m²/year for `OFFICE` and `RETAIL` separately. They
represent the upper end of the local range (broker prime / new-build);
the yield gates below are calibrated against that anchor, so the formula
remains internally consistent.

To refresh: re-read those reports and patch the dicts. Each entry has a
single source URL in the trailing comment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CommercialKind = Literal["OFFICE", "RETAIL"]

# --------------------------------------------------------------------------- #
# Per-commune (INSEE code) baselines.                                         #
# --------------------------------------------------------------------------- #

# CBRE 1-Jan-2026 "neuf/restructuré" office rents, IDF.
# https://immobilier.cbre.fr/blog/bureaux/prix-des-loyers-de-bureaux-ile-de-france/
OFFICE_BY_INSEE: dict[str, float] = {
    # Paris — Quartier Central des Affaires (1, 2, 8, 9, 16, 17)
    "75101": 900,  # 1er
    "75102": 850,  # 2e
    "75108": 950,  # 8e — prime QCA
    "75109": 800,  # 9e
    "75116": 800,  # 16e
    "75117": 720,  # 17e
    # Paris — Rive Gauche / Centre Est
    "75103": 700,  # 3e (Marais)
    "75104": 700,  # 4e
    "75105": 650,  # 5e
    "75106": 750,  # 6e
    "75107": 800,  # 7e
    # Paris — Sud / Nord Est (cheaper office sub-markets)
    "75110": 500,  # 10e
    "75111": 500,  # 11e
    "75112": 520,  # 12e
    "75113": 480,  # 13e
    "75114": 500,  # 14e
    "75115": 540,  # 15e
    "75118": 420,  # 18e
    "75119": 400,  # 19e
    "75120": 420,  # 20e
    # Hauts-de-Seine business poles
    "92062": 600,  # Puteaux (La Défense core)
    "92026": 600,  # Courbevoie (La Défense)
    "92012": 550,  # Boulogne-Billancourt
    "92044": 565,  # Levallois-Perret
    "92040": 550,  # Issy-les-Moulineaux
    "92051": 715,  # Neuilly-sur-Seine
    "92050": 380,  # Nanterre (excl. prefecture)
    "92075": 450,  # Vanves
    "92073": 480,  # Suresnes
    # Val-de-Marne
    "94041": 275,  # Ivry-sur-Seine
    # Outer ring
    "78517": 200,  # Rambouillet (estimate, secondary tertiary)
    "78362": 180,  # Mantes-la-Ville
    "93071": 170,  # Sevran
    # Regional metros (CBRE H2 2025 prime office)
    "69123": 350,  # Lyon — Part-Dieu / 3e
    "13001": 290,  # Marseille 1er (Euroméditerranée)
    "31555": 280,  # Toulouse
    "33063": 270,  # Bordeaux
    "44109": 260,  # Nantes
    "59350": 240,  # Lille
    "67482": 240,  # Strasbourg
    "06088": 320,  # Nice
    "35238": 220,  # Rennes
}

# Cushman main-street prime retail (Q4 2025) + Knight Frank regional.
# Numbers are PRIME high-street; pure secondary retail is far lower (~200-500).
# https://www.cushmanwakefield.com/en/insights/main-streets-across-the-world
RETAIL_BY_INSEE: dict[str, float] = {
    "75108": 12000,  # Champs-Élysées / Faubourg-Saint-Honoré belt
    "75101": 6000,
    "75102": 4500,
    "75109": 4000,  # Boulevard Haussmann
    "75116": 3500,
    "75106": 4500,  # Rive Gauche prime
    "75107": 3500,
    "92012": 1800,  # Boulogne high street
    "92051": 2200,  # Neuilly
    "69123": 2400,  # Lyon — Rue de la République
    "13001": 1800,  # Marseille — Rue Saint-Ferréol
    "33063": 2200,  # Bordeaux — Sainte-Catherine
    "31555": 2000,  # Toulouse — Rue Saint-Rome / Alsace-Lorraine
    "44109": 1800,  # Nantes — Rue Crébillon
    "06088": 2400,  # Nice — Avenue Jean Médecin
    "59350": 1700,  # Lille — Rue Neuve
}


# --------------------------------------------------------------------------- #
# Population-tier fallback for communes not in the dicts above.               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Tier:
    label: str
    office: float  # EUR/m²/year
    retail: float
    yield_gate: float  # decimal, e.g. 0.07


# Ordered from most to least dense. First matching tier wins.
TIERS: list[tuple[int, Tier]] = [
    (1_000_000, Tier("Tier-1 metro CBD", 500, 2500, 0.055)),
    (200_000, Tier("Tier-1 metro non-CBD", 300, 1500, 0.065)),
    (100_000, Tier("Tier-2 metro", 220, 800, 0.075)),
    (50_000, Tier("Mid commune", 180, 500, 0.085)),
    (20_000, Tier("Small commune", 140, 350, 0.095)),
    (5_000, Tier("Bourg", 100, 200, 0.105)),
    (0, Tier("Rural", 70, 120, 0.12)),
]

# Yield gates by zone (used when the INSEE sits in `OFFICE_BY_INSEE`/`RETAIL_BY_INSEE`).
# Numbers track CBRE / BNP yield monitor — prime QCA Paris 4.50%, secondary 6-7%, regional 7-8%.
YIELD_GATES_BY_INSEE: dict[str, float] = {
    # Paris QCA
    "75101": 0.050,
    "75102": 0.052,
    "75108": 0.045,
    "75109": 0.055,
    "75116": 0.055,
    "75117": 0.060,
    # Paris non-QCA
    "75103": 0.060,
    "75104": 0.060,
    "75105": 0.060,
    "75106": 0.055,
    "75107": 0.055,
    "75110": 0.065,
    "75111": 0.065,
    "75112": 0.065,
    "75113": 0.065,
    "75114": 0.065,
    "75115": 0.065,
    "75118": 0.070,
    "75119": 0.070,
    "75120": 0.070,
    # La Défense / inner ring
    "92062": 0.060,
    "92026": 0.060,
    "92012": 0.060,
    "92044": 0.060,
    "92040": 0.060,
    "92051": 0.055,
    "92050": 0.075,
    # Regional metros
    "69123": 0.065,
    "13001": 0.075,
    "31555": 0.070,
    "33063": 0.070,
    "44109": 0.070,
    "59350": 0.075,
    "67482": 0.075,
    "06088": 0.065,
    "35238": 0.075,
}


def _tier_for(population: int | None) -> Tier:
    pop = population or 0
    for floor, tier in TIERS:
        if pop >= floor:
            return tier
    return TIERS[-1][1]


def lookup_rent(insee: str | None, kind: CommercialKind, population: int | None) -> tuple[float, str]:
    """Return (EUR/m²/year, source label)."""
    table = OFFICE_BY_INSEE if kind == "OFFICE" else RETAIL_BY_INSEE
    if insee and insee in table:
        return table[insee], f"baseline:{kind.lower()}:{insee}"
    tier = _tier_for(population)
    rent = tier.office if kind == "OFFICE" else tier.retail
    return rent, f"tier:{tier.label}"


def lookup_market_yield(insee: str | None, population: int | None) -> tuple[float, str]:
    """Return (decimal yield, source label).

    A commercial buyer wants the rent / price ratio to BEAT this gate.
    Prime CBD historically clears below 5%, regional secondary above 8%.
    """
    if insee and insee in YIELD_GATES_BY_INSEE:
        return YIELD_GATES_BY_INSEE[insee], f"yield:{insee}"
    tier = _tier_for(population)
    return tier.yield_gate, f"yield:{tier.label}"


def is_commercial(type_dvf: str | None) -> bool:
    return type_dvf == "Local industriel. commercial ou assimilé"


def guess_kind(type_label: str | None) -> CommercialKind:
    """OFFICE vs RETAIL heuristic from Licitor's wording.

    "Local commercial" alone is ambiguous (Licitor uses it for boxes, depots,
    showrooms, ground-floor shops, professional offices...). Default to OFFICE
    because (a) the OFFICE benchmark dict is much denser than RETAIL, and
    (b) RETAIL numbers in our table are PRIME high-street rents that
    systematically overstate the rent for a non-prime address.
    """
    if not type_label:
        return "OFFICE"
    low = type_label.lower()
    if any(k in low for k in ("boutique", "magasin", "fonds de commerce", "commerce de bouche")):
        return "RETAIL"
    return "OFFICE"
