"""
price_cache.py - Local OHLCV cache so OROS can scan the full universe for free.

Why
---
The legacy path fetched price history one ticker at a time inside
`stage_analyzer._fetch_history` (a yfinance call per name). That is fine for an
80-name watchlist and impossible for ~8,000. This module pulls daily OHLCV in
batches, caches each ticker to parquet, and serves it back instantly. The
Weinstein stage runs on a 30-week (150 trading day) MA derived from these daily
bars, so a single daily cache feeds both the weekly stage scan and the daily
breakout scan (the dual resolution in the plan).

Cadence (Phase 7 cron):
  * Daily after the close: `refresh_prices(universe_symbols)`.
  * Each scan run reads the cache via `get_history`, no network.

Resilience: batched downloads with retry/backoff, a stale-cache fallback so a
throttled fetch reuses the last good bars, and a DataQualityReport per refresh.

    PYTHONPATH=. python3 data/price_cache.py        # small self-test
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "prices" / "daily"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
DEFAULT_PERIOD = "2y"          # enough for a 150-day MA + 52-week range
DEFAULT_STALE_DAYS = 4         # weekend + a holiday tolerance


@dataclass
class DataQualityReport:
    requested: int = 0
    fetched: int = 0           # freshly written this run
    from_stale: int = 0        # reused an old cache after a failed fetch
    empty: int = 0             # no data anywhere
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"requested={self.requested} fetched={self.fetched} "
                f"stale_reused={self.from_stale} empty={self.empty} "
                f"failed={len(self.failed)}")


def _path(ticker: str) -> Path:
    safe = ticker.upper().replace("/", "_")
    return _CACHE_DIR / f"{safe}.parquet"


def _age_days(p: Path) -> float:
    return (time.time() - p.stat().st_mtime) / 86400.0


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def get_history(ticker: str, max_stale_days: Optional[float] = None) -> Optional[pd.DataFrame]:
    """Return cached daily OHLCV for `ticker`, or None if absent.

    If `max_stale_days` is set and the cache is older, still returns the data
    (a scan on slightly stale bars beats no scan) but logs it as stale.
    """
    p = _path(ticker)
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        if df is None or df.empty:
            return None
        if max_stale_days is not None and _age_days(p) > max_stale_days:
            logger.debug("stale cache for %s (%.1fd)", ticker, _age_days(p))
        return df
    except Exception as exc:  # noqa: BLE001
        logger.warning("read failed for %s: %s", ticker, exc)
        return None


def cached_symbols() -> set[str]:
    return {p.stem.upper() for p in _CACHE_DIR.glob("*.parquet")}


# ---------------------------------------------------------------------------
# Write / refresh
# ---------------------------------------------------------------------------
def _normalize(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Coerce a yfinance frame to the [Open,High,Low,Close,Volume] shape."""
    if df is None or df.empty:
        return None
    df = df.rename(columns={c: c.title() for c in df.columns})
    have = [c for c in _COLUMNS if c in df.columns]
    if "Close" not in have or "Volume" not in have:
        return None
    out = df[have].dropna(how="all")
    if out.empty:
        return None
    # Normalize the index to a tz-aware America/New_York DatetimeIndex.
    # yfinance's bulk download returns tz-naive daily dates while
    # Ticker().history() (used for the SPY benchmark inside the analyzer) is
    # tz-aware NY. Matching the tz here lets the analyzer's merge align dates.
    idx = pd.to_datetime(out.index)
    try:
        out.index = idx.tz_localize("America/New_York") if idx.tz is None \
            else idx.tz_convert("America/New_York")
    except Exception:
        out.index = idx.tz_localize(None)
    return out


def refresh_prices(
    symbols: Iterable[str],
    period: str = DEFAULT_PERIOD,
    batch_size: int = 200,
    max_retries: int = 3,
    pause: float = 1.0,
) -> DataQualityReport:
    """Bulk-download daily OHLCV for `symbols` and cache to parquet.

    Batched and backed off so the free yfinance path survives at scale. On a
    failed batch the existing cache is left in place (stale fallback).
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed; cannot refresh prices.")
        return DataQualityReport()

    syms = list(dict.fromkeys(s.upper() for s in symbols))
    rep = DataQualityReport(requested=len(syms))

    for i in range(0, len(syms), batch_size):
        batch = syms[i:i + batch_size]
        data = None
        for attempt in range(1, max_retries + 1):
            try:
                data = yf.download(
                    batch, period=period, interval="1d", auto_adjust=True,
                    group_by="ticker", threads=True, progress=False,
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("batch %d attempt %d failed: %s", i // batch_size, attempt, exc)
                time.sleep(pause * attempt)
        if data is None:
            # whole batch failed: keep any existing caches as stale fallback
            for s in batch:
                rep.from_stale += int(_path(s).exists())
                if not _path(s).exists():
                    rep.failed.append(s)
            continue

        for s in batch:
            try:
                # Select the ticker level whenever columns are a MultiIndex.
                # Keying off len(batch) was a bug: newer yfinance returns
                # MultiIndex columns ('QQQ','Close') with group_by="ticker"
                # even for a SINGLE-ticker batch, so every one-symbol refresh
                # silently failed in _normalize ('tuple' has no .title).
                sub = data[s] if isinstance(data.columns, pd.MultiIndex) else data
                norm = _normalize(sub)
                if norm is None:
                    if _path(s).exists():
                        rep.from_stale += 1
                    else:
                        rep.empty += 1
                    continue
                norm.to_parquet(_path(s))
                rep.fetched += 1
            except Exception:
                if _path(s).exists():
                    rep.from_stale += 1
                else:
                    rep.failed.append(s)
        time.sleep(pause)

    logger.info("price refresh: %s", rep.summary())
    return rep


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sample = ["SPCX", "AAPL", "NVDA", "O", "PWR", "PLTR", "ZZZZ_FAKE"]
    print("Refreshing sample:", sample)
    rep = refresh_prices(sample)
    print("Quality:", rep.summary())
    for s in sample:
        df = get_history(s)
        if df is not None:
            print(f"  {s:10} rows={len(df)} last={df.index[-1].date()} close={df['Close'].iloc[-1]:.2f}")
        else:
            print(f"  {s:10} (no cache)")


if __name__ == "__main__":
    _selftest()
