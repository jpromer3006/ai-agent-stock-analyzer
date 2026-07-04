"""
momentum_screener.py — Discover fresh Stage 2 candidates by momentum.

The Trader Mode watchlist is static — same tickers every scan. Per Stan
Weinstein, only ~5–10% of stocks have actionable setups at any given
time, so a stale watchlist often produces zero buy candidates even when
the broader market has plenty of breakouts elsewhere.

This module addresses that by computing **60-day price momentum** across
a curated pool of ~150 large-cap U.S. equities and returning the top N
movers. Those tickers can then be loaded into a Trader Mode watchlist
and scanned with the standard Weinstein gates.

Why 60-day return as the screen?
    Weinstein's Mansfield RS line is essentially "stock return / index
    return" — a momentum-adjusted ratio. Pure 60-day price return is a
    fast pre-screen that captures the same idea: stocks pulling away
    from the market are the universe of plausible Stage 2 candidates.

Pool composition:
    - S&P 100 (largest U.S. equities, broad sector spread)
    - Sector leaders not always in S&P 100 (e.g., select REITs, infra,
      and clean-energy names that align with the project's universe)
    The pool is a hardcoded list — no new API dependencies. Update
    annually if rotation matters.

Caching:
    Results cached to disk for 4 hours so re-running the screener
    doesn't hammer yfinance.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "momentum"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_TTL = 4 * 60 * 60  # 4 hours


# ---------------------------------------------------------------------------
# Curated candidate pool — S&P 100 + sector leaders relevant to the project
# ---------------------------------------------------------------------------

# S&P 100 (large-cap, broadly diversified)
_SP100 = [
    "AAPL", "ABBV", "ABT", "ACN", "ADBE", "AIG", "AMGN", "AMT", "AMZN", "AVGO",
    "AXP", "BA", "BAC", "BK", "BKNG", "BLK", "BMY", "BRK-B", "C", "CAT",
    "CHTR", "CL", "CMCSA", "COF", "COP", "COST", "CRM", "CSCO", "CVS", "CVX",
    "DE", "DHR", "DIS", "DOW", "DUK", "EMR", "EXC", "F", "FDX", "GD",
    "GE", "GILD", "GM", "GOOG", "GOOGL", "GS", "HD", "HON", "IBM", "INTC",
    "JNJ", "JPM", "KHC", "KO", "LIN", "LLY", "LMT", "LOW", "MA", "MCD",
    "MDLZ", "MDT", "MET", "META", "MMM", "MO", "MRK", "MS", "MSFT", "NEE",
    "NFLX", "NKE", "NVDA", "ORCL", "PEP", "PFE", "PG", "PM", "PYPL", "QCOM",
    "RTX", "SBUX", "SCHW", "SO", "SPG", "T", "TGT", "TMO", "TMUS", "TSLA",
    "TXN", "UNH", "UNP", "UPS", "USB", "V", "VZ", "WBA", "WFC", "WMT", "XOM",
]

# Project-specific sector leaders not always in S&P 100
# (REITs, infrastructure/EPC, clean energy, specialty contractors)
_SECTOR_LEADERS = [
    # REITs
    "O", "PLD", "WELL", "EQIX", "PSA", "DLR", "VICI", "ARE", "AVB",
    # Infra / EPC
    "PWR", "EME", "MTZ", "ACM", "FLR", "KBR", "PRIM", "AGX", "DY", "GVA",
    # Clean energy / utilities
    "ENPH", "FSLR", "BE", "PLUG", "AMRC", "ORA", "BKH", "AWK", "MDU",
    # Tech leaders not in S&P 100
    "AMD", "PANW", "SHOP", "SNOW", "PLTR", "CRWD", "NET", "ABNB", "UBER",
    # 2026 AI cohort — foundry, AI compute, datacenter buildout, AI software
    "TSM", "ARM", "ASML", "MRVL", "MTSI", "ANET", "VRT", "SMCI",
    "CRDO", "ALAB", "AI", "DDOG", "MDB", "ZS",
    # Banks / financial
    "JPM", "MS", "C", "GS", "BAC",
    # Consumer / retail
    "DDS", "TJX", "ORLY", "TSCO",
    # Healthcare
    "ISRG", "VRTX", "REGN",
]


def get_screening_pool() -> list[str]:
    """Return deduplicated list of tickers in the momentum screening pool."""
    seen = set()
    pool: list[str] = []
    for t in _SP100 + _SECTOR_LEADERS:
        u = t.upper()
        if u not in seen:
            seen.add(u)
            pool.append(u)
    return pool


# ---------------------------------------------------------------------------
# Per-ticker 60-day return fetch
# ---------------------------------------------------------------------------

@dataclass
class MomentumResult:
    ticker: str
    return_60d: float = 0.0       # 60 trading-day total return, e.g. 0.142 = +14.2%
    last_close: float = 0.0
    error: Optional[str] = None


def _fetch_60d_return(ticker: str) -> MomentumResult:
    """Fetch 3-month price history from yfinance and compute 60-day return."""
    try:
        import yfinance as yf
        # auto_adjust=True so dividends/splits don't pollute the return
        df = yf.Ticker(ticker).history(period="3mo", auto_adjust=True)
        if df is None or df.empty or len(df) < 30:
            return MomentumResult(ticker=ticker, error="insufficient history")
        close = df["Close"]
        first = float(close.iloc[0])
        last = float(close.iloc[-1])
        if first == 0:
            return MomentumResult(ticker=ticker, error="zero starting price")
        ret = (last - first) / first
        return MomentumResult(
            ticker=ticker,
            return_60d=ret,
            last_close=last,
        )
    except Exception as exc:
        return MomentumResult(ticker=ticker, error=str(exc))


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------

def _cache_path(top_n: int) -> Path:
    return _CACHE_DIR / f"momentum_top{top_n}.json"


def _load_cached(top_n: int) -> Optional[list[str]]:
    p = _cache_path(top_n)
    if not p.exists():
        return None
    try:
        if time.time() - p.stat().st_mtime > _CACHE_TTL:
            return None
        with open(p) as f:
            data = json.load(f)
        return data.get("tickers", [])
    except Exception:
        return None


def _save_cache(top_n: int, tickers: list[str], results: list[MomentumResult]) -> None:
    try:
        with open(_cache_path(top_n), "w") as f:
            json.dump({
                "tickers": tickers,
                "details": [
                    {"ticker": r.ticker,
                     "return_60d": r.return_60d,
                     "last_close": r.last_close}
                    for r in results if not r.error
                ],
                "as_of": time.time(),
            }, f)
    except Exception as exc:
        logger.warning(f"momentum cache save failed: {exc}")


def screen_momentum_leaders(
    top_n: int = 50,
    pool: Optional[list[str]] = None,
    max_workers: int = 12,
    use_cache: bool = True,
    progress_callback=None,
) -> list[MomentumResult]:
    """
    Screen the candidate pool for top-N stocks by 60-day price return.

    Parameters
    ----------
    top_n : how many leaders to return (default 50)
    pool : optional override of the candidate pool
    max_workers : parallel yfinance threads (default 12 — yfinance is I/O-bound)
    use_cache : if True, return cached result within 4-hour TTL
    progress_callback : fn(done, total, ticker) called per fetch — for UI spinners

    Returns
    -------
    list[MomentumResult] sorted by return_60d descending, length = top_n
    """
    if use_cache:
        cached = _load_cached(top_n)
        if cached:
            # Reconstruct shallow MomentumResult objects from the cache
            try:
                with open(_cache_path(top_n)) as f:
                    detail_map = {d["ticker"]: d for d in json.load(f).get("details", [])}
                return [
                    MomentumResult(
                        ticker=t,
                        return_60d=detail_map.get(t, {}).get("return_60d", 0.0),
                        last_close=detail_map.get(t, {}).get("last_close", 0.0),
                    )
                    for t in cached
                ]
            except Exception:
                pass  # cache corrupt — fall through and re-screen

    candidates = pool or get_screening_pool()
    results: list[MomentumResult] = []
    total = len(candidates)
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_60d_return, t): t for t in candidates}
        for fut in as_completed(futures):
            r = fut.result()
            done += 1
            if progress_callback:
                try:
                    progress_callback(done, total, r.ticker)
                except Exception:
                    pass
            if not r.error:
                results.append(r)

    # Sort descending by return, take top N
    results.sort(key=lambda r: r.return_60d, reverse=True)
    top = results[:top_n]
    if use_cache and top:
        _save_cache(top_n, [r.ticker for r in top], top)
    return top


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    pool = get_screening_pool()
    print(f"Screening {len(pool)} candidates for top {n} momentum leaders...")
    leaders = screen_momentum_leaders(top_n=n, use_cache=False)
    print(f"\n  Rank  Ticker   60d Return   Last Close")
    print(f"  ─────────────────────────────────────────")
    for i, r in enumerate(leaders, 1):
        print(f"  {i:>4}  {r.ticker:<7}  {r.return_60d:>+8.1%}    ${r.last_close:>8.2f}")
