"""
earnings_calendar.py — Next earnings date lookup for the entry gate.

The Trader scan should never recommend a fresh entry inside the
earnings blackout window. Weinstein didn't address earnings directly,
but a Stage 2 breakout into an earnings miss is exactly the silent
killer the entry gate is meant to surface.

This module:

  1. Calls yfinance for an upcoming earnings date.
  2. Caches the result per ticker for one day under
     data/cache/earnings/{ticker}.json.
  3. Returns (next_date_iso, trading_days_to_earnings) or (None, None)
     if yfinance returns nothing.

Never raises. A missing earnings date must never block a scan; the
entry gate just won't fire its earnings reason.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "earnings"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Refresh the cached earnings date this often.
_CACHE_TTL_HOURS = 24

# Earnings blackout window. 5 trading days lines up with most
# pre-earnings drift research.
EARNINGS_BLACKOUT_TRADING_DAYS = 5


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}.json"


def _read_cache(ticker: str) -> Optional[dict]:
    path = _cache_path(ticker)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            blob = json.load(f)
        ts = blob.get("cached_at")
        if not ts:
            return None
        cached_at = datetime.fromisoformat(ts.rstrip("Z"))
        if (datetime.utcnow() - cached_at) > timedelta(hours=_CACHE_TTL_HOURS):
            return None
        return blob
    except Exception as exc:
        logger.debug(f"earnings cache read failed for {ticker}: {exc}")
        return None


def _write_cache(ticker: str, payload: dict) -> None:
    payload = {**payload, "cached_at": datetime.utcnow().isoformat() + "Z"}
    try:
        with open(_cache_path(ticker), "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as exc:
        logger.debug(f"earnings cache write failed for {ticker}: {exc}")


# ---------------------------------------------------------------------------
# Trading day distance
# ---------------------------------------------------------------------------

def _trading_days_between(start: date, end: date) -> int:
    """Approximate trading-day distance using Mon to Fri weekdays.

    Ignores US market holidays on purpose. Holiday detection would need
    pandas_market_calendars; one or two day slop is acceptable for a
    blackout window that is already a 5-day band of caution, not a
    knife-edge rule.
    """
    if end <= start:
        return 0
    days = 0
    cur = start
    while cur < end:
        cur = cur + timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _from_alpha_vantage(ticker: str, today: date
                        ) -> tuple[Optional[str], Optional[int]]:
    """Try Alpha Vantage as the primary earnings source.

    AV's EARNINGS_CALENDAR covers mid cap names that yfinance and FMP
    miss (DY, MTZ and similar). Returns (None, None) on missing key,
    rate limit, or any error.
    """
    try:
        from data.alpha_vantage_client import next_earnings_av
        iso, _eps, _tod = next_earnings_av(ticker)
        if iso:
            nxt = date.fromisoformat(iso)
            return iso, _trading_days_between(today, nxt)
    except Exception as exc:
        logger.debug(f"earnings_calendar: AV lookup failed for "
                     f"{ticker}: {exc}")
    return None, None


def _from_yfinance(ticker: str, today: date
                   ) -> tuple[Optional[str], Optional[int]]:
    """Yfinance fallback."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        ed = getattr(t, "earnings_dates", None)
        if ed is not None and hasattr(ed, "empty") and not ed.empty:
            idx = ed.index
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            future = [d.date() for d in idx if d.date() > today]
            if future:
                nxt = min(future)
                return nxt.isoformat(), _trading_days_between(today, nxt)
    except Exception as exc:
        logger.warning(f"earnings_calendar: yfinance lookup failed "
                       f"for {ticker}: {exc}")
    return None, None


def next_earnings(ticker: str,
                  as_of: Optional[date] = None
                  ) -> tuple[Optional[str], Optional[int]]:
    """Return (next_earnings_date_iso, trading_days_to_earnings).

    Tries Alpha Vantage first (better mid cap coverage), falls back to
    yfinance, returns (None, None) if both miss. Caches the resolved
    answer (and which source won) for 24 hours so the AV free tier
    rate limit is not hit on every scan. Never raises.
    """
    ticker = ticker.upper()
    today = as_of or date.today()

    cached = _read_cache(ticker)
    if cached is not None:
        return cached.get("next_earnings_date"), cached.get("days_to_earnings")

    source = "none"
    iso, days = _from_alpha_vantage(ticker, today)
    if iso is not None:
        source = "alpha_vantage"
    else:
        iso, days = _from_yfinance(ticker, today)
        if iso is not None:
            source = "yfinance"

    _write_cache(ticker, {
        "ticker": ticker,
        "next_earnings_date": iso,
        "days_to_earnings": days,
        "source": source,
    })
    return iso, days


def is_inside_blackout(days_to_earnings: Optional[int]) -> bool:
    """True when an entry today would land inside the blackout window."""
    if days_to_earnings is None:
        return False
    return 0 <= days_to_earnings <= EARNINGS_BLACKOUT_TRADING_DAYS


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "AAPL", "MSFT"]
    for t in tickers:
        iso, days = next_earnings(t)
        if iso is None:
            print(f"{t:<6} no earnings date available")
        else:
            blackout = " (inside blackout)" if is_inside_blackout(days) else ""
            print(f"{t:<6} next earnings {iso} ({days} trading days){blackout}")
