"""
new_listing_scanner.py - IPO / new-listing momentum track (walled off from Weinstein).

Why this exists
---------------
Weinstein's stage engine needs ~150 trading days for a 30-week MA, so it cannot
stage a freshly listed stock. SPCX (IPO 2026-06-12) is the canonical case: now
visible in the universe, but too young to stage. Missing IPO-era leaders was the
original complaint.

This module is a SEPARATE track for young names, using principles from O'Neil
(the N in CANSLIM: new issues that form an early base and break out on volume)
and Minervini (early-stage leaders, volume expansion on strength). It runs on
daily bars and short moving averages, never the 30-week MA, and it is deliberately
kept out of `stage_analyzer` so it can never weaken the core Weinstein discipline.
It is intentionally conservative: a 5-day-old stock is reported as TOO_NEW, not
bought blindly. A name graduates to the Weinstein engine once it has ~150 days.

These are well-known public trading principles, implemented from scratch, not text
from the books. The book RAG (Phase 3) supplies the cited rationale; this module is
the quantitative gate.

    PYTHONPATH=. python3 ml/new_listing_scanner.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from data import price_cache

logger = logging.getLogger(__name__)

# Weinstein needs this many days; below it a name belongs to this track.
WEINSTEIN_MIN_DAYS = 150

# Tunable gates for the IPO-momentum track.
MIN_DAYS_ASSESS = 15          # under this: TOO_NEW, watch only
BASE_WINDOW = 10              # lookback (days) used to define the early base
TIGHT_BASE_MAX_RANGE = 0.25   # base high/low spread to count as constructive
BREAKOUT_BUFFER = 0.005       # close must clear base high by this much
VOLUME_SURGE_MIN = 1.5        # breakout volume vs short average
EXTENSION_MAX = 0.25          # too far above the short MA = extended
BENCHMARK = "SPY"


@dataclass
class NewListingResult:
    ticker: str
    days_listed: int = 0
    last_close: float = 0.0
    short_ma: float = 0.0
    pct_above_ma: float = 0.0
    rs_vs_spy: float = 0.0          # return since listing minus SPY, percent
    base_high: float = 0.0
    base_low: float = 0.0
    base_range_pct: float = 0.0
    volume_surge: float = 1.0
    breakout: bool = False
    extended: bool = False
    setup: str = "NONE"             # BREAKOUT / EARLY_UPTREND / BASING / WEAK / EXTENDED / TOO_NEW
    score: float = 0.0              # 0..1
    suggested_stop: float = 0.0
    action: str = "Watch"
    notes: list[str] = field(default_factory=list)
    error: str = ""


def _benchmark_return(days: int) -> Optional[float]:
    spy = price_cache.get_history(BENCHMARK)
    if spy is None:
        price_cache.refresh_prices([BENCHMARK])
        spy = price_cache.get_history(BENCHMARK)
    if spy is None or len(spy) < days:
        return None
    c = spy["Close"]
    return float(c.iloc[-1] / c.iloc[-days] - 1.0)


def assess_new_listing(ticker: str, df: Optional[pd.DataFrame] = None) -> NewListingResult:
    """Score one young stock with O'Neil/Minervini-style IPO-momentum gates."""
    ticker = ticker.upper()
    r = NewListingResult(ticker=ticker)
    if df is None:
        df = price_cache.get_history(ticker, max_stale_days=price_cache.DEFAULT_STALE_DAYS)
    if df is None or df.empty:
        r.error = "no cached price history"
        return r

    n = len(df)
    r.days_listed = n
    close = df["Close"]
    vol = df["Volume"]
    r.last_close = float(close.iloc[-1])

    if n >= WEINSTEIN_MIN_DAYS:
        r.setup = "NONE"
        r.action = "Hand to Weinstein engine"
        r.notes.append(f"{n} days of history: old enough for stage analysis.")
        return r

    if n < MIN_DAYS_ASSESS:
        r.setup = "TOO_NEW"
        r.action = "Watch"
        need = MIN_DAYS_ASSESS - n
        r.notes.append(f"Only {n} trading days listed. Needs ~{need} more to assess an early base.")
        return r

    # Short moving average sized to available history.
    ma_win = min(50, max(10, n // 2))
    r.short_ma = float(close.tail(ma_win).mean())
    r.pct_above_ma = (r.last_close / r.short_ma - 1.0) * 100 if r.short_ma else 0.0

    # Early base = prior BASE_WINDOW days excluding today.
    base = df.iloc[-(BASE_WINDOW + 1):-1] if n > BASE_WINDOW else df.iloc[:-1]
    r.base_high = float(base["High"].max())
    r.base_low = float(base["Low"].min())
    r.base_range_pct = (r.base_high / r.base_low - 1.0) if r.base_low else 1.0

    # Volume surge vs the short average.
    vbar = float(vol.tail(ma_win).mean()) or 1.0
    r.volume_surge = float(vol.iloc[-1]) / vbar if vbar else 1.0

    # Relative strength since listing vs SPY over the same window.
    stock_ret = float(close.iloc[-1] / close.iloc[0] - 1.0)
    bench_ret = _benchmark_return(n)
    r.rs_vs_spy = (stock_ret - bench_ret) * 100 if bench_ret is not None else 0.0

    r.extended = r.pct_above_ma > EXTENSION_MAX * 100
    tight_base = r.base_range_pct <= TIGHT_BASE_MAX_RANGE
    clears = r.last_close > r.base_high * (1 + BREAKOUT_BUFFER)
    vol_ok = r.volume_surge >= VOLUME_SURGE_MIN
    r.breakout = bool(clears and vol_ok and not r.extended)

    # Suggested stop: base low (breakout) or short MA (uptrend).
    r.suggested_stop = round(r.base_low if r.breakout else min(r.short_ma, r.base_low), 2)

    # Classify + score.
    score = 0.0
    if r.rs_vs_spy > 0: score += 0.30
    if r.last_close > r.short_ma: score += 0.20
    if tight_base: score += 0.20
    if vol_ok: score += 0.20
    if not r.extended: score += 0.10

    if r.extended:
        r.setup, r.action = "EXTENDED", "Avoid (extended)"
        r.notes.append(f"{r.pct_above_ma:.0f}% above the {ma_win}d MA: too extended to start.")
    elif r.breakout and r.rs_vs_spy > 0:
        r.setup, r.action = "BREAKOUT", "Buy candidate (IPO base breakout)"
        r.notes.append(f"Cleared {BASE_WINDOW}d base high {r.base_high:.2f} on {r.volume_surge:.1f}x volume.")
    elif tight_base and r.last_close >= r.short_ma:
        r.setup, r.action = "BASING", "Watch for breakout"
        r.notes.append(f"Constructive base ({r.base_range_pct*100:.0f}% range). Trigger: close > {r.base_high:.2f} on volume.")
    elif r.last_close > r.short_ma and r.rs_vs_spy > 0:
        r.setup, r.action = "EARLY_UPTREND", "Watch"
        r.notes.append("Above short MA with positive RS, but no clean base/breakout yet.")
    else:
        r.setup, r.action = "WEAK", "Avoid"
        r.notes.append("Below short MA or lagging the market.")

    r.score = round(score, 2)
    return r


def scan_new_listings(symbols: list[str]) -> list[NewListingResult]:
    """Assess every symbol; return only genuine young names, ranked by score."""
    out = []
    for s in symbols:
        res = assess_new_listing(s)
        if res.error or res.setup in ("NONE",):     # skip no-data and graduated names
            continue
        out.append(res)
    out.sort(key=lambda r: (r.setup != "BREAKOUT", -r.score))
    return out


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sample = ["SPCX", "SPY", "AAPL", "NVDA", "PLTR"]
    price_cache.refresh_prices(sample)
    print(f"{'ticker':7} {'days':>4} {'setup':12} {'RS%':>7} {'%>MA':>6} {'vol':>5}  action")
    for s in sample:
        r = assess_new_listing(s)
        if r.error:
            print(f"{s:7} {'-':>4} {'ERROR':12} {'':7} {'':6} {'':5}  {r.error}")
            continue
        print(f"{s:7} {r.days_listed:4} {r.setup:12} {r.rs_vs_spy:7.1f} "
              f"{r.pct_above_ma:6.1f} {r.volume_surge:5.1f}  {r.action}")
        for note in r.notes:
            print(f"        - {note}")


if __name__ == "__main__":
    _selftest()
