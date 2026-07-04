"""
trade_auditor.py — Tier 1 Trade Auditor: post-decision attribution.

The Trader scan writes a journal of every pick with its decision-time
signal block (ml/journal.py, schema v2). The journal is write only; on
its own it cannot tell us whether our gates actually predicted anything.

This agent fixes that. It runs offline against the journal and:

  1. For every snapshot, fetches realized prices at +1, +5, +20 trading
     days and writes data/journal/outcomes_YYYY-MM-DD.json. One file
     per snapshot, immutable history.

  2. Aggregates every outcomes file into data/journal/audit_report.md
     with hit rate slices by entry_quality_grade, entry_gate_status,
     and setup_type, plus top winners / losers for surprise detection.

What this agent does NOT do
---------------------------
* Never calls analyze_stage on a past ticker. Recomputing today's MA
  on a past pick leaks hindsight. The auditor reads frozen journal
  values only.
* Never auto deploys a new signal. Tier 2 (LLM powered hypothesis
  generation) will be added later, and even then the user decides.
* Never claims significance on tiny samples. Buckets under 3 picks are
  shown but flagged as "n too small".

Hit thresholds (generous on 1d, stringent on 20d)
-------------------------------------------------
  1d  hit  >= +0.5%
  5d  hit  >= +2.0%
  20d hit  >= +5.0%

These are calibration knobs; tune in HIT_THRESHOLDS when sample size
gets meaningful.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_JOURNAL_DIR = _PROJECT_ROOT / "data" / "journal"
_PRICE_CACHE = _JOURNAL_DIR / "outcome_prices"
_PRICE_CACHE.mkdir(parents=True, exist_ok=True)

# Forward looking windows, in trading days.
LOOKAHEAD_TRADING_DAYS = [1, 5, 20]

# A pick is a "hit" if its forward return clears these thresholds.
HIT_THRESHOLDS = {1: 0.005, 5: 0.020, 20: 0.050}

# Small sample warning bucket.
MIN_SAMPLE_FOR_INFERENCE = 3


# ---------------------------------------------------------------------------
# Price fetch with per-ticker, per-snapshot cache
# ---------------------------------------------------------------------------

def _price_cache_path(ticker: str, anchor: date) -> Path:
    return _PRICE_CACHE / f"{ticker}_{anchor.isoformat()}.json"


def _fetch_forward_prices(ticker: str, anchor: date,
                          max_trading_days: int = 25
                          ) -> Optional[list[dict]]:
    """Return a list of {date, close} for trading days >= anchor.

    Looks ahead enough calendar days to cover max_trading_days trading
    sessions, accounting for weekends. Caches the slice per ticker per
    anchor date forever (the past is immutable).
    """
    cache = _price_cache_path(ticker, anchor)
    if cache.exists():
        try:
            with open(cache) as f:
                return json.load(f)
        except Exception:
            pass

    try:
        import yfinance as yf
        # Reserve ~1.5x calendar days to cover the trading day window.
        end = anchor + timedelta(days=int(max_trading_days * 1.5) + 5)
        df = yf.Ticker(ticker).history(
            start=anchor, end=end, auto_adjust=True, interval="1d"
        )
        if df is None or df.empty:
            return None
        # Strip timezone, normalize to plain dates.
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        rows = []
        for ts, row in df.iterrows():
            d = ts.date() if hasattr(ts, "date") else ts
            if d < anchor:
                continue
            try:
                close = float(row["Close"])
            except Exception:
                continue
            rows.append({"date": d.isoformat(), "close": round(close, 4)})
            if len(rows) >= max_trading_days + 1:
                break
        if not rows:
            return None
        with open(cache, "w") as f:
            json.dump(rows, f)
        return rows
    except Exception as exc:
        logger.warning(f"trade_auditor: forward price fetch failed for "
                       f"{ticker} @ {anchor}: {exc}")
        return None


def _nth_trading_day(rows: list[dict], n: int,
                     anchor: date) -> Optional[dict]:
    """n-th trading day strictly AFTER anchor.

    rows include the anchor day itself if it was a trading day; we
    filter that out so n=1 always means "the first trading day after
    the snapshot was taken", regardless of whether the snapshot landed
    on a weekday, weekend, or market holiday.
    """
    forward = [r for r in rows if r["date"] > anchor.isoformat()]
    if n < 1 or n > len(forward):
        return None
    return forward[n - 1]


# ---------------------------------------------------------------------------
# Per pick outcome
# ---------------------------------------------------------------------------

def compute_outcome_for_pick(pick: dict, decision_date: date) -> dict:
    """Compute forward returns for one journal pick.

    Reads the decision-time close from the journal (never recomputes),
    fetches realized closes at +1, +5, +20 trading days, and tags
    "hit" against HIT_THRESHOLDS.
    """
    ticker = pick["ticker"]
    decision_close = float(pick.get("last_close", 0.0) or 0.0)
    if decision_close <= 0:
        return {"ticker": ticker, "error": "no decision close in journal"}

    # Fetch one extra trading day to make sure we have +N forward days
    # whether or not anchor itself was a trading day.
    rows = _fetch_forward_prices(
        ticker, decision_date,
        max_trading_days=max(LOOKAHEAD_TRADING_DAYS) + 2,
    )

    forward_count = (
        sum(1 for r in rows if r["date"] > decision_date.isoformat())
        if rows else 0
    )
    out = {
        "ticker": ticker,
        "decision_date": decision_date.isoformat(),
        "decision_close": decision_close,
        "n_forward_rows_available": forward_count,
        "outcomes": {},
    }

    if not rows:
        for n in LOOKAHEAD_TRADING_DAYS:
            out["outcomes"][f"{n}d"] = None
        out["error"] = "no forward price history available"
        return out

    for n in LOOKAHEAD_TRADING_DAYS:
        row = _nth_trading_day(rows, n, decision_date)
        if row is None:
            # Not enough trading days have passed yet (the future).
            out["outcomes"][f"{n}d"] = None
            continue
        close = float(row["close"])
        ret = (close - decision_close) / decision_close
        out["outcomes"][f"{n}d"] = {
            "actual_date": row["date"],
            "close": round(close, 4),
            "return_pct": round(ret, 6),
            "hit": ret >= HIT_THRESHOLDS[n],
            "threshold": HIT_THRESHOLDS[n],
        }
    return out


# ---------------------------------------------------------------------------
# Per snapshot outcome file
# ---------------------------------------------------------------------------

def build_outcomes_for_snapshot(snapshot_date: str) -> Optional[Path]:
    """Process one journal snapshot, write outcomes_YYYY-MM-DD.json."""
    from ml import journal
    env = journal.load_snapshot(snapshot_date)
    if env is None:
        logger.warning(f"trade_auditor: no snapshot for {snapshot_date}")
        return None

    dec_date = date.fromisoformat(snapshot_date)
    outcomes = [compute_outcome_for_pick(pick, dec_date)
                for pick in env["picks"]]

    envelope = {
        "snapshot_date": snapshot_date,
        "as_of_audit": datetime.utcnow().isoformat() + "Z",
        "n_picks": len(outcomes),
        "lookahead_trading_days": LOOKAHEAD_TRADING_DAYS,
        "hit_thresholds": HIT_THRESHOLDS,
        "outcomes": outcomes,
    }
    path = _JOURNAL_DIR / f"outcomes_{snapshot_date}.json"
    with open(path, "w") as f:
        json.dump(envelope, f, indent=2)
    logger.info(f"trade_auditor: wrote outcomes for {snapshot_date} "
                f"({len(outcomes)} picks)")
    return path


def update_all_outcomes() -> int:
    """Rebuild outcomes for every journal snapshot on disk."""
    from ml import journal
    n = 0
    for snapshot_date in journal.list_snapshot_dates():
        try:
            if build_outcomes_for_snapshot(snapshot_date) is not None:
                n += 1
        except Exception as exc:
            logger.warning(f"trade_auditor: skip {snapshot_date}: {exc}")
    return n


# ---------------------------------------------------------------------------
# Aggregate audit report
# ---------------------------------------------------------------------------

def _load_all_audit_rows() -> list[dict]:
    """Flatten every (pick, outcome) pair across all snapshots."""
    from ml import journal
    rows: list[dict] = []
    for outcomes_path in sorted(_JOURNAL_DIR.glob("outcomes_*.json")):
        try:
            with open(outcomes_path) as f:
                env = json.load(f)
        except Exception:
            continue
        snap = journal.load_snapshot(env["snapshot_date"])
        if snap is None:
            continue
        picks_by_ticker = {p["ticker"]: p for p in snap["picks"]}
        for outcome in env["outcomes"]:
            if "error" in outcome and not outcome.get("outcomes"):
                continue
            pick = picks_by_ticker.get(outcome["ticker"])
            if pick is None:
                continue
            rows.append({
                "snapshot_date": env["snapshot_date"],
                "ticker": outcome["ticker"],
                "stage": pick.get("stage"),
                "setup_type": pick.get("setup_type"),
                "entry_quality_grade": pick.get("entry_quality_grade"),
                "entry_gate_status": pick.get("entry_gate_status"),
                "weinstein_buy_confirmed": pick.get("weinstein_buy_confirmed"),
                "sentiment_score": pick.get("sentiment_score"),
                "outcomes": outcome["outcomes"],
            })
    return rows


def _slice(rows: list[dict], key_fn, lookahead_label: str) -> list[dict]:
    buckets: dict = defaultdict(list)
    for r in rows:
        bucket = key_fn(r)
        if bucket is None:
            continue
        out = r["outcomes"].get(lookahead_label)
        if not isinstance(out, dict):
            continue
        buckets[bucket].append(out)
    results = []
    for bucket, outcomes in sorted(buckets.items(), key=lambda kv: str(kv[0])):
        n = len(outcomes)
        hits = sum(1 for o in outcomes if o.get("hit"))
        avg = mean(o["return_pct"] for o in outcomes) if outcomes else 0.0
        results.append({
            "bucket": str(bucket),
            "n": n,
            "hit_rate": hits / n if n else 0.0,
            "avg_return": avg,
            "low_sample": n < MIN_SAMPLE_FOR_INFERENCE,
        })
    return results


def _md_slice_table(slices: list[dict], header: str) -> list[str]:
    lines = [
        f"| {header} | n | hit rate | avg return | note |",
        "|---|---|---|---|---|",
    ]
    for s in slices:
        note = "n too small" if s["low_sample"] else ""
        lines.append(
            f"| {s['bucket']} | {s['n']} | {s['hit_rate']:.0%} | "
            f"{s['avg_return']:+.2%} | {note} |"
        )
    return lines


def aggregate_audit_report() -> Optional[Path]:
    """Build human readable attribution report from all outcomes files."""
    rows = _load_all_audit_rows()
    if not rows:
        logger.warning("trade_auditor: no audit rows on disk")
        return None

    snapshot_dates = sorted({r["snapshot_date"] for r in rows})

    md: list[str] = [
        "# Trade Auditor Report",
        "",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        f"Snapshots analyzed: {len(snapshot_dates)} ({snapshot_dates[0]} to "
        f"{snapshot_dates[-1]})",
        f"Total picks: {len(rows)}",
        f"Confirmed buys: {sum(1 for r in rows if r['weinstein_buy_confirmed'])}",
        "",
        "Educational. Not financial advice. The auditor proposes; the trader decides.",
        "",
    ]

    for la_n in LOOKAHEAD_TRADING_DAYS:
        la = f"{la_n}d"
        md.append(f"## {la} forward returns "
                  f"(hit threshold: {HIT_THRESHOLDS[la_n]:+.1%})")
        md.append("")

        md.append("### By entry quality grade")
        md.extend(_md_slice_table(
            _slice(rows, lambda x: x.get("entry_quality_grade") or "-", la),
            "Grade"))
        md.append("")

        md.append("### By entry gate status")
        md.extend(_md_slice_table(
            _slice(rows, lambda x: x.get("entry_gate_status") or "-", la),
            "Status"))
        md.append("")

        md.append("### By setup type")
        md.extend(_md_slice_table(
            _slice(rows, lambda x: x.get("setup_type") or "-", la),
            "Type"))
        md.append("")

        md.append("### Confirmed buys only, by Q grade")
        md.extend(_md_slice_table(
            _slice([r for r in rows if r["weinstein_buy_confirmed"]],
                   lambda x: x.get("entry_quality_grade") or "-", la),
            "Grade"))
        md.append("")

    # Surprises.
    md.append("## Notable surprises (5 day window)")
    md.append("")
    md.append("Top movers in either direction. Use these to spot signal "
              "gaps. A big winner with Q=C suggests the system underrated "
              "something; a big loser with Q=A suggests a gap in the gate.")
    md.append("")

    five_d_rows = [r for r in rows
                   if isinstance(r["outcomes"].get("5d"), dict)]
    winners = sorted(five_d_rows,
                     key=lambda x: -x["outcomes"]["5d"]["return_pct"])[:5]
    losers = sorted(five_d_rows,
                    key=lambda x: x["outcomes"]["5d"]["return_pct"])[:5]

    def _surprise_table(name: str, rs: list[dict]) -> list[str]:
        lines = [
            f"### {name}",
            "| Date | Ticker | Stage | Setup | Q | Gate | 5d return |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in rs:
            five = r["outcomes"]["5d"]
            lines.append(
                f"| {r['snapshot_date']} | {r['ticker']} | "
                f"{r['stage']} | {r['setup_type']} | "
                f"{r['entry_quality_grade'] or '-'} | "
                f"{r['entry_gate_status']} | "
                f"{five['return_pct']:+.2%} |"
            )
        return lines

    md.extend(_surprise_table("Top 5 winners", winners))
    md.append("")
    md.extend(_surprise_table("Top 5 losers", losers))
    md.append("")

    md.append("---")
    md.append("")
    md.append("Tier 2 (LLM powered hypothesis generation) lands once we "
              "have at least 25 to 30 labeled picks across distinct "
              "snapshots. Until then this report is honest attribution "
              "of the existing gates only.")

    path = _JOURNAL_DIR / "audit_report.md"
    with open(path, "w") as f:
        f.write("\n".join(md))
    logger.info(f"trade_auditor: wrote {path}")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    p = argparse.ArgumentParser(description="Trade Auditor (Tier 1)")
    p.add_argument("--report-only", action="store_true",
                   help="Skip outcome refresh, regenerate the report only.")
    args = p.parse_args()

    if not args.report_only:
        n = update_all_outcomes()
        print(f"Refreshed outcomes for {n} snapshot(s).")

    path = aggregate_audit_report()
    if path:
        print(f"Audit report written: {path}")
    else:
        print("No data yet. Run a few scans first.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    main()
