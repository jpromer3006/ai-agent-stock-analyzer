"""
journal.py — Decision-time snapshot of every scan.

The Trader scan is stateless: it recomputes every signal from fresh data
each run. That makes any after-the-fact review of a past pick unsafe,
because recomputing on today's history lets hindsight leak into what was
supposedly a past observation.

This module fixes that. After every freshly computed scan, it freezes a
snapshot of each ticker's signal values exactly as they were at scan
time, into one file per day:

    data/journal/picks_YYYY-MM-DD.json

The review / attribution workflow (a later step) reads these files and
compares the stored decision-time price against a later outcome price.
It never re-runs analyze_stage on a past pick, so the feedback loop is
look-ahead safe by construction.

The snapshot stores VALUES, never a way to recompute them.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bump when the per-pick field set changes, so the review workflow can
# detect older files instead of silently misreading them.
SCHEMA_VERSION = 2

_PROJECT_ROOT = Path(__file__).parent.parent
_JOURNAL_DIR = _PROJECT_ROOT / "data" / "journal"
_JOURNAL_DIR.mkdir(parents=True, exist_ok=True)


def _pick_row(r) -> dict:
    """Freeze one StageResult into a plain dict of decision-time values.

    Uses getattr with defaults so a partial or older StageResult can
    never break the snapshot. The sentiment and earnings fields are
    populated by ml/entry_gates.py for confirmed buys only; non-buys
    carry null values here by design.
    """
    return {
        # identity
        "ticker": r.ticker,
        # decision-time stage signal block
        "stage": getattr(r, "stage", 0),
        "stage_name": getattr(r, "stage_name", ""),
        "action": getattr(r, "action", ""),
        "bull_probability": round(getattr(r, "bull_probability", 0.0), 4),
        "last_close": round(getattr(r, "last_close", 0.0), 4),
        "ma_30w": round(getattr(r, "ma_30w", 0.0), 4),
        "pct_above_ma": round(getattr(r, "pct_above_ma", 0.0), 6),
        "ma_slope_pct": round(getattr(r, "ma_slope_pct", 0.0), 6),
        "mansfield_rs": round(getattr(r, "mansfield_rs", 0.0), 4),
        "volume_surge": round(getattr(r, "volume_surge", 0.0), 4),
        "volume_confirmed": bool(getattr(r, "volume_confirmed", False)),
        "rs_confirmed": bool(getattr(r, "rs_confirmed", False)),
        "weinstein_buy_confirmed": bool(getattr(r, "weinstein_buy_confirmed", False)),
        "setup_type": getattr(r, "setup_type", "n/a"),
        "resistance_grade": getattr(r, "resistance_grade", "n/a"),
        "today_change_pct": round(getattr(r, "today_change_pct", 0.0), 6),
        # invalidation + since-breakout block
        "is_invalidated": bool(getattr(r, "is_invalidated", False)),
        "stage2_entry_date": getattr(r, "stage2_entry_date", None),
        "stage2_entry_price": round(getattr(r, "stage2_entry_price", 0.0), 4),
        "return_since_breakout": round(getattr(r, "return_since_breakout", 0.0), 6),
        "weeks_held": int(getattr(r, "weeks_held", 0)),
        # entry risk overlay (Step 3a, populated by ml/entry_gates.py)
        "entry_gate_status": getattr(r, "entry_gate_status", "pass"),
        "entry_gate_reasons": list(getattr(r, "entry_gate_reasons", []) or []),
        "entry_quality_score": round(getattr(r, "entry_quality_score", 0.0), 1),
        "entry_quality_grade": getattr(r, "entry_quality_grade", ""),
        # Step 3b: news sentiment + earnings blackout. Populated only
        # for confirmed buys; non-buys carry None here by design.
        "sentiment_score": (
            round(float(getattr(r, "sentiment_score", None)), 4)
            if getattr(r, "sentiment_score", None) is not None
            else None
        ),
        "sentiment_headline_count": int(
            getattr(r, "sentiment_headline_count", 0) or 0
        ),
        "sentiment_as_of": getattr(r, "sentiment_as_of", None),
        "next_earnings_date": getattr(r, "next_earnings_date", None),
        "days_to_earnings": getattr(r, "days_to_earnings", None),
    }


def _market_regime_label() -> str:
    """Best-effort current market regime label. Never raises."""
    try:
        from ml.market_context import compute_market_regime
        regime = compute_market_regime()
        if regime.error:
            return "UNKNOWN"
        return regime.regime
    except Exception as exc:
        logger.warning(f"journal: market regime lookup failed: {exc}")
        return "UNKNOWN"


def record_scan_snapshot(report) -> Optional[str]:
    """Freeze a scan into data/journal/picks_YYYY-MM-DD.json.

    One row per non-error result. One file per day; the latest scan of
    the day overwrites earlier ones (last write wins), with scan_ts
    recording exactly when the snapshot was taken.

    Returns the file path written, or None if there was nothing to write.
    """
    rows = [_pick_row(r) for r in report.results if not getattr(r, "error", None)]
    if not rows:
        return None

    scan_ts = getattr(report, "as_of", None) or (
        datetime.utcnow().isoformat() + "Z"
    )
    snapshot_date = scan_ts[:10]  # YYYY-MM-DD

    envelope = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_date": snapshot_date,
        "scan_ts": scan_ts,
        "market_regime": _market_regime_label(),
        "total_picks": len(rows),
        "picks": rows,
    }

    path = _JOURNAL_DIR / f"picks_{snapshot_date}.json"
    with open(path, "w") as f:
        json.dump(envelope, f, indent=2)
    logger.info(f"journal: wrote {len(rows)} picks to {path.name}")
    return str(path)


def load_snapshot(snapshot_date: str) -> Optional[dict]:
    """Load one day's snapshot envelope. `snapshot_date` is YYYY-MM-DD.

    Returns the envelope dict (with a `picks` list), or None if no
    snapshot exists for that date.
    """
    path = _JOURNAL_DIR / f"picks_{snapshot_date}.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as exc:
        logger.warning(f"journal: could not read {path.name}: {exc}")
        return None


def list_snapshot_dates() -> list[str]:
    """All snapshot dates on disk, newest first (YYYY-MM-DD strings)."""
    dates = []
    for p in _JOURNAL_DIR.glob("picks_*.json"):
        dates.append(p.stem.replace("picks_", ""))
    return sorted(dates, reverse=True)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from ml.batch_scanner import scan_universe

    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["WELL", "MTZ", "ORA", "MSFT"]
    print(f"Scanning {len(tickers)} tickers, then snapshotting...")
    report = scan_universe(tickers, use_cache=False)
    path = record_scan_snapshot(report)
    print(f"Snapshot written: {path}")
    if path:
        env = load_snapshot(path[-15:-5])  # picks_YYYY-MM-DD.json -> date
        if env:
            print(f"  schema v{env['schema_version']}  "
                  f"regime={env['market_regime']}  picks={env['total_picks']}")
