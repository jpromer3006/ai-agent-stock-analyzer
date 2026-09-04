"""
scan_report.py - The scan artifact: one grade of the OROS 500, produced once
per run and read by everything downstream.

Why this module exists (JP, 2026-09-04). The pipeline used to build the
portfolio FIRST (run_orchestrated computed stages privately while scoring)
and tally the "scan" AFTERWARD (record_breadth walked the same names a
second time). Two stage passes, in sequence, with the book already decided
before the scan was recorded. The narration says "the scanner grades every
chart, then the book is built from what it found," and that was simply not
the order the code ran in.

One flow: build_scan() grades the universe once and returns a report. The
portfolio ranks from report.rows, breadth is a groupby over report.rows,
the regime is a function of report.rows, and the video reads the file. No
consumer recomputes a stage. A book that names a ticker absent from the
scan is a bug that can be detected instead of a warning nobody reads.

Two guarantees the old path could not make:

  1. CONFIRMED COUNT. Benchmarks are removed BEFORE the cap is applied
     (they used to be stripped after, which is the whole reason the desk
     said "495 candidate names" every day), and any name that fails to
     grade is replaced from the next rank down until OROS_CAP names are
     confirmed. Every substitution is logged, so the count is auditable
     rather than assumed.

  2. ONE TIMESTAMP, ONE BAR. Every row carries the date of the bar it was
     graded on. A stale cache entry can no longer masquerade as today's
     move, and a mixed-freshness scan is visible instead of silent.

Educational, not financial advice.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_J = Path(__file__).resolve().parent.parent / "data" / "journal"
_SCAN_DIR = _J / "scans"
_LATEST = _J / "scan_latest.json"

# Index and broad-market funds are not candidates. They are the yardstick.
# Removed BEFORE the cap so the scan confirms a full OROS_CAP of real names.
BENCHMARKS = {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "RSP", "VXX"}

# The universe rule, stated once, in the words the desk uses on air.
UNIVERSE_RULE = (
    "Every US-listed common stock and ETF is ranked nightly by 20-day dollar "
    "volume, filtered to names above $5 trading more than $5M a day; the top "
    "{cap} that grade cleanly are scanned. The list rebuilds every night, so "
    "a name earns its place or loses it."
)


@dataclass
class ScanRow:
    ticker: str
    stage: int
    stage_name: str
    bar_date: str                  # the session this grade was computed on
    close: float
    ma_30w: float
    pct_above_ma: float
    ma_slope_pct: float
    mansfield_rs: float
    volume_surge: float
    today_change_pct: float
    ret_5d: float
    ret_21d: float
    dollar_vol_20d: float
    confidence: float
    bull_probability: float
    action: str
    invalidated: bool


@dataclass
class ScanReport:
    as_of: str                     # when the scan ran (ISO, UTC)
    session_date: str              # the trading session it represents
    cap: int
    confirmed: int
    rows: list = field(default_factory=list)
    skipped: list = field(default_factory=list)      # [{ticker, reason}]
    substitutions: int = 0
    stale_rows: int = 0            # graded on a bar older than session_date
    source: str = "local"
    universe_rule: str = ""
    scan_hash: str = ""

    # -- convenience views -------------------------------------------------
    def stage_counts(self) -> dict:
        counts = {"Basing": 0, "Advancing": 0, "Topping": 0, "Declining": 0}
        for r in self.rows:
            name = r["stage_name"] if isinstance(r, dict) else r.stage_name
            if name in counts:
                counts[name] += 1
        return counts

    def invalidated_count(self) -> int:
        return sum(1 for r in self.rows
                   if (r["invalidated"] if isinstance(r, dict) else r.invalidated))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [r if isinstance(r, dict) else asdict(r) for r in self.rows]
        return d


def _bar_date(df) -> Optional[str]:
    try:
        return str(df.index[-1])[:10]
    except Exception:  # noqa: BLE001
        return None


def _returns(df) -> tuple:
    """(5-day, 21-day) simple returns off completed bars, 0.0 when too short."""
    try:
        c = df["Close"].dropna()
        r5 = float(c.iloc[-1] / c.iloc[-6] - 1.0) * 100 if len(c) > 6 else 0.0
        r21 = float(c.iloc[-1] / c.iloc[-22] - 1.0) * 100 if len(c) > 22 else 0.0
        return r5, r21
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _dollar_vol(df) -> float:
    try:
        c, v = df["Close"], df["Volume"]
        return float((c.tail(20) * v.tail(20)).mean())
    except Exception:  # noqa: BLE001
        return 0.0


def _hash_rows(rows: list) -> str:
    """Content hash over (ticker, stage, bar_date) so a book can cite the
    exact scan that produced it and a re-run proves it saw the same market."""
    h = hashlib.sha256()
    for r in rows:
        d = r if isinstance(r, dict) else asdict(r)
        h.update(f"{d['ticker']}|{d['stage']}|{d['bar_date']}".encode())
    return h.hexdigest()[:16]


def build_scan(ranked_symbols: list, cap: Optional[int] = None,
               session_date: Optional[str] = None) -> ScanReport:
    """Grade down the ranked universe until `cap` names are CONFIRMED.

    `ranked_symbols` is the liquidity-ranked list (data.universe). Names that
    error, return no stage, or have no usable bar are skipped with a reason
    and replaced by the next rank down. That is the difference between "we
    scanned 500" and "we sliced 500 and 495 survived."
    """
    from data import price_cache
    from ml.oros_config import OROS_CAP, run_source
    from ml.stage_analyzer import analyze_stage

    cap = int(cap or OROS_CAP)
    session_date = session_date or f"{datetime.now():%Y-%m-%d}"

    candidates = [s for s in ranked_symbols if s not in BENCHMARKS]
    rows, skipped = [], []

    for sym in candidates:
        if len(rows) >= cap:
            break
        try:
            df = price_cache.get_history(sym)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"ticker": sym, "reason": f"history error: {exc}"[:80]})
            continue
        if df is None or len(df) < 60:
            skipped.append({"ticker": sym, "reason": "insufficient history"})
            continue
        bar = _bar_date(df)
        if not bar:
            skipped.append({"ticker": sym, "reason": "no dated bar"})
            continue
        try:
            r = analyze_stage(sym, df=df, use_cache=True)
        except Exception as exc:  # noqa: BLE001
            skipped.append({"ticker": sym, "reason": f"grade error: {exc}"[:80]})
            continue
        if getattr(r, "error", None) or not getattr(r, "stage_name", None):
            skipped.append({"ticker": sym,
                            "reason": getattr(r, "error", None) or "no stage"})
            continue

        r5, r21 = _returns(df)
        rows.append(ScanRow(
            ticker=sym,
            stage=int(getattr(r, "stage", 0) or 0),
            stage_name=r.stage_name,
            bar_date=bar,
            close=float(getattr(r, "last_close", 0.0) or 0.0),
            ma_30w=float(getattr(r, "ma_30w", 0.0) or 0.0),
            pct_above_ma=float(getattr(r, "pct_above_ma", 0.0) or 0.0),
            ma_slope_pct=float(getattr(r, "ma_slope_pct", 0.0) or 0.0),
            mansfield_rs=float(getattr(r, "mansfield_rs", 0.0) or 0.0),
            volume_surge=float(getattr(r, "volume_surge", 1.0) or 1.0),
            today_change_pct=float(getattr(r, "today_change_pct", 0.0) or 0.0),
            ret_5d=r5,
            ret_21d=r21,
            dollar_vol_20d=_dollar_vol(df),
            confidence=float(getattr(r, "confidence", 0.0) or 0.0),
            bull_probability=float(getattr(r, "bull_probability", 0.0) or 0.0),
            action=str(getattr(r, "action", "") or ""),
            invalidated=bool(getattr(r, "is_invalidated", False)),
        ))

    # Anything consumed beyond the first `cap` candidates is a substitution.
    consumed = len(rows) + len(skipped)
    substitutions = max(0, consumed - cap)
    stale = sum(1 for r in rows if r.bar_date != session_date)

    report = ScanReport(
        as_of=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        session_date=session_date,
        cap=cap,
        confirmed=len(rows),
        rows=[asdict(r) for r in rows],
        skipped=skipped,
        substitutions=substitutions,
        stale_rows=stale,
        source=run_source(),
        universe_rule=UNIVERSE_RULE.format(cap=cap),
    )
    report.scan_hash = _hash_rows(report.rows)

    if report.confirmed < cap:
        logger.warning(
            "SCAN SHORT: %d confirmed of %d requested (%d candidates exhausted, "
            "%d skipped) — the desk must not claim %d",
            report.confirmed, cap, len(candidates), len(skipped), cap)
    if stale:
        logger.warning("SCAN FRESHNESS: %d of %d rows graded on a bar older "
                       "than %s", stale, report.confirmed, session_date)
    logger.info("scan complete: %d confirmed, %d skipped, %d substitutions, "
                "hash %s", report.confirmed, len(skipped), substitutions,
                report.scan_hash)
    return report


def save_scan(report: ScanReport) -> Path:
    """Persist the dated scan and refresh the latest pointer."""
    _SCAN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = report.as_of.replace(":", "").replace("-", "")[:15]
    path = _SCAN_DIR / f"scan_{report.session_date}_{stamp[-6:]}.json"
    payload = json.dumps(report.to_dict(), indent=1)
    path.write_text(payload)
    _LATEST.write_text(payload)
    return path


def load_latest() -> Optional[dict]:
    if not _LATEST.exists():
        return None
    try:
        return json.loads(_LATEST.read_text())
    except Exception:  # noqa: BLE001
        return None
