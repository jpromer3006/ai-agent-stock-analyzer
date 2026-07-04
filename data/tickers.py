"""
tickers.py — 80-ticker universe across 15 sector categories.

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
    TECH = "TECH"                        # mega-platform megacaps (AAPL, MSFT, GOOGL, META, AMZN)
    ENERGY = "ENERGY"
    CONSUMER = "CONSUMER"
    HEALTHCARE = "HEALTHCARE"
    GENERIC = "GENERIC"
    # Phase 3b additions — sector splits and new specialists
    AI_COMPUTE = "AI_COMPUTE"             # NVDA, AVGO, SMCI, ANET, VRT, ARM, CRDO, ALAB, PLTR, AI
    SEMICONDUCTORS = "SEMICONDUCTORS"     # TSM, ASML, MRVL, MTSI, AMD (chip + fab supply chain)
    SOFTWARE_SAAS = "SOFTWARE_SAAS"       # CRM, DDOG (recurring revenue SaaS)
    BIOTECH = "BIOTECH"                   # clinical-stage and emerging biotech
    PHARMA_MEDDEVICE = "PHARMA_MEDDEVICE" # large pharma + med devices
    UTILITIES = "UTILITIES"               # regulated utilities (rate base, ROE)
    DEFENSE = "DEFENSE"                   # aerospace and defense primes
    INDUSTRIALS = "INDUSTRIALS"           # cyclicals, machinery, building
    INSURANCE = "INSURANCE"               # P&C, life, reinsurance
    # Funds — ETFs and mutual funds (SPY, QQQ, VTSAX). Not operating companies:
    # no 10-K/XBRL, mutual funds have no volume. Routed to the fund specialist.
    FUND = "FUND"


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
# Universe — 80 tickers across 15 categories
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
    # Tech / AI / Semis / SaaS (22) — broad 2026 AI cohort coverage
    #
    # All currently tagged TECH; Phase 3 splits this into separate
    # AI/Compute, Semiconductors, and Software/SaaS specialists with
    # their own system prompts and tool schemas. Sub_sector field hints
    # at the eventual routing so we can migrate in place.
    # ═══════════════════════════════════════════════════════════════
    # Mega-cap platforms (route by dominant segment in Phase 3)
    "MSFT":  TickerMeta("MSFT",  "Microsoft",            StockCategory.TECH, "AI/Cloud"),
    "GOOGL": TickerMeta("GOOGL", "Alphabet",             StockCategory.TECH, "AI/Ads"),
    "META":  TickerMeta("META",  "Meta Platforms",       StockCategory.TECH, "AI/Social"),
    "AMZN":  TickerMeta("AMZN",  "Amazon",               StockCategory.TECH, "AI/Cloud/Retail"),
    "AAPL":  TickerMeta("AAPL",  "Apple",                StockCategory.TECH, "Consumer Hardware"),

    # AI / Compute (silicon plus AI-thesis hardware)  — Phase 3b: AI_COMPUTE
    "NVDA":  TickerMeta("NVDA",  "NVIDIA",               StockCategory.AI_COMPUTE, "AI Accelerators"),
    "AVGO":  TickerMeta("AVGO",  "Broadcom",             StockCategory.AI_COMPUTE, "AI Networking/Silicon"),
    "ARM":   TickerMeta("ARM",   "Arm Holdings",         StockCategory.AI_COMPUTE, "Chip IP"),
    "SMCI":  TickerMeta("SMCI",  "Super Micro Computer", StockCategory.AI_COMPUTE, "AI Servers"),
    "ANET":  TickerMeta("ANET",  "Arista Networks",      StockCategory.AI_COMPUTE, "Datacenter Networking"),
    "VRT":   TickerMeta("VRT",   "Vertiv Holdings",      StockCategory.AI_COMPUTE, "Datacenter Power/Cooling"),
    "CRDO":  TickerMeta("CRDO",  "Credo Technology",     StockCategory.AI_COMPUTE, "Datacenter Connectivity"),
    "ALAB":  TickerMeta("ALAB",  "Astera Labs",          StockCategory.AI_COMPUTE, "AI Connectivity Silicon"),
    "PLTR":  TickerMeta("PLTR",  "Palantir",             StockCategory.AI_COMPUTE, "AI Software/Government"),
    "AI":    TickerMeta("AI",    "C3.ai",                StockCategory.AI_COMPUTE, "AI Software"),

    # Semiconductors (foundry and equipment) — Phase 3b: SEMICONDUCTORS
    "TSM":   TickerMeta("TSM",   "Taiwan Semiconductor", StockCategory.SEMICONDUCTORS, "Foundry"),
    "ASML":  TickerMeta("ASML",  "ASML Holding",         StockCategory.SEMICONDUCTORS, "Lithography Equipment"),
    "MRVL":  TickerMeta("MRVL",  "Marvell Technology",   StockCategory.SEMICONDUCTORS, "Datacenter Silicon"),
    "MTSI":  TickerMeta("MTSI",  "MACOM Technology",     StockCategory.SEMICONDUCTORS, "Specialty Connectivity"),
    "AMD":   TickerMeta("AMD",   "Advanced Micro Devices", StockCategory.SEMICONDUCTORS, "CPU/GPU"),

    # Software / SaaS (traditional recurring revenue) — Phase 3b: SOFTWARE_SAAS
    "CRM":   TickerMeta("CRM",   "Salesforce",           StockCategory.SOFTWARE_SAAS, "CRM/Cloud"),
    "DDOG":  TickerMeta("DDOG",  "Datadog",              StockCategory.SOFTWARE_SAAS, "Observability"),
    "NOW":   TickerMeta("NOW",   "ServiceNow",           StockCategory.SOFTWARE_SAAS, "Enterprise Workflow"),
    "SNOW":  TickerMeta("SNOW",  "Snowflake",            StockCategory.SOFTWARE_SAAS, "Data Cloud"),

    # ═══════════════════════════════════════════════════════════════
    # Energy (5)
    # ═══════════════════════════════════════════════════════════════
    "XOM":  TickerMeta("XOM", "ExxonMobil",              StockCategory.ENERGY, "Integrated Oil"),
    "CVX":  TickerMeta("CVX", "Chevron",                 StockCategory.ENERGY, "Integrated Oil"),
    "OXY":  TickerMeta("OXY", "Occidental Petroleum",    StockCategory.ENERGY, "E&P"),
    # NEE and DUK reclassified to UTILITIES (Phase 3b) — Weinstein lens is
    # rate base growth and allowed ROE, not oil cycle.
    "NEE":  TickerMeta("NEE", "NextEra Energy",          StockCategory.UTILITIES, "Renewable Utility"),
    "DUK":  TickerMeta("DUK", "Duke Energy",             StockCategory.UTILITIES, "Electric Utility"),

    # ═══════════════════════════════════════════════════════════════
    # Consumer (5)
    # ═══════════════════════════════════════════════════════════════
    "WMT":  TickerMeta("WMT",  "Walmart",                StockCategory.CONSUMER, "Retail"),
    "KO":   TickerMeta("KO",   "Coca-Cola",              StockCategory.CONSUMER, "Beverages"),
    "PG":   TickerMeta("PG",   "Procter & Gamble",       StockCategory.CONSUMER, "Staples"),
    "MCD":  TickerMeta("MCD",  "McDonald's",             StockCategory.CONSUMER, "Restaurants"),
    "COST": TickerMeta("COST", "Costco",                 StockCategory.CONSUMER, "Retail"),

    # ═══════════════════════════════════════════════════════════════
    # Healthcare — Managed Care (1)
    # ═══════════════════════════════════════════════════════════════
    "UNH":  TickerMeta("UNH",  "UnitedHealth",           StockCategory.HEALTHCARE, "Managed Care"),

    # ═══════════════════════════════════════════════════════════════
    # Pharma / MedDevice (5) — reclassified from HEALTHCARE
    # ═══════════════════════════════════════════════════════════════
    "JNJ":  TickerMeta("JNJ",  "Johnson & Johnson",      StockCategory.PHARMA_MEDDEVICE, "Diversified Pharma"),
    "LLY":  TickerMeta("LLY",  "Eli Lilly",              StockCategory.PHARMA_MEDDEVICE, "Pharma"),
    "PFE":  TickerMeta("PFE",  "Pfizer",                 StockCategory.PHARMA_MEDDEVICE, "Pharma"),
    "ABBV": TickerMeta("ABBV", "AbbVie",                 StockCategory.PHARMA_MEDDEVICE, "Pharma"),
    "TMO":  TickerMeta("TMO",  "Thermo Fisher Scientific", StockCategory.PHARMA_MEDDEVICE, "Life Science Tools"),

    # ═══════════════════════════════════════════════════════════════
    # Biotech (3)
    # ═══════════════════════════════════════════════════════════════
    "MRNA": TickerMeta("MRNA", "Moderna",                StockCategory.BIOTECH, "mRNA Therapeutics"),
    "REGN": TickerMeta("REGN", "Regeneron",              StockCategory.BIOTECH, "Biologics"),
    "VRTX": TickerMeta("VRTX", "Vertex Pharmaceuticals", StockCategory.BIOTECH, "Gene Therapy"),

    # ═══════════════════════════════════════════════════════════════
    # Defense / Aerospace (4) — BA reclassified from GENERIC
    # ═══════════════════════════════════════════════════════════════
    "BA":   TickerMeta("BA",   "Boeing",                 StockCategory.DEFENSE, "Aerospace"),
    "LMT":  TickerMeta("LMT",  "Lockheed Martin",       StockCategory.DEFENSE, "Defense Prime"),
    "RTX":  TickerMeta("RTX",  "RTX Corporation",        StockCategory.DEFENSE, "Defense/Aerospace"),
    "NOC":  TickerMeta("NOC",  "Northrop Grumman",       StockCategory.DEFENSE, "Defense Prime"),
    "GD":   TickerMeta("GD",   "General Dynamics",       StockCategory.DEFENSE, "Defense/Marine"),

    # ═══════════════════════════════════════════════════════════════
    # Industrials (3) — CAT, GE reclassified from GENERIC
    # ═══════════════════════════════════════════════════════════════
    "CAT":  TickerMeta("CAT",  "Caterpillar",            StockCategory.INDUSTRIALS, "Machinery"),
    "GE":   TickerMeta("GE",   "GE Aerospace",           StockCategory.INDUSTRIALS, "Aerospace/Power"),
    "DE":   TickerMeta("DE",   "Deere & Company",        StockCategory.INDUSTRIALS, "Machinery"),

    # ═══════════════════════════════════════════════════════════════
    # Insurance (3) — BRK-B reclassified from GENERIC
    # ═══════════════════════════════════════════════════════════════
    "BRK-B": TickerMeta("BRK-B", "Berkshire Hathaway",   StockCategory.INSURANCE, "Conglomerate/P&C"),
    "ALL":   TickerMeta("ALL",   "Allstate",             StockCategory.INSURANCE, "P&C Insurance"),
    "PGR":   TickerMeta("PGR",   "Progressive",          StockCategory.INSURANCE, "Auto Insurance"),

    # ═══════════════════════════════════════════════════════════════
    # Generic / Diversified (1)
    # ═══════════════════════════════════════════════════════════════
    "DIS":   TickerMeta("DIS",   "Walt Disney",          StockCategory.GENERIC, "Media"),
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
