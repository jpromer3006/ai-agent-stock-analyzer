"""
tickers.py — Expanded 50-ticker universe across 7 sector categories.

Each ticker is assigned a StockCategory that determines which
specialist agent handles it. The classifier can override this
for tickers outside the pre-defined universe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Stock categories — each maps to a specialist agent
# ---------------------------------------------------------------------------
class StockCategory(str, Enum):
    REIT = "REIT"
    INFRASTRUCTURE_EPC = "INFRASTRUCTURE_EPC"
    BANK = "BANK"
    TECH = "TECH"
    ENERGY = "ENERGY"
    CONSUMER = "CONSUMER"
    HEALTHCARE = "HEALTHCARE"
    GENERIC = "GENERIC"


# ---------------------------------------------------------------------------
# Ticker metadata
# ---------------------------------------------------------------------------
@dataclass
class TickerMeta:
    ticker: str
    company_name: str
    category: StockCategory
    sub_sector: str = ""
    cik: Optional[str] = None  # filled lazily by SEC client
    xls_filename: Optional[str] = None
    # REIT-specific
    reit_type: Optional[str] = None  # "Industrial", "Retail", "Residential", etc.
    # Infra-specific
    fred_driver: Optional[str] = None
    satellite_eligible: bool = False


# ---------------------------------------------------------------------------
# Universe — 50 tickers across 7 categories
# ---------------------------------------------------------------------------
UNIVERSE: dict[str, TickerMeta] = {

    # ═══════════════════════════════════════════════════════════════
    # REITs (10) — primary focus per user request
    # ═══════════════════════════════════════════════════════════════
    "O":    TickerMeta("O",    "Realty Income Corp",      StockCategory.REIT, "Net Lease", reit_type="Net Lease"),
    "PLD":  TickerMeta("PLD",  "Prologis Inc",            StockCategory.REIT, "Industrial", reit_type="Industrial"),
    "AMT":  TickerMeta("AMT",  "American Tower Corp",     StockCategory.REIT, "Specialty",  reit_type="Cell Tower"),
    "SPG":  TickerMeta("SPG",  "Simon Property Group",    StockCategory.REIT, "Retail",     reit_type="Mall"),
    "EQIX": TickerMeta("EQIX", "Equinix Inc",             StockCategory.REIT, "Specialty",  reit_type="Data Center"),
    "PSA":  TickerMeta("PSA",  "Public Storage",          StockCategory.REIT, "Specialty",  reit_type="Self Storage"),
    "WELL": TickerMeta("WELL", "Welltower Inc",           StockCategory.REIT, "Healthcare", reit_type="Senior Housing"),
    "DLR":  TickerMeta("DLR",  "Digital Realty Trust",    StockCategory.REIT, "Specialty",  reit_type="Data Center"),
    "VICI": TickerMeta("VICI", "VICI Properties",         StockCategory.REIT, "Specialty",  reit_type="Gaming"),
    "ARE":  TickerMeta("ARE",  "Alexandria Real Estate",  StockCategory.REIT, "Office",     reit_type="Life Science"),

    # ═══════════════════════════════════════════════════════════════
    # Infrastructure / EPC (10) — carried over from REIT Intelligence
    # ═══════════════════════════════════════════════════════════════
    "PWR":  TickerMeta("PWR",  "Quanta Services",         StockCategory.INFRASTRUCTURE_EPC, "Utility Contractor", fred_driver="PNRESCONS"),
    "EME":  TickerMeta("EME",  "EMCOR Group",             StockCategory.INFRASTRUCTURE_EPC, "Mechanical/Electrical", fred_driver="PNRESCONS"),
    "FLR":  TickerMeta("FLR",  "Fluor Corporation",       StockCategory.INFRASTRUCTURE_EPC, "Power EPC", fred_driver="PNRESCONS"),
    "KBR":  TickerMeta("KBR",  "KBR Inc.",                StockCategory.INFRASTRUCTURE_EPC, "Power EPC", fred_driver="PNRESCONS"),
    "DY":   TickerMeta("DY",   "Dycom Industries",        StockCategory.INFRASTRUCTURE_EPC, "Telecom Infra", fred_driver="PNRESCONS"),
    "APG":  TickerMeta("APG",  "API Group",               StockCategory.INFRASTRUCTURE_EPC, "Safety Services", fred_driver="PNRESCONS"),
    "MTZ":  TickerMeta("MTZ",  "MasTec",                  StockCategory.INFRASTRUCTURE_EPC, "Utility Contractor", fred_driver="IPN213111N"),
    "PRIM": TickerMeta("PRIM", "Primoris Services",       StockCategory.INFRASTRUCTURE_EPC, "Utility Contractor", fred_driver="PNRESCONS"),
    "AGX":  TickerMeta("AGX",  "Argan Inc.",              StockCategory.INFRASTRUCTURE_EPC, "Power Plant EPC", fred_driver="PNRESCONS", satellite_eligible=True),
    "ORA":  TickerMeta("ORA",  "Ormat Technologies",      StockCategory.INFRASTRUCTURE_EPC, "Geothermal", fred_driver="PNRESCONS", satellite_eligible=True),

    # ═══════════════════════════════════════════════════════════════
    # Banks (5)
    # ═══════════════════════════════════════════════════════════════
    "JPM": TickerMeta("JPM", "JPMorgan Chase",           StockCategory.BANK, "Money Center"),
    "BAC": TickerMeta("BAC", "Bank of America",          StockCategory.BANK, "Money Center"),
    "WFC": TickerMeta("WFC", "Wells Fargo",              StockCategory.BANK, "Money Center"),
    "GS":  TickerMeta("GS",  "Goldman Sachs",            StockCategory.BANK, "Investment Bank"),
    "MS":  TickerMeta("MS",  "Morgan Stanley",           StockCategory.BANK, "Investment Bank"),

    # ═══════════════════════════════════════════════════════════════
    # Tech (5)
    # ═══════════════════════════════════════════════════════════════
    "MSFT":  TickerMeta("MSFT",  "Microsoft",            StockCategory.TECH, "Software/Cloud"),
    "GOOGL": TickerMeta("GOOGL", "Alphabet",             StockCategory.TECH, "Ads/Cloud"),
    "META":  TickerMeta("META",  "Meta Platforms",       StockCategory.TECH, "Social/Ads"),
    "CRM":   TickerMeta("CRM",   "Salesforce",           StockCategory.TECH, "SaaS"),
    "NVDA":  TickerMeta("NVDA",  "NVIDIA",               StockCategory.TECH, "Semiconductors"),

    # ═══════════════════════════════════════════════════════════════
    # Energy (5)
    # ═══════════════════════════════════════════════════════════════
    "XOM":  TickerMeta("XOM", "ExxonMobil",              StockCategory.ENERGY, "Integrated Oil"),
    "CVX":  TickerMeta("CVX", "Chevron",                 StockCategory.ENERGY, "Integrated Oil"),
    "OXY":  TickerMeta("OXY", "Occidental Petroleum",    StockCategory.ENERGY, "E&P"),
    "NEE":  TickerMeta("NEE", "NextEra Energy",          StockCategory.ENERGY, "Renewable Utility"),
    "DUK":  TickerMeta("DUK", "Duke Energy",             StockCategory.ENERGY, "Electric Utility"),

    # ═══════════════════════════════════════════════════════════════
    # Consumer (5)
    # ═══════════════════════════════════════════════════════════════
    "WMT":  TickerMeta("WMT",  "Walmart",                StockCategory.CONSUMER, "Retail"),
    "KO":   TickerMeta("KO",   "Coca-Cola",              StockCategory.CONSUMER, "Beverages"),
    "PG":   TickerMeta("PG",   "Procter & Gamble",       StockCategory.CONSUMER, "Staples"),
    "MCD":  TickerMeta("MCD",  "McDonald's",             StockCategory.CONSUMER, "Restaurants"),
    "COST": TickerMeta("COST", "Costco",                 StockCategory.CONSUMER, "Retail"),

    # ═══════════════════════════════════════════════════════════════
    # Healthcare (5)
    # ═══════════════════════════════════════════════════════════════
    "UNH":  TickerMeta("UNH",  "UnitedHealth",           StockCategory.HEALTHCARE, "Insurance"),
    "JNJ":  TickerMeta("JNJ",  "Johnson & Johnson",      StockCategory.HEALTHCARE, "Pharma"),
    "LLY":  TickerMeta("LLY",  "Eli Lilly",              StockCategory.HEALTHCARE, "Pharma"),
    "PFE":  TickerMeta("PFE",  "Pfizer",                 StockCategory.HEALTHCARE, "Pharma"),
    "ABBV": TickerMeta("ABBV", "AbbVie",                 StockCategory.HEALTHCARE, "Pharma"),

    # ═══════════════════════════════════════════════════════════════
    # Generic / Diversified (5)
    # ═══════════════════════════════════════════════════════════════
    "BRK-B": TickerMeta("BRK-B", "Berkshire Hathaway",   StockCategory.GENERIC, "Conglomerate"),
    "DIS":   TickerMeta("DIS",   "Walt Disney",          StockCategory.GENERIC, "Media"),
    "BA":    TickerMeta("BA",    "Boeing",               StockCategory.GENERIC, "Aerospace"),
    "CAT":   TickerMeta("CAT",   "Caterpillar",          StockCategory.GENERIC, "Industrial"),
    "GE":    TickerMeta("GE",    "General Electric",     StockCategory.GENERIC, "Industrial"),
}


# ---------------------------------------------------------------------------
# FRED series mapping (for infrastructure specialist)
# ---------------------------------------------------------------------------
FRED_SERIES = {
    "PNRESCONS":  "Private Non-Residential Construction Spending (SAAR, $B)",
    "TLPBLCONS":  "Total Public Construction Spending (SAAR, $B)",
    "IPN213111N": "Oil & Gas Well Drilling Production Index",
    "HOUST":      "Housing Starts Total",
    "CPIAUCSL":   "CPI All Urban Consumers",
    "FEDFUNDS":   "Federal Funds Rate",
    "GS10":       "10-Year Treasury Rate",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_ticker(ticker: str) -> Optional[TickerMeta]:
    """Return TickerMeta for a ticker, or None if not in universe."""
    return UNIVERSE.get(ticker.upper())


def tickers_by_category(category: StockCategory) -> list[str]:
    """Return all tickers in a given category."""
    return [t for t, m in UNIVERSE.items() if m.category == category]


def category_counts() -> dict[str, int]:
    """Return count of tickers per category."""
    counts: dict[str, int] = {}
    for m in UNIVERSE.values():
        counts[m.category.value] = counts.get(m.category.value, 0) + 1
    return counts


if __name__ == "__main__":
    print(f"Universe size: {len(UNIVERSE)} tickers\n")
    for cat, count in sorted(category_counts().items()):
        tickers = tickers_by_category(StockCategory[cat])
        print(f"  {cat:<20} ({count:2d})  {', '.join(tickers)}")
