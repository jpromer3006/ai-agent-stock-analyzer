"""
sector_rs.py — Chapter 8 lightweight sector relative-strength ranking.

Weinstein Chapter 8 ("No Stock is an Island") argues that a strong stock
in a weak sector is fighting the current, and that even a Stage 2
breakout should be examined against the sector's own stage and momentum.

This module is the lightweight version of the planned Phase 3 Sector
Rotation Strategist. It computes 60-day return for the eleven SPDR
sector ETFs relative to SPY and returns a rank table that the Chartist
Strategist consumes as one of its confidence inputs.

Public interface:
    get_sector_ranking(use_cache=True) -> SectorRanking
    rank_for_ticker(ticker, stock_category) -> dict
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sector ETF universe and StockCategory mapping
# ---------------------------------------------------------------------------

SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLI":  "Industrials",
    "XLE":  "Energy",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
    "XLC":  "Communication Services",
}

# Map ml/tickers.py StockCategory values to the closest sector ETF.
# Generic and Infrastructure use Industrials as a proxy.
CATEGORY_TO_ETF = {
    "REIT":               "XLRE",
    "INFRASTRUCTURE_EPC": "XLI",
    "BANK":               "XLF",
    "TECH":               "XLK",
    "ENERGY":             "XLE",
    "CONSUMER":           "XLY",     # default to discretionary
    "HEALTHCARE":         "XLV",
    "GENERIC":            "XLI",
}

LOOKBACK_DAYS = 60
BENCHMARK = "SPY"

_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "sector_rs"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_FILE = _CACHE_DIR / "ranking.json"
_CACHE_TTL_SEC = 6 * 60 * 60         # 6 hours


@dataclass
class SectorRanking:
    as_of_date: Optional[str] = None
    benchmark_return_pct: float = 0.0
    rows: list[dict] = field(default_factory=list)   # one per ETF, sorted best first
    error: Optional[str] = None

    def rank_of(self, etf: str) -> int:
        """Return the 1-based rank of an ETF, or 0 if missing."""
        for i, r in enumerate(self.rows):
            if r["etf"] == etf:
                return i + 1
        return 0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _load_cached() -> Optional[SectorRanking]:
    if not _CACHE_FILE.exists():
        return None
    age = time.time() - _CACHE_FILE.stat().st_mtime
    if age > _CACHE_TTL_SEC:
        return None
    try:
        with open(_CACHE_FILE) as f:
            data = json.load(f)
        return SectorRanking(**data)
    except Exception:
        return None


def _save_cache(r: SectorRanking) -> None:
    try:
        with open(_CACHE_FILE, "w") as f:
            json.dump(asdict(r), f, default=str)
    except Exception as exc:
        logger.warning(f"sector_rs cache save failed: {exc}")


# ---------------------------------------------------------------------------
# Compute ranking
# ---------------------------------------------------------------------------

def _fetch_recent_close(symbol: str, days: int) -> Optional[pd.Series]:
    try:
        import yfinance as yf
        df = yf.Ticker(symbol).history(period=f"{days + 30}d",
                                        interval="1d", auto_adjust=True)
        if df is None or df.empty:
            return None
        return df["Close"]
    except Exception as exc:
        logger.warning(f"yfinance fetch failed for {symbol}: {exc}")
        return None


def get_sector_ranking(use_cache: bool = True) -> SectorRanking:
    """Compute the eleven-sector rank table over a 60-day lookback."""
    if use_cache:
        cached = _load_cached()
        if cached is not None:
            return cached

    bench = _fetch_recent_close(BENCHMARK, LOOKBACK_DAYS)
    if bench is None or len(bench) < LOOKBACK_DAYS:
        return SectorRanking(error="benchmark history unavailable")

    bench_ret = float(bench.iloc[-1] / bench.iloc[-LOOKBACK_DAYS] - 1.0)
    rows: list[dict] = []

    for etf, label in SECTOR_ETFS.items():
        series = _fetch_recent_close(etf, LOOKBACK_DAYS)
        if series is None or len(series) < LOOKBACK_DAYS:
            continue
        ret = float(series.iloc[-1] / series.iloc[-LOOKBACK_DAYS] - 1.0)
        rs = ret - bench_ret                    # excess return vs SPY
        rows.append({
            "etf": etf,
            "sector": label,
            "return_pct": round(ret, 4),
            "rs_vs_spy": round(rs, 4),
        })

    rows.sort(key=lambda r: r["rs_vs_spy"], reverse=True)
    as_of = bench.index[-1].strftime("%Y-%m-%d") if len(bench) else None

    result = SectorRanking(
        as_of_date=as_of,
        benchmark_return_pct=round(bench_ret, 4),
        rows=rows,
    )
    _save_cache(result)
    return result


def rank_for_ticker(ticker: str, stock_category: Optional[str] = None,
                    use_cache: bool = True) -> dict:
    """Return rank context for a single ticker.

    If `stock_category` is provided (one of the StockCategory enum values
    as a string), it maps through CATEGORY_TO_ETF. Otherwise we attempt
    to look up the sector from data.tickers.UNIVERSE.

    Returns dict with keys: etf, sector, rank, total, return_pct, rs_vs_spy,
    is_tailwind (rank <= 4), is_headwind (rank >= 8), narrative.
    """
    out: dict = {
        "etf": None, "sector": None, "rank": 0, "total": 0,
        "return_pct": 0.0, "rs_vs_spy": 0.0,
        "is_tailwind": False, "is_headwind": False, "narrative": "",
    }

    # Resolve sector ETF
    if stock_category is None:
        try:
            from data.tickers import UNIVERSE
            meta = UNIVERSE.get(ticker.upper())
            if meta is not None:
                stock_category = meta.category.value
        except Exception:
            pass
    if stock_category is None:
        out["narrative"] = (
            f"{ticker}: sector unknown, Chapter 8 rotation check skipped."
        )
        return out
    etf = CATEGORY_TO_ETF.get(stock_category)
    if etf is None:
        out["narrative"] = f"{ticker}: no ETF mapping for {stock_category}."
        return out

    ranking = get_sector_ranking(use_cache=use_cache)
    if ranking.error:
        out["narrative"] = f"Sector ranking unavailable: {ranking.error}"
        return out

    rank = ranking.rank_of(etf)
    total = len(ranking.rows)
    matching = next((r for r in ranking.rows if r["etf"] == etf), None)
    if matching is None:
        out["narrative"] = f"{etf} not in current ranking."
        return out

    is_tailwind = rank <= 4
    is_headwind = rank >= 8

    # Phrase the verdict based on BOTH the rank (rotation leadership)
    # and the sign of rs_vs_spy (relative to the benchmark). A sector can
    # lead the rotation while still trailing SPY if one mega-cap is
    # carrying the index — we describe that case honestly.
    rs_signed = matching["rs_vs_spy"]
    rs_word = "outperforming" if rs_signed > 0 else "trailing"
    rs_abs = abs(rs_signed)
    if is_tailwind:
        narrative = (
            f"{matching['sector']} ranks #{rank} of {total} sectors over "
            f"the last 60 days, {rs_word} SPY by {rs_abs:.1%}. "
            f"{'Tailwind' if rs_signed > 0 else 'Relative tailwind (top-of-pack despite the SPY drag)'} "
            f"in {ticker}'s favor."
        )
    elif is_headwind:
        narrative = (
            f"{matching['sector']} ranks #{rank} of {total} sectors, "
            f"{rs_word} SPY by {rs_abs:.1%}. Even a strong stock in this "
            f"sector is fighting the rotation (Chapter 8 headwind)."
        )
    else:
        narrative = (
            f"{matching['sector']} ranks #{rank} of {total}, "
            f"{rs_word} SPY by {rs_abs:.1%}. Middle of the rotation, "
            f"neutral context."
        )

    out.update({
        "etf": etf,
        "sector": matching["sector"],
        "rank": rank,
        "total": total,
        "return_pct": matching["return_pct"],
        "rs_vs_spy": matching["rs_vs_spy"],
        "is_tailwind": is_tailwind,
        "is_headwind": is_headwind,
        "narrative": narrative,
    })
    return out


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    r = get_sector_ranking(use_cache=False)
    if r.error:
        print(f"ERROR: {r.error}")
    else:
        print(f"Sector rotation as of {r.as_of_date} "
              f"(SPY 60d return {r.benchmark_return_pct:+.1%})")
        for i, row in enumerate(r.rows):
            print(f"  #{i+1:>2}  {row['etf']:<5}  {row['sector']:<26}  "
                  f"60d {row['return_pct']:+.1%}  RS {row['rs_vs_spy']:+.1%}")

        print("\nPer-ticker rank examples:")
        for t in ["NVDA", "O", "INTC", "XOM", "WMT"]:
            ctx = rank_for_ticker(t, use_cache=True)
            print(f"  {t}: {ctx['narrative']}")
