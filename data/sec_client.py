"""
sec_client.py — SEC EDGAR client for XBRL financial facts + 10-K/10-Q filings.

Two endpoints:
    1. XBRL company facts — structured financial data (revenue, cash flow, balance sheet)
       https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json
    2. Filing submissions — 10-K/10-Q text URLs
       https://data.sec.gov/submissions/CIK{cik}.json

SEC requires a User-Agent header. Rate limit: 10 req/sec max.
All results cached to data/cache/sec/.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SEC_USER_AGENT = "AdminAI Research admin@adminaillc.com"
SEC_HEADERS = {"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}

_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "sec"
_XBRL_CACHE = _CACHE_DIR / "xbrl"
_FILINGS_CACHE = _CACHE_DIR / "filings"
_TICKER_MAP_CACHE = _CACHE_DIR / "ticker_cik_map.json"

for d in (_XBRL_CACHE, _FILINGS_CACHE):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Ticker → CIK mapping
# ---------------------------------------------------------------------------
_TICKER_CIK_URL = "https://www.sec.gov/files/company_tickers.json"
_CIK_CACHE_TTL = 7 * 24 * 3600  # refresh weekly


def _load_ticker_cik_map(force_refresh: bool = False) -> dict[str, str]:
    """
    Load ticker→CIK mapping. Cached to data/cache/sec/ticker_cik_map.json.
    Refreshes weekly.
    """
    if _TICKER_MAP_CACHE.exists() and not force_refresh:
        age = time.time() - _TICKER_MAP_CACHE.stat().st_mtime
        if age < _CIK_CACHE_TTL:
            with open(_TICKER_MAP_CACHE) as f:
                return json.load(f)

    logger.info("Fetching SEC ticker→CIK map...")
    resp = requests.get(_TICKER_CIK_URL, headers=SEC_HEADERS, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    # raw format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    mapping: dict[str, str] = {}
    for _, entry in raw.items():
        ticker = entry.get("ticker", "").upper()
        cik = entry.get("cik_str")
        if ticker and cik is not None:
            mapping[ticker] = f"{cik:010d}"

    with open(_TICKER_MAP_CACHE, "w") as f:
        json.dump(mapping, f)

    return mapping


def get_cik(ticker: str) -> Optional[str]:
    """Return 10-digit CIK string for a ticker, or None if not found."""
    ticker = ticker.upper().replace("-", "")  # handle BRK-B → BRKB
    mapping = _load_ticker_cik_map()
    cik = mapping.get(ticker)
    if cik:
        return cik
    # Try alternative formats
    alt = ticker.replace(".", "")
    return mapping.get(alt)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
_last_request_time = 0.0
_MIN_INTERVAL = 0.15  # ~6 req/sec to stay well under SEC's 10/sec limit


def _rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time = time.time()


def _sec_get(url: str, timeout: int = 15) -> requests.Response:
    _rate_limit()
    resp = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# XBRL company facts (structured financials)
# ---------------------------------------------------------------------------

# Standard XBRL concepts for the 3 financial statements.
# Each maps friendly key → list of possible GAAP taxonomy names (first match wins)
INCOME_STATEMENT_CONCEPTS = {
    "Revenue":          ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "CostOfRevenue":    ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
    "GrossProfit":      ["GrossProfit"],
    "OperatingIncome":  ["OperatingIncomeLoss"],
    "NetIncome":        ["NetIncomeLoss"],
    "EPSBasic":         ["EarningsPerShareBasic"],
    "EPSDiluted":       ["EarningsPerShareDiluted"],
}

BALANCE_SHEET_CONCEPTS = {
    "TotalAssets":           ["Assets"],
    "CurrentAssets":         ["AssetsCurrent"],
    "CashAndEquivalents":    ["CashAndCashEquivalentsAtCarryingValue", "Cash"],
    "TotalLiabilities":      ["Liabilities"],
    "CurrentLiabilities":    ["LiabilitiesCurrent"],
    "LongTermDebt":          ["LongTermDebt", "LongTermDebtNoncurrent"],
    "TotalEquity":           ["StockholdersEquity"],
}

CASH_FLOW_CONCEPTS = {
    "OperatingCashFlow":     ["NetCashProvidedByUsedInOperatingActivities"],
    "InvestingCashFlow":     ["NetCashProvidedByUsedInInvestingActivities"],
    "FinancingCashFlow":     ["NetCashProvidedByUsedInFinancingActivities"],
    "CapitalExpenditure":    ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "DividendsPaid":         ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
}

# REIT-specific concepts
REIT_CONCEPTS = {
    "RealEstateInvestments": ["RealEstateInvestmentPropertyNet", "RealEstateInvestmentPropertyAtCost"],
    "RentalIncome":          ["OperatingLeasesIncomeStatementLeaseRevenue", "LeaseIncome"],
    "FFO":                   ["FundsFromOperations"],  # often not reported in XBRL — computed from NI + D&A
    "Depreciation":          ["DepreciationAndAmortization", "Depreciation"],
}


def get_company_facts(ticker: str, force_refresh: bool = False) -> Optional[dict]:
    """
    Fetch XBRL company facts JSON from SEC.
    Returns parsed JSON or None if unavailable.
    Cached to data/cache/sec/xbrl/{ticker}.json (24h TTL).
    """
    cik = get_cik(ticker)
    if not cik:
        logger.warning(f"No CIK found for {ticker}")
        return None

    cache_file = _XBRL_CACHE / f"{ticker.upper()}.json"
    if cache_file.exists() and not force_refresh:
        age = time.time() - cache_file.stat().st_mtime
        if age < 24 * 3600:
            with open(cache_file) as f:
                return json.load(f)

    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        resp = _sec_get(url)
        data = resp.json()
        with open(cache_file, "w") as f:
            json.dump(data, f)
        return data
    except Exception as exc:
        logger.error(f"Failed to fetch company facts for {ticker}: {exc}")
        return None


def _extract_concept_series(facts: dict, concept_names: list[str],
                             unit: str = "USD", form: str = "10-K") -> list[dict]:
    """
    Extract time-series for a list of possible GAAP concept names.
    Returns list of {"fy", "fp", "end", "val", "form"} sorted by end date.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    for concept in concept_names:
        if concept not in us_gaap:
            continue
        units = us_gaap[concept].get("units", {})
        if unit not in units:
            # Try any available unit for non-USD concepts (e.g., shares, USD/shares)
            if not units:
                continue
            unit_key = list(units.keys())[0]
        else:
            unit_key = unit

        observations = units[unit_key]
        # Filter by form type (10-K for annual, 10-Q for quarterly)
        if form:
            observations = [o for o in observations if o.get("form", "").startswith(form)]

        # Sort by end date
        observations = sorted(observations, key=lambda x: x.get("end", ""))
        return observations

    return []


def get_income_statement(ticker: str, years: int = 5) -> dict:
    """
    Return income statement time-series for a ticker.
    Returns {concept: [{fy, end, val}]} for the last N fiscal years.
    """
    facts = get_company_facts(ticker)
    if not facts:
        return {}

    result: dict[str, list[dict]] = {}
    for key, concepts in INCOME_STATEMENT_CONCEPTS.items():
        unit = "USD/shares" if "EPS" in key else "USD"
        series = _extract_concept_series(facts, concepts, unit=unit, form="10-K")
        # Take most recent N years
        result[key] = series[-years:] if series else []
    return result


def get_balance_sheet(ticker: str, periods: int = 4) -> dict:
    """Return balance sheet time-series (most recent N fiscal year-ends)."""
    facts = get_company_facts(ticker)
    if not facts:
        return {}

    result: dict[str, list[dict]] = {}
    for key, concepts in BALANCE_SHEET_CONCEPTS.items():
        series = _extract_concept_series(facts, concepts, unit="USD", form="10-K")
        result[key] = series[-periods:] if series else []
    return result


def get_cash_flow(ticker: str, years: int = 5) -> dict:
    """Return cash flow time-series."""
    facts = get_company_facts(ticker)
    if not facts:
        return {}

    result: dict[str, list[dict]] = {}
    for key, concepts in CASH_FLOW_CONCEPTS.items():
        series = _extract_concept_series(facts, concepts, unit="USD", form="10-K")
        result[key] = series[-years:] if series else []
    return result


def get_reit_metrics(ticker: str, years: int = 5) -> dict:
    """REIT-specific metrics from XBRL."""
    facts = get_company_facts(ticker)
    if not facts:
        return {}

    result: dict[str, list[dict]] = {}
    for key, concepts in REIT_CONCEPTS.items():
        series = _extract_concept_series(facts, concepts, unit="USD", form="10-K")
        result[key] = series[-years:] if series else []
    return result


# ---------------------------------------------------------------------------
# 10-K / 10-Q text filings
# ---------------------------------------------------------------------------

def get_filings_index(ticker: str) -> list[dict]:
    """
    Return the list of recent filings for a ticker.
    Each item: {accessionNumber, form, filingDate, primaryDocument, reportDate}
    """
    cik = get_cik(ticker)
    if not cik:
        return []

    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = _sec_get(url)
        data = resp.json()
    except Exception as exc:
        logger.error(f"Failed to fetch submissions for {ticker}: {exc}")
        return []

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return []

    # Transpose parallel lists to list of dicts
    keys = list(recent.keys())
    n = len(recent.get("form", []))
    filings = []
    for i in range(n):
        filing = {k: recent[k][i] for k in keys if i < len(recent[k])}
        filings.append(filing)

    return filings


def get_latest_10k_url(ticker: str) -> Optional[str]:
    """Return the URL to the primary document of the latest 10-K."""
    filings = get_filings_index(ticker)
    cik = get_cik(ticker)
    if not cik or not filings:
        return None

    for f in filings:
        if f.get("form") == "10-K":
            accession = f["accessionNumber"].replace("-", "")
            primary = f["primaryDocument"]
            return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary}"
    return None


def _download_filing_text(url: str) -> Optional[str]:
    """Download and extract text from a 10-K filing URL."""
    try:
        resp = _sec_get(url, timeout=30)
    except Exception as exc:
        logger.error(f"Failed to download filing: {exc}")
        return None

    if not resp.text:
        return None

    # Parse HTML → plain text
    soup = BeautifulSoup(resp.text, "lxml")
    # Remove scripts, styles, tables of contents
    for tag in soup(["script", "style"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Collapse whitespace
    text = re.sub(r"\n\s*\n", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def get_10k_text(ticker: str, force_refresh: bool = False) -> Optional[str]:
    """
    Fetch the full text of the latest 10-K filing.
    Cached to data/cache/sec/filings/{ticker}_10k.txt.
    """
    cache_file = _FILINGS_CACHE / f"{ticker.upper()}_10k.txt"
    if cache_file.exists() and not force_refresh:
        age = time.time() - cache_file.stat().st_mtime
        if age < 90 * 24 * 3600:  # 90 day TTL (10-Ks are annual)
            return cache_file.read_text()

    url = get_latest_10k_url(ticker)
    if not url:
        return None

    text = _download_filing_text(url)
    if text:
        cache_file.write_text(text)
    return text


# ---------------------------------------------------------------------------
# 10-K section extraction (Item 1, 1A, 7, etc.)
# ---------------------------------------------------------------------------

# Common SEC 10-K section patterns
SECTION_PATTERNS = {
    "Item 1 - Business":          r"(?i)item\s+1[^a-z]\s*[.\s]*business",
    "Item 1A - Risk Factors":     r"(?i)item\s+1a[.\s]*risk\s+factors",
    "Item 2 - Properties":        r"(?i)item\s+2[.\s]*properties",
    "Item 3 - Legal Proceedings": r"(?i)item\s+3[.\s]*legal\s+proceedings",
    "Item 7 - MD&A":              r"(?i)item\s+7[^a-z]\s*[.\s]*management['\u2019]?s?\s+discussion",
    "Item 7A - Market Risk":      r"(?i)item\s+7a[.\s]*quantitative",
    "Item 8 - Financial":         r"(?i)item\s+8[.\s]*financial\s+statements",
}


def extract_10k_sections(ticker: str, max_chars_per_section: int = 15000) -> dict[str, str]:
    """
    Parse the 10-K text and return a dict of {section_name: text}.
    Each section is truncated to max_chars_per_section to keep context manageable.
    """
    text = get_10k_text(ticker)
    if not text:
        return {}

    # Find all section markers with their positions
    matches = []
    for name, pattern in SECTION_PATTERNS.items():
        for m in re.finditer(pattern, text):
            matches.append((m.start(), name))

    # Sort by position, dedup (keep first occurrence of each)
    matches.sort()
    seen_names = set()
    ordered = []
    for pos, name in matches:
        if name not in seen_names:
            seen_names.add(name)
            ordered.append((pos, name))

    # Extract text between consecutive section starts
    sections: dict[str, str] = {}
    for i, (start, name) in enumerate(ordered):
        end = ordered[i + 1][0] if i + 1 < len(ordered) else len(text)
        raw = text[start:end].strip()
        sections[name] = raw[:max_chars_per_section]

    return sections


# ---------------------------------------------------------------------------
# Formatted summary helpers (for agent tool outputs)
# ---------------------------------------------------------------------------

def format_income_statement(ticker: str) -> str:
    """Format income statement time-series as a readable string."""
    data = get_income_statement(ticker, years=3)
    if not data or not any(data.values()):
        return f"No XBRL income statement data available for {ticker}."

    lines = [f"=== Income Statement — {ticker} (last 3 fiscal years) ==="]
    # Build year columns
    years_seen = set()
    for series in data.values():
        for obs in series:
            years_seen.add(obs.get("fy"))
    years = sorted([y for y in years_seen if y])[-3:]

    for concept, series in data.items():
        by_year = {obs.get("fy"): obs.get("val") for obs in series}
        row = f"  {concept:<20}"
        for y in years:
            val = by_year.get(y)
            if val is None:
                row += f"  {y}: N/A"
            elif concept.startswith("EPS"):
                row += f"  FY{y}: ${val:.2f}"
            else:
                row += f"  FY{y}: ${val/1e6:,.0f}M"
        lines.append(row)
    return "\n".join(lines)


def format_balance_sheet(ticker: str) -> str:
    data = get_balance_sheet(ticker, periods=3)
    if not data or not any(data.values()):
        return f"No XBRL balance sheet data available for {ticker}."

    lines = [f"=== Balance Sheet — {ticker} (last 3 fiscal year-ends) ==="]
    years_seen = set()
    for series in data.values():
        for obs in series:
            years_seen.add(obs.get("fy"))
    years = sorted([y for y in years_seen if y])[-3:]

    for concept, series in data.items():
        by_year = {obs.get("fy"): obs.get("val") for obs in series}
        row = f"  {concept:<22}"
        for y in years:
            val = by_year.get(y)
            row += f"  FY{y}: ${val/1e6:,.0f}M" if val is not None else f"  {y}: N/A"
        lines.append(row)
    return "\n".join(lines)


def format_cash_flow(ticker: str) -> str:
    data = get_cash_flow(ticker, years=3)
    if not data or not any(data.values()):
        return f"No XBRL cash flow data available for {ticker}."

    lines = [f"=== Cash Flow Statement — {ticker} (last 3 fiscal years) ==="]
    years_seen = set()
    for series in data.values():
        for obs in series:
            years_seen.add(obs.get("fy"))
    years = sorted([y for y in years_seen if y])[-3:]

    for concept, series in data.items():
        by_year = {obs.get("fy"): obs.get("val") for obs in series}
        row = f"  {concept:<22}"
        for y in years:
            val = by_year.get(y)
            row += f"  FY{y}: ${val/1e6:,.0f}M" if val is not None else f"  {y}: N/A"
        lines.append(row)

    # Compute FCF if both OCF and Capex are present
    ocf_series = data.get("OperatingCashFlow", [])
    capex_series = data.get("CapitalExpenditure", [])
    if ocf_series and capex_series:
        ocf_by_year = {obs.get("fy"): obs.get("val") for obs in ocf_series}
        capex_by_year = {obs.get("fy"): obs.get("val") for obs in capex_series}
        fcf_row = f"  {'FreeCashFlow (OCF-Capex)':<22}"
        for y in years:
            ocf = ocf_by_year.get(y)
            capex = capex_by_year.get(y)
            if ocf is not None and capex is not None:
                fcf = ocf - capex
                fcf_row += f"  FY{y}: ${fcf/1e6:,.0f}M"
            else:
                fcf_row += f"  {y}: N/A"
        lines.append(fcf_row)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "O"
    print(f"Testing SEC client with ticker: {ticker}\n")

    cik = get_cik(ticker)
    print(f"CIK: {cik}\n")

    print(format_income_statement(ticker))
    print()
    print(format_balance_sheet(ticker))
    print()
    print(format_cash_flow(ticker))
    print()

    sections = extract_10k_sections(ticker)
    print(f"=== 10-K Sections Found: {len(sections)} ===")
    for name, text in sections.items():
        print(f"  {name}: {len(text)} chars")
