"""
universe_scanner.py - Cache-backed, dual-resolution Weinstein scan (Phase 2).

The legacy `batch_scanner.scan_universe` fetches each ticker live from yfinance,
which caps it at small watchlists. This module scans the full dynamic universe
by reading the local price cache (`data/price_cache`) and feeding the cached
bars straight into the existing `analyze_stage(df=...)`, so 100% of the Weinstein
math, gates, and StageResult shape are reused, not reinvented. It also produces
the same `ScanReport` type, so every downstream consumer keeps working.

Two resolutions, one cache:
  * Weekly stage: analyze_stage computes the 30-week (150-day) MA from the
    cached daily bars -> stage 1/2/3/4 for every name.
  * Daily breakout (speed two): `fast_breakouts` surfaces fresh, confirmed
    Stage 2 setups, the late-stage-2 momentum names like SPCX.

    PYTHONPATH=. python3 ml/universe_scanner.py     # sample self-test
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Optional

from data import price_cache
from ml.batch_scanner import ScanReport
try:                                    # optional refinements; absent in some versions
    from ml.batch_scanner import _attach_resistance, _attach_transition_watch
except ImportError:
    _attach_resistance = _attach_transition_watch = None
from ml.stage_analyzer import StageResult, analyze_stage

logger = logging.getLogger(__name__)


def _scan_one(ticker: str, attach_resistance: bool) -> StageResult:
    """Analyze one ticker from cached bars. No network for the stage itself."""
    df = price_cache.get_history(ticker, max_stale_days=price_cache.DEFAULT_STALE_DAYS)
    if df is None:
        return StageResult(
            ticker=ticker, stage=0, stage_name="Unknown",
            bull_probability=0.5, action="NO DATA", confidence=0.0,
            error=f"No cached price history for {ticker}", error_kind="data",
        )
    r = analyze_stage(ticker, df=df, use_cache=False)
    # Resistance/transition refinements only matter for stages 2 and 3. They
    # may touch the network, so they are opt-in for a pure-cache bulk scan and
    # belong to the finalist tier.
    if attach_resistance and _attach_resistance and r.stage in (2, 3) and not r.error:
        try:
            r = _attach_resistance(r)
            r = _attach_transition_watch(r)
        except Exception:
            pass
    return r


def scan_cached(
    symbols: list[str],
    max_workers: int = 8,
    attach_resistance: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> ScanReport:
    """Stage-analyze every symbol from the local cache, in parallel."""
    symbols = list(dict.fromkeys(s.upper() for s in symbols))
    results: list[StageResult] = []
    total = len(symbols)
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_scan_one, s, attach_resistance): s for s in symbols}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                logger.exception("scan crashed for %s", s)
                results.append(StageResult(
                    ticker=s, stage=0, stage_name="Unknown",
                    bull_probability=0.5, action="ERROR", confidence=0.0,
                    error=str(exc), error_kind="exception",
                ))
            completed += 1
            if progress_callback:
                try:
                    progress_callback(completed, total, s)
                except Exception:
                    pass

    ok = sum(1 for r in results if not r.error)
    return ScanReport(
        as_of=datetime.utcnow().isoformat() + "Z",
        total_tickers=total, successful=ok, failed=total - ok, results=results,
    )


def fast_breakouts(report: ScanReport) -> list[StageResult]:
    """Daily breakout layer (speed two): fresh, confirmed Stage 2 setups.

    Reuses the report's own `confirmed_buys` (Stage 2, both gates pass, not
    extended) when available, else falls back to the gate flags. These are the
    late-stage-2 momentum names the weekly-only design used to miss.
    """
    cb = getattr(report, "confirmed_buys", None)
    if cb is not None:
        return list(cb)
    return [r for r in report.results
            if r.stage == 2 and getattr(r, "weinstein_buy_confirmed", False)
            and getattr(r, "setup_type", "") == "fresh"]


def scan_full_universe(
    filtered: bool = False, limit: Optional[int] = None, refresh: bool = False,
    **scan_kwargs,
) -> ScanReport:
    """Convenience: pull the dynamic universe and scan it from cache.

    refresh=True repopulates the price cache first (the daily cron path).
    """
    from data.universe import get_universe
    symbols = list(get_universe(filtered=filtered).keys())
    if limit:
        symbols = symbols[:limit]
    if refresh:
        price_cache.refresh_prices(symbols)
    return scan_cached(symbols, **scan_kwargs)


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # Representative cross-sector sample incl. the historically missed SPCX.
    sample = [
        "SPCX", "AAPL", "NVDA", "MSFT", "PLTR", "O", "PWR", "EME", "FSLR",
        "ENPH", "JPM", "XOM", "LLY", "TSLA", "AMD", "SMCI", "ANET", "VRT",
        "COST", "WMT", "PG", "KO", "T", "PFE", "DIS",
    ]
    print(f"Refreshing cache for {len(sample)} sample names ...")
    qrep = price_cache.refresh_prices(sample)
    print("Cache quality:", qrep.summary())

    print("\nScanning from cache ...")
    report = scan_cached(sample, attach_resistance=False)
    buckets = report.stage_buckets
    print(f"\nAs of {report.as_of}")
    print(f"Scanned {report.total_tickers}  ok={report.successful}  failed={report.failed}")
    print("Stage distribution:",
          {f"Stage {k}": len(v) for k, v in buckets.items()})

    print("\nStage 2 leaderboard (top 10):")
    for r in report.stage2_breakouts[:10]:
        flag = "BUY" if getattr(r, "weinstein_buy_confirmed", False) else "watch"
        print(f"  {r.ticker:6} {r.stage_name:10} bull={r.bull_probability:.2f} "
              f"RS={r.mansfield_rs:+.1f} vol={r.volume_surge:.1f}x [{flag}]")

    fb = fast_breakouts(report)
    print(f"\nFast breakouts (fresh confirmed Stage 2): {[r.ticker for r in fb]}")

    spcx = next((r for r in report.results if r.ticker == "SPCX"), None)
    if spcx:
        print(f"\nSPCX -> Stage {spcx.stage} ({spcx.stage_name}), "
              f"action={spcx.action}, bull={spcx.bull_probability:.2f}"
              + (f", error={spcx.error}" if spcx.error else ""))


if __name__ == "__main__":
    _selftest()
