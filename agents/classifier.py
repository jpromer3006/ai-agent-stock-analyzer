"""
classifier.py — Route a ticker to the correct specialist agent.

Strategy:
    1. If ticker is in pre-defined UNIVERSE, use its hardcoded category.
    2. Else fetch yfinance sector/industry metadata.
    3. Apply deterministic keyword rules first (fast, free).
    4. If still ambiguous, fall back to LLM classification (Claude).

Returns a StockCategory enum.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from data.tickers import UNIVERSE, StockCategory

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_FILE = _PROJECT_ROOT / "data" / "cache" / "classifier_cache.json"
_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Rule-based classification from yfinance sector/industry strings
# ---------------------------------------------------------------------------
# Ordered list — first match wins
RULES: list[tuple[StockCategory, list[str]]] = [
    (StockCategory.REIT, [
        "reit", "real estate—", "real estate -", "real estate—diversified",
        "real estate services", "reit—", "reit -"
    ]),
    (StockCategory.BANK, [
        "banks", "banks—", "banks -", "capital markets",
        "financial—credit services", "insurance—diversified"
    ]),
    (StockCategory.INFRASTRUCTURE_EPC, [
        "engineering & construction", "construction",
        "specialty industrial machinery", "infrastructure operations"
    ]),
    (StockCategory.TECH, [
        "software", "information technology services", "semiconductors",
        "internet content", "communication equipment", "computer hardware",
        "consumer electronics", "electronic components", "electronic gaming"
    ]),
    (StockCategory.ENERGY, [
        "oil & gas", "utilities", "renewable utilities",
        "regulated electric", "regulated gas", "utilities—"
    ]),
    (StockCategory.CONSUMER, [
        "discount stores", "grocery stores", "specialty retail",
        "beverages", "household products", "packaged foods",
        "restaurants", "apparel", "home improvement"
    ]),
    (StockCategory.HEALTHCARE, [
        "drug manufacturers", "healthcare plans", "biotechnology",
        "medical devices", "medical instruments", "health information",
        "pharmaceutical"
    ]),
]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _load_cache() -> dict[str, str]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        with open(_CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict[str, str]):
    with open(_CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


# ---------------------------------------------------------------------------
# yfinance sector/industry fetch
# ---------------------------------------------------------------------------
@lru_cache(maxsize=100)
def _get_yf_info(ticker: str) -> dict:
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return {
            "sector": info.get("sector", "") or "",
            "industry": info.get("industry", "") or "",
            "longBusinessSummary": (info.get("longBusinessSummary", "") or "")[:500],
            "longName": info.get("longName", "") or ticker,
        }
    except Exception as exc:
        logger.warning(f"yfinance info fetch failed for {ticker}: {exc}")
        return {"sector": "", "industry": "", "longBusinessSummary": "", "longName": ticker}


def _classify_by_rules(sector: str, industry: str) -> Optional[StockCategory]:
    """Apply deterministic keyword rules to yfinance sector/industry strings."""
    combined = f"{sector} {industry}".lower()
    for category, keywords in RULES:
        for kw in keywords:
            if kw in combined:
                return category
    return None


# ---------------------------------------------------------------------------
# LLM fallback classifier
# ---------------------------------------------------------------------------
_LLM_SYSTEM_PROMPT = """\
You are a stock category classifier. Given a company's sector, industry, and description, \
classify it into EXACTLY ONE of these categories:

REIT — Real estate investment trusts (industrial, retail, residential, data center, cell tower, healthcare REITs)
INFRASTRUCTURE_EPC — Engineering, procurement, construction, utility contractors, specialty industrial
BANK — Commercial banks, investment banks, insurance companies
TECH — Software, hardware, internet services, semiconductors, telecom equipment
ENERGY — Oil & gas, utilities (electric, gas, renewable), pipelines
CONSUMER — Retail, food & beverage, household products, restaurants, consumer discretionary
HEALTHCARE — Pharma, biotech, medical devices, health insurance, healthcare services
GENERIC — Conglomerates, industrial equipment, aerospace, transportation, media — anything not fitting above

Respond with ONLY the category name (e.g., "REIT"). No explanation.
"""


def _classify_by_llm(sector: str, industry: str, description: str, name: str) -> Optional[StockCategory]:
    """Use Claude to classify when rules fail."""
    try:
        import anthropic
    except ImportError:
        return None

    # Load API key: Keychain first, then env
    api_key = _get_anthropic_key()
    if not api_key:
        return None

    client = anthropic.Anthropic(api_key=api_key)

    user = (
        f"Company: {name}\n"
        f"Sector: {sector}\n"
        f"Industry: {industry}\n"
        f"Description: {description}"
    )

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=20,
            system=_LLM_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        logger.warning(f"LLM classification failed: {exc}")
        return None

    text = resp.content[0].text.strip().upper()
    try:
        return StockCategory[text]
    except KeyError:
        logger.warning(f"LLM returned invalid category: {text}")
        return None


def _get_anthropic_key() -> Optional[str]:
    """Load ANTHROPIC_API_KEY from Keychain, env, or .env."""
    import os
    import subprocess

    # Try Keychain
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "ANTHROPIC_API_KEY", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass

    # Try env / .env
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass

    return os.environ.get("ANTHROPIC_API_KEY", "").strip() or None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def classify(ticker: str, use_llm: bool = True) -> StockCategory:
    """
    Classify a ticker into a StockCategory.

    Priority:
        1. Pre-defined UNIVERSE (hardcoded categories)
        2. Cached previous classification
        3. Rule-based on yfinance sector/industry
        4. LLM classification (if enabled and available)
        5. Default to GENERIC
    """
    ticker = ticker.upper()

    # 1. Pre-defined universe
    meta = UNIVERSE.get(ticker)
    if meta:
        return meta.category

    # 2. Cache
    cache = _load_cache()
    if ticker in cache:
        try:
            return StockCategory[cache[ticker]]
        except KeyError:
            pass

    # 3-4. Resolve via yfinance + rules/LLM
    info = _get_yf_info(ticker)
    sector = info["sector"]
    industry = info["industry"]

    category = _classify_by_rules(sector, industry)

    if category is None and use_llm:
        category = _classify_by_llm(
            sector, industry, info["longBusinessSummary"], info["longName"]
        )

    if category is None:
        category = StockCategory.GENERIC

    # Cache the result
    cache[ticker] = category.value
    _save_cache(cache)

    return category


def explain_classification(ticker: str) -> dict:
    """Return classification + the reasoning inputs for UI display."""
    ticker = ticker.upper()
    meta = UNIVERSE.get(ticker)
    if meta:
        return {
            "ticker": ticker,
            "category": meta.category.value,
            "source": "predefined_universe",
            "company_name": meta.company_name,
            "sub_sector": meta.sub_sector,
        }

    info = _get_yf_info(ticker)
    category = classify(ticker)
    return {
        "ticker": ticker,
        "category": category.value,
        "source": "yfinance_lookup",
        "company_name": info["longName"],
        "sector": info["sector"],
        "industry": info["industry"],
    }


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_tickers = [
        "O",        # REIT (universe)
        "PWR",      # Infra (universe)
        "AAPL",     # Tech (not in universe, needs lookup)
        "C",        # Bank (not in universe)
        "PEP",      # Consumer (not in universe)
        "GLAD",     # Less known ticker
    ]
    for t in test_tickers:
        result = explain_classification(t)
        print(f"{t:<6}  {result['category']:<20}  {result.get('industry', result.get('sub_sector', ''))}")
