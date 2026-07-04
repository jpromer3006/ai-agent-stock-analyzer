"""
sectors.py - Deterministic sector classification from SEC SIC codes.

The portfolio engine needs a sector for every name (for diversification caps).
The legacy 80-ticker list had hand-coded sectors; the 11,528-name dynamic universe
did not, so most names showed UNKNOWN. This module fixes that with the SEC's own
Standard Industrial Classification (SIC) code, which every filer carries, mapped
to coarse GICS-like sectors. Free, deterministic, point-in-time safe, no LLM.

Fallback to yfinance .info["sector"] for ETFs and non-SEC names. Cached to disk.

    PYTHONPATH=. python3 data/sectors.py
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_CACHE = Path(__file__).resolve().parent / "cache" / "sectors.json"
_CACHE.parent.mkdir(parents=True, exist_ok=True)
SEC_UA = os.environ.get("SEC_USER_AGENT", "OROS Research admin@adminaillc.com")
_mem: dict[str, str] = {}


def classify_sic(sic) -> str:
    """Map a 4-digit SIC code to a coarse GICS-like sector."""
    try:
        n = int(sic)
    except (TypeError, ValueError):
        return "Other"
    # specific overrides first
    if n in (2833, 2834, 2835, 2836):
        return "Health Care"            # pharma
    if n == 3674:
        return "Semiconductors"
    if n == 6798:
        return "Real Estate"            # REIT
    if n == 3711:
        return "Consumer Discretionary"  # autos
    if 3570 <= n <= 3579 or 3670 <= n <= 3679 or 7370 <= n <= 7379:
        return "Information Technology"
    if 3820 <= n <= 3851 or 8000 <= n <= 8099:
        return "Health Care"            # medical devices + health services
    if 1300 <= n <= 1399 or 2900 <= n <= 2999:
        return "Energy"
    if 4900 <= n <= 4999:
        return "Utilities"
    if 4800 <= n <= 4899 or 7800 <= n <= 7999:
        return "Communication Services"
    if 6000 <= n <= 6299 or 6700 <= n <= 6799:
        return "Financials"
    if 6300 <= n <= 6499:
        return "Financials"             # insurance
    if 6500 <= n <= 6599:
        return "Real Estate"
    if 2000 <= n <= 2199 or n in (5400, 5410, 5411):
        return "Consumer Staples"
    if 2800 <= n <= 2899 or 1000 <= n <= 1299 or 3300 <= n <= 3399 or 2600 <= n <= 2699:
        return "Materials"
    if 1500 <= n <= 1799 or 3400 <= n <= 3569 or 3580 <= n <= 3669 or \
       3700 <= n <= 3799 or 4000 <= n <= 4799 or 8700 <= n <= 8799:
        return "Industrials"
    if 5000 <= n <= 5999 or 7000 <= n <= 7299:
        return "Consumer Discretionary"
    return "Other"


def _load_cache() -> dict:
    global _mem
    if _mem:
        return _mem
    if _CACHE.exists():
        try:
            _mem = json.loads(_CACHE.read_text())
        except Exception:
            _mem = {}
    return _mem


def _save_cache() -> None:
    try:
        _CACHE.write_text(json.dumps(_mem))
    except Exception:
        pass


def _sic_from_sec(cik: str) -> Optional[str]:
    try:
        req = urllib.request.Request(f"https://data.sec.gov/submissions/CIK{cik}.json",
                                     headers={"User-Agent": SEC_UA})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
        return str(d.get("sic") or "")
    except Exception as e:  # noqa: BLE001
        logger.debug("SEC sic fetch failed for CIK %s: %s", cik, e)
        return None


def get_sector(ticker: str, cik: Optional[str] = None) -> str:
    ticker = ticker.upper()
    cache = _load_cache()
    if ticker in cache:
        return cache[ticker]

    sector = None
    if cik:
        sic = _sic_from_sec(cik)
        if sic:
            sector = classify_sic(sic)
    if sector in (None, "Other"):
        try:
            import yfinance as yf
            s = (yf.Ticker(ticker).info or {}).get("sector")
            if s:
                # Normalize yfinance labels to the same buckets as the SIC map so
                # the diversification cap treats e.g. "Financial Services" == "Financials".
                _YF = {"Financial Services": "Financials", "Technology": "Information Technology",
                       "Healthcare": "Health Care", "Consumer Cyclical": "Consumer Discretionary",
                       "Consumer Defensive": "Consumer Staples", "Basic Materials": "Materials"}
                sector = _YF.get(s, s)
        except Exception:
            pass
    sector = sector or "Other"
    cache[ticker] = sector
    _save_cache()
    return sector


def _selftest() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    from data.universe import fetch_sec_cik_map
    cikmap = fetch_sec_cik_map()
    for t in ["AMAT", "ACLS", "AEHR", "ALGM", "NVDA", "JPM", "LLY", "XOM", "O", "KO"]:
        print(f"  {t:6} -> {get_sector(t, cikmap.get(t))}")


if __name__ == "__main__":
    _selftest()
