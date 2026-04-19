"""
batch_scanner.py — Parallel Weinstein Stage Analysis across a universe.

Scans a list of tickers in parallel threads, returns results sorted
by bull probability (descending). Designed for morning pre-market
screening: "show me the Stage 2 breakouts and Stage 4 breakdowns."
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ml.stage_analyzer import StageResult, TradeSetup, analyze_stage

logger = logging.getLogger(__name__)

# Disk cache for batch scans (15-min TTL)
_PROJECT_ROOT = Path(__file__).parent.parent
_SCAN_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "scans"
_SCAN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_SCAN_CACHE_TTL = 15 * 60  # 15 minutes


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


def _cache_key(tickers: list[str]) -> str:
    """Stable hash for a ticker list (order-insensitive)."""
    sig = ",".join(sorted(t.upper() for t in tickers))
    return hashlib.sha1(sig.encode()).hexdigest()[:16]


def _serialize_report(report: "ScanReport") -> dict:
    return {
        "as_of": report.as_of,
        "total_tickers": report.total_tickers,
        "successful": report.successful,
        "failed": report.failed,
        "results": [
            {
                **{k: v for k, v in asdict(r).items() if k != "trade_setup"},
                "trade_setup": asdict(r.trade_setup) if r.trade_setup else None,
            }
            for r in report.results
        ],
    }


def _deserialize_report(data: dict) -> "ScanReport":
    results = []
    for rd in data.get("results", []):
        ts_dict = rd.pop("trade_setup", None)
        ts = TradeSetup(**ts_dict) if ts_dict else None
        r = StageResult(**rd)
        r.trade_setup = ts
        results.append(r)
    return ScanReport(
        as_of=data["as_of"],
        total_tickers=data["total_tickers"],
        successful=data["successful"],
        failed=data["failed"],
        results=results,
    )


def _load_cached(tickers: list[str]) -> Optional["ScanReport"]:
    path = _SCAN_CACHE_DIR / f"{_cache_key(tickers)}.json"
    if not path.exists():
        return None
    age = time.time() - path.stat().st_mtime
    if age > _SCAN_CACHE_TTL:
        return None
    try:
        with open(path) as f:
            return _deserialize_report(json.load(f))
    except Exception:
        return None


def _save_cache(tickers: list[str], report: "ScanReport"):
    path = _SCAN_CACHE_DIR / f"{_cache_key(tickers)}.json"
    try:
        with open(path, "w") as f:
            json.dump(_serialize_report(report), f)
    except Exception as exc:
        logger.warning(f"Could not cache scan: {exc}")


def scan_universe(
    tickers: list[str],
    max_workers: int = 10,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    use_cache: bool = True,
) -> ScanReport:
    """
    Run stage analysis on every ticker in parallel.

    Parameters
    ----------
    tickers : list of ticker symbols
    max_workers : parallel thread count (yfinance is I/O bound, 10 is safe)
    progress_callback : optional fn(completed, total, last_ticker)
    use_cache : if True, return cached scan within TTL (default 15 min)

    Returns
    -------
    ScanReport
    """
    if use_cache:
        cached = _load_cached(tickers)
        if cached is not None:
            if progress_callback:
                try:
                    progress_callback(len(tickers), len(tickers), "(from cache)")
                except Exception:
                    pass
            return cached

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
    report = ScanReport(
        as_of=datetime.utcnow().isoformat() + "Z",
        total_tickers=total,
        successful=ok,
        failed=total - ok,
        results=results,
    )
    if use_cache:
        _save_cache(tickers, report)
    return report


def clear_scan_cache() -> int:
    """Clear disk-cached scans. Returns count cleared."""
    count = 0
    for p in _SCAN_CACHE_DIR.glob("*.json"):
        try:
            p.unlink()
            count += 1
        except Exception:
            pass
    return count


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
