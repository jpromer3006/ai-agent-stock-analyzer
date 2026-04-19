"""
batch_scanner.py — Parallel Weinstein Stage Analysis across a universe.

Scans a list of tickers in parallel threads, returns results sorted
by bull probability (descending). Designed for morning pre-market
screening: "show me the Stage 2 breakouts and Stage 4 breakdowns."
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from ml.stage_analyzer import StageResult, analyze_stage

logger = logging.getLogger(__name__)


@dataclass
class ScanReport:
    as_of: str
    total_tickers: int
    successful: int
    failed: int
    results: list[StageResult] = field(default_factory=list)

    @property
    def stage_buckets(self) -> dict[int, list[StageResult]]:
        buckets: dict[int, list[StageResult]] = {1: [], 2: [], 3: [], 4: []}
        for r in self.results:
            if r.stage in buckets:
                buckets[r.stage].append(r)
        return buckets

    @property
    def top_bull(self) -> list[StageResult]:
        """Sorted by bull_probability descending."""
        ok = [r for r in self.results if not r.error]
        return sorted(ok, key=lambda r: r.bull_probability, reverse=True)

    @property
    def top_bear(self) -> list[StageResult]:
        """Sorted by bull_probability ascending (most bearish first)."""
        ok = [r for r in self.results if not r.error]
        return sorted(ok, key=lambda r: r.bull_probability)

    @property
    def stage2_breakouts(self) -> list[StageResult]:
        """Stage 2 stocks sorted by bull_probability."""
        return sorted(
            [r for r in self.results if r.stage == 2],
            key=lambda r: r.bull_probability, reverse=True,
        )

    @property
    def stage4_breakdowns(self) -> list[StageResult]:
        """Stage 4 stocks sorted by bull_probability ascending (most bearish)."""
        return sorted(
            [r for r in self.results if r.stage == 4],
            key=lambda r: r.bull_probability,
        )


def scan_universe(
    tickers: list[str],
    max_workers: int = 10,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> ScanReport:
    """
    Run stage analysis on every ticker in parallel.

    Parameters
    ----------
    tickers : list of ticker symbols
    max_workers : parallel thread count (yfinance is I/O bound, 10 is safe)
    progress_callback : optional fn(completed, total, last_ticker)

    Returns
    -------
    ScanReport
    """
    from datetime import datetime
    results: list[StageResult] = []
    completed = 0
    total = len(tickers)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(analyze_stage, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker = futures[fut]
            try:
                r = fut.result()
            except Exception as exc:
                from ml.stage_analyzer import StageResult
                r = StageResult(
                    ticker=ticker, stage=0, stage_name="Unknown",
                    bull_probability=0.5, action="ERROR", confidence=0.0,
                    error=str(exc),
                )
            results.append(r)
            completed += 1
            if progress_callback:
                try:
                    progress_callback(completed, total, ticker)
                except Exception:
                    pass

    ok = sum(1 for r in results if not r.error)
    return ScanReport(
        as_of=datetime.utcnow().isoformat() + "Z",
        total_tickers=total,
        successful=ok,
        failed=total - ok,
        results=results,
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from ml.stage_analyzer import format_result

    # Default: sample across sectors from the main universe
    default_tickers = [
        "NVDA", "MSFT", "GOOGL", "META", "CRM",       # Tech
        "JPM", "BAC", "WFC", "GS", "MS",              # Banks
        "O", "PLD", "AMT", "WELL", "VICI",             # REITs
        "XOM", "CVX", "NEE", "DUK", "OXY",             # Energy
        "WMT", "COST", "KO", "MCD", "PG",              # Consumer
        "UNH", "LLY", "JNJ", "PFE", "ABBV",            # Healthcare
    ]
    tickers = sys.argv[1:] if len(sys.argv) > 1 else default_tickers

    print(f"Scanning {len(tickers)} tickers via Weinstein Stage Analysis...\n")

    def on_progress(done, total, ticker):
        print(f"  [{done}/{total}] {ticker}", end="\r")

    report = scan_universe(tickers, progress_callback=on_progress)
    print(f"\n\nCompleted in {report.successful} successful, {report.failed} failed")
    print("=" * 100)

    print("\n🟢 TOP 10 BULL (Stage 2 ranked by bull probability):")
    for r in report.top_bull[:10]:
        print("  " + format_result(r))

    print("\n🔴 TOP 10 BEAR (Stage 4 or weakest bull prob):")
    for r in report.top_bear[:10]:
        print("  " + format_result(r))

    print("\n📊 STAGE DISTRIBUTION:")
    for stage, bucket in sorted(report.stage_buckets.items()):
        tickers_in = ", ".join(r.ticker for r in bucket[:10])
        name = {1: "Basing", 2: "Advancing", 3: "Topping", 4: "Declining"}[stage]
        print(f"  Stage {stage} ({name:<10}): {len(bucket):>3} tickers   {tickers_in}{'...' if len(bucket) > 10 else ''}")
