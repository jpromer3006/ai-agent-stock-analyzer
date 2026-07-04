"""
alpha_vantage_client.py — Alpha Vantage data wrapper.

Currently exposes:

    next_earnings_av(ticker) -> (iso_date, eps_estimate, time_of_day)

Alpha Vantage's EARNINGS_CALENDAR endpoint returns upcoming earnings
within a 3 month horizon as CSV. Coverage is broader than yfinance for
the mid cap names the scanner cares about. The free tier rate limit is
strict: 25 requests per day and 5 per minute, so this module:

  * caches under data/cache/alpha_vantage/earnings_{ticker}.json
    with a 24 hour TTL
  * detects the rate limit message and returns None so the caller can
    fall back to yfinance
  * never raises

To find DY's tomorrow morning print, that is the one finding that
drove the Step 3b pivot from FMP to AV.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "alpha_vantage"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_CACHE_TTL_HOURS = 24
_TIMEOUT_S = 10
_BASE_URL = "https://www.alphavantage.co/query"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _api_key() -> Optional[str]:
    """Read ALPHA_VANTAGE_API_KEY from environment or .env."""
    key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if key:
        return key
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("ALPHA_VANTAGE_API_KEY="):
                    return line.strip().split("=", 1)[1] or None
    return None


def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"earnings_{ticker.upper()}.json"


def _read_cache(ticker: str) -> Optional[dict]:
    path = _cache_path(ticker)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            blob = json.load(f)
        cached_at = datetime.fromisoformat(blob["cached_at"].rstrip("Z"))
        if (datetime.utcnow() - cached_at) > timedelta(hours=_CACHE_TTL_HOURS):
            return None
        return blob
    except Exception:
        return None


def _write_cache(ticker: str, payload: dict) -> None:
    payload = {**payload, "cached_at": datetime.utcnow().isoformat() + "Z"}
    try:
        with open(_cache_path(ticker), "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as exc:
        logger.debug(f"alpha_vantage cache write failed for {ticker}: {exc}")


def _looks_rate_limited(text: str) -> bool:
    """AV returns a CSV with a single "Information" row when rate limited.

    The first cell is "Information" and the rest of the line is a long
    sentence about the daily / per minute limit.
    """
    head = text[:80].lower()
    return ("information" in head and "limit" in text.lower()) or \
           ("thank you for using alpha vantage" in head)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def next_earnings_av(ticker: str
                     ) -> tuple[Optional[str], Optional[float], Optional[str]]:
    """Return (iso_date, eps_estimate, time_of_day) for the next print.

    Any of the three can be None if AV has no data, the key is missing,
    or the response is rate limited. Cached for 24 hours.
    """
    ticker = ticker.upper()

    cached = _read_cache(ticker)
    if cached is not None:
        return (cached.get("date"),
                cached.get("eps_estimate"),
                cached.get("time_of_day"))

    key = _api_key()
    if not key:
        logger.debug("alpha_vantage: no API key in env, skipping")
        return None, None, None

    params = {
        "function": "EARNINGS_CALENDAR",
        "symbol": ticker,
        "horizon": "3month",
        "apikey": key,
    }

    try:
        r = requests.get(_BASE_URL, params=params, timeout=_TIMEOUT_S)
    except Exception as exc:
        logger.warning(f"alpha_vantage: request failed for {ticker}: {exc}")
        return None, None, None

    if r.status_code != 200:
        logger.warning(f"alpha_vantage: HTTP {r.status_code} for {ticker}")
        return None, None, None

    text = r.text or ""
    if _looks_rate_limited(text):
        logger.warning(f"alpha_vantage: rate limited on {ticker}")
        # Do not cache rate limit responses; caller may retry tomorrow.
        return None, None, None

    iso_date: Optional[str] = None
    eps_est: Optional[float] = None
    tod: Optional[str] = None
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        today_iso = date.today().isoformat()
        future = [row for row in rows
                  if row.get("reportDate", "") > today_iso]
        # Some companies show today's date too; keep them.
        today_rows = [row for row in rows
                      if row.get("reportDate", "") == today_iso]
        future = today_rows + future
        if future:
            future.sort(key=lambda x: x.get("reportDate", ""))
            row = future[0]
            iso_date = row.get("reportDate") or None
            est_raw = row.get("estimate", "")
            try:
                eps_est = float(est_raw) if est_raw else None
            except ValueError:
                eps_est = None
            tod = (row.get("timeOfTheDay") or "").strip() or None
    except Exception as exc:
        logger.warning(f"alpha_vantage: parse failed for {ticker}: {exc}")

    _write_cache(ticker, {
        "ticker": ticker,
        "date": iso_date,
        "eps_estimate": eps_est,
        "time_of_day": tod,
    })
    return iso_date, eps_est, tod


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys, time
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["DY", "MTZ", "GS"]
    for t in tickers:
        d, est, tod = next_earnings_av(t)
        print(f"{t:<6} date={d}  est={est}  time={tod}")
        time.sleep(13)  # AV free tier: 5 per minute = 12 sec spacing
