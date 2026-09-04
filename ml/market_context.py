"""
market_context.py — Stan Weinstein Chapter 8 indicators.

"Using the Best Long-Term Indicators to Spot Bull and Bear Markets"

Weinstein's "No Isolationism" rule: a stock's stage alone is not enough.
You must also know what stage the BROAD MARKET is in, because ~75% of
individual stocks follow the market.

This module produces a single MarketRegime snapshot combining:

    1. Market Stage (1-4) — via analyze_stage("SPY")
       "Stage Analysis for the Market Averages" (Ch. 8)

    2. Momentum (50d vs 200d SMA) — "Measuring the Market's Momentum"
       SPY 50-day above 200-day + both rising = tailwind.

    3. Breadth (Advance/Decline proxy) — "The Advance-Decline Line"
       When a scan is provided, we compute the ratio of Stage 2 +
       healthy-Stage-1 tickers to total tickers. Weinstein's rule:
       if breadth is diverging (market up but fewer stocks participating),
       the rally is weakening.

The final verdict maps to one of five regimes:

    BULL            — market Stage 2, momentum up, breadth > 50%
    BULL_FADING     — market Stage 2/3, momentum slowing, breadth < 50%
    NEUTRAL         — Stage 1, momentum flat
    BEAR_EARLY      — Stage 3, momentum turning down, breadth < 40%
    BEAR            — Stage 4, momentum down, breadth < 30%
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BENCHMARK = "SPY"


@dataclass
class MarketRegime:
    spy_stage: int                      # 1-4
    spy_stage_name: str                 # "Basing" / "Advancing" / ...
    spy_price: float = 0.0
    spy_ma_30w: float = 0.0
    spy_pct_above_ma: float = 0.0       # signed
    spy_ma_slope_pct: float = 0.0       # 4-week slope
    sma_50: float = 0.0
    sma_200: float = 0.0
    momentum_tailwind: bool = False     # 50 > 200 and both rising

    # Near-term tape read. The 30-week MA and the 50/200 cross are slow,
    # lagging signals — they stay bullish for weeks into a top. These two
    # short-window returns let the Market Weather line show that the index
    # is still technically Stage 2 WHILE flagging a sharp recent pullback,
    # instead of asserting a "confirmed uptrend" the user can see is rolling
    # over. near_term_pullback fires on a >2% drop over the last 5 sessions.
    spy_ret_5d: float = 0.0
    spy_ret_20d: float = 0.0
    near_term_pullback: bool = False

    # Breadth (derived from scan if available)
    breadth_pct: Optional[float] = None  # 0-1, share of tickers in Stage 1-2
    breadth_total: int = 0
    breadth_stage2: int = 0
    breadth_stage4: int = 0

    # Final verdict
    regime: str = "NEUTRAL"              # BULL / BULL_FADING / NEUTRAL / BEAR_EARLY / BEAR
    regime_emoji: str = "⚪"
    regime_color: str = "#6c757d"
    regime_headline: str = ""
    regime_action: str = ""              # what it implies for new positions

    as_of: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_market_regime(scan_report: Optional[object] = None) -> MarketRegime:
    """
    Compute the current market regime.

    If `scan_report` is provided (from ml.batch_scanner), breadth is
    computed from its stage buckets. Otherwise breadth is left None.
    """
    from ml.stage_analyzer import analyze_stage

    regime = MarketRegime(
        spy_stage=0, spy_stage_name="Unknown",
        as_of=datetime.utcnow().isoformat() + "Z",
    )

    # 1. SPY stage
    spy = analyze_stage(BENCHMARK)
    if spy.error:
        regime.error = f"SPY data error: {spy.error}"
        return regime

    regime.spy_stage = spy.stage
    regime.spy_stage_name = spy.stage_name
    regime.spy_price = spy.last_close
    regime.spy_ma_30w = spy.ma_30w
    regime.spy_pct_above_ma = spy.pct_above_ma
    regime.spy_ma_slope_pct = spy.ma_slope_pct

    # 2. Momentum (50d vs 200d)
    try:
        import yfinance as yf
        hist = yf.Ticker(BENCHMARK).history(period="1y", auto_adjust=True)
        if hist is not None and not hist.empty and len(hist) >= 200:
            sma50 = float(hist["Close"].tail(50).mean())
            sma200 = float(hist["Close"].tail(200).mean())
            # Simple rising check: compare last 20-day mean to prev 20-day
            last_20 = hist["Close"].tail(20).mean()
            prev_20 = hist["Close"].iloc[-40:-20].mean()
            sma50_rising = last_20 > prev_20
            regime.sma_50 = sma50
            regime.sma_200 = sma200
            regime.momentum_tailwind = (sma50 > sma200) and bool(sma50_rising)

            # Near-term tape read (short windows — these LEAD the slow MAs)
            close = hist["Close"]
            if len(close) >= 21:
                regime.spy_ret_5d = float(close.iloc[-1] / close.iloc[-6] - 1.0)
                regime.spy_ret_20d = float(close.iloc[-1] / close.iloc[-21] - 1.0)
                regime.near_term_pullback = regime.spy_ret_5d <= -0.02
    except Exception as exc:
        logger.warning(f"SPY momentum calc failed: {exc}")

    # 3. Breadth from scan (if provided)
    if scan_report is not None and hasattr(scan_report, "stage_buckets"):
        buckets = scan_report.stage_buckets
        total = sum(len(b) for b in buckets.values())
        if total > 0:
            # BREADTH FIX (JP, 2026-09-04): this used to count Stage 1
            # (BASING) as healthy alongside Stage 2. On 2026-09-04 that
            # turned an honest 45% advancing into 83%, so the
            # `breadth >= 0.5` gate below could never fail and BULL_FADING
            # never fired once in 14 recorded sessions. Basing is not
            # participation; it is waiting. Only Stage 2 advances count.
            healthy = len(buckets[2])
            regime.breadth_pct = healthy / total
            regime.breadth_total = total
            regime.breadth_stage2 = len(buckets[2])
            regime.breadth_stage4 = len(buckets[4])

    # 4. Verdict
    _assign_regime(regime)
    return regime


def _assign_regime(r: MarketRegime):
    """Decide the final regime label, emoji, color, headline, action."""
    stage = r.spy_stage
    tailwind = r.momentum_tailwind
    breadth = r.breadth_pct  # may be None

    # Stage-driven base verdict
    if stage == 2 and tailwind:
        if breadth is None or breadth >= 0.5:
            _set(r, "BULL", "🟢", "#00c853",
                 "Bull market — SPY in Stage 2 with momentum tailwind",
                 "Favor Stage 2 stocks. Weinstein's ideal environment for long entries.")
            return
        else:
            _set(r, "BULL_FADING", "🟡", "#ffc107",
                 "Bull market, but breadth is narrowing",
                 "Be selective. Quality names still work; reduce new risk.")
            return

    if stage == 2 and not tailwind:
        _set(r, "BULL_FADING", "🟡", "#ffc107",
             "SPY Stage 2 but losing momentum",
             "Trim aggressive longs; ride leaders with trailing stops.")
        return

    if stage == 3:
        _set(r, "BEAR_EARLY", "🟠", "#ff9800",
             "Topping — SPY Stage 3. Weinstein: 'no stock is an island.'",
             "Stop adding new longs. Tighten stops. Consider hedges.")
        return

    if stage == 4:
        _set(r, "BEAR", "🔴", "#ff1744",
             "Bear market — SPY Stage 4 with falling MA",
             "Cash is a position. Short setups (Stage 4 stocks) only. "
             "Never fight the 30-week MA.")
        return

    if stage == 1:
        _set(r, "NEUTRAL", "⚪", "#6c757d",
             "Market basing — SPY Stage 1, waiting for direction",
             "Reduce size. Watch for clean Stage 2 breakouts in leaders.")
        return

    _set(r, "NEUTRAL", "⚪", "#6c757d",
         f"Market stage unclear (Stage {stage})",
         "Prefer smaller position sizes until direction clarifies.")


def _set(r: MarketRegime, regime: str, emoji: str, color: str,
         headline: str, action: str):
    r.regime = regime
    r.regime_emoji = emoji
    r.regime_color = color
    r.regime_headline = headline
    r.regime_action = action


# ---------------------------------------------------------------------------
# Pretty formatter
# ---------------------------------------------------------------------------

def format_regime(r: MarketRegime) -> str:
    """Human-readable multi-line summary (used by the agent tool)."""
    if r.error:
        return f"Market regime: ERROR — {r.error}"
    lines = [
        f"=== Market Regime (per Weinstein Ch. 8) ===",
        f"  Verdict:  {r.regime_emoji} {r.regime} — {r.regime_headline}",
        f"  Action:   {r.regime_action}",
        f"",
        f"  SPY Stage:        {r.spy_stage} ({r.spy_stage_name})",
        f"  SPY price:        ${r.spy_price:,.2f}",
        f"  SPY 30W MA:       ${r.spy_ma_30w:,.2f}  "
        f"(price {r.spy_pct_above_ma:+.1%} vs MA, slope {r.spy_ma_slope_pct:+.1%}/mo)",
    ]
    if r.sma_50 > 0 and r.sma_200 > 0:
        momentum = "tailwind ✓" if r.momentum_tailwind else "no tailwind ✗"
        lines.append(
            f"  Momentum (50>200): SMA50 ${r.sma_50:,.2f} vs SMA200 ${r.sma_200:,.2f}  "
            f"[{momentum}]"
        )
    if r.breadth_pct is not None:
        lines.append(
            f"  Breadth:          {r.breadth_pct:.0%} of {r.breadth_total} scanned "
            f"tickers in Stage 1-2  "
            f"(Stage 2: {r.breadth_stage2}, Stage 4: {r.breadth_stage4})"
        )
    lines.append(f"  As of: {r.as_of}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Computing market regime (no breadth)...")
    r = compute_market_regime()
    print(format_regime(r))
    print()
    print("Computing with breadth from a 50-ticker scan...")
    from data.tickers import UNIVERSE
    from ml.batch_scanner import scan_universe
    scan = scan_universe(list(UNIVERSE.keys())[:20])
    r2 = compute_market_regime(scan)
    print(format_regime(r2))


# ---------------------------------------------------------------------------
# Regime from the scan artifact (JP, 2026-09-04)
#
# The episode label used to come from portfolio_engine.detect_regime(): SPY's
# stage plus a VIX nudge, three possible values, no breadth. SPY sits in
# Stage 2 most of the time, so the desk said BULL every single day while
# fewer than half the scanned market was advancing. A label that can only
# take one value is not a signal.
#
# This reads the scan artifact instead, so the number the desk says out loud
# is the number the scan produced, and a viewer can recount it themselves.
# Thresholds are published deliberately: a rule anyone can recompute beats an
# adjective they have to trust.
# ---------------------------------------------------------------------------

# (floor on % of the scan in Stage 2, label, what it implies for new risk)
REGIME_LADDER = (
    (55.0, "BROAD BULL",
     "Most of the market is advancing. Breakouts have company."),
    (45.0, "NARROW BULL",
     "The trend holds but leadership is thin. Be selective, size smaller."),
    (35.0, "MIXED",
     "No side owns the tape. Fewer positions, tighter stops."),
    (0.0, "DEFENSIVE",
     "Participation has broken down. Cash is a position."),
)

_REGIME_FILE = Path(__file__).resolve().parent.parent / "data" / "journal" / "regime_history.json"


def _regime_streak(label: str, session_date: str) -> int:
    """How many consecutive sessions have carried this label, today included.

    Said out loud ("eleventh straight session at narrow bull"), a streak turns
    a slow-moving label into information, and makes the day it finally flips
    land with the weight it deserves.
    """
    try:
        hist = json.loads(_REGIME_FILE.read_text()).get("entries", [])
    except Exception:  # noqa: BLE001
        hist = []
    streak = 1
    for e in reversed(hist):
        if e.get("date") == session_date:
            continue                      # today's own entry, already counted
        if e.get("label") == label:
            streak += 1
        else:
            break
    return streak


def _journal_regime(entry: dict) -> None:
    try:
        hist = json.loads(_REGIME_FILE.read_text())
    except Exception:  # noqa: BLE001
        hist = {"entries": []}
    entries = hist.get("entries") or []
    if entries and entries[-1].get("date") == entry["date"]:
        entries[-1] = entry               # idempotent per session
    else:
        entries.append(entry)
    _REGIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REGIME_FILE.write_text(json.dumps({"entries": entries}, indent=1))


def regime_from_scan(report: dict, spy_stage: Optional[int] = None,
                     journal: bool = True) -> dict:
    """Derive the spoken regime from a scan artifact (ml.scan_report).

    `report` is the dict form of a ScanReport. `spy_stage` is SPY's own
    Weinstein stage, which can only DOWNGRADE the breadth read: a market
    where half the names advance while the index itself has broken its
    30-week average is not a bull market, whatever the count says.
    """
    counts = {"Basing": 0, "Advancing": 0, "Topping": 0, "Declining": 0}
    for r in report.get("rows", []):
        name = r.get("stage_name")
        if name in counts:
            counts[name] += 1
    total = sum(counts.values())
    if not total:
        return {"label": "UNKNOWN", "pct_advancing": 0.0, "total": 0,
                "counts": counts, "streak": 0,
                "headline": "Scan produced no graded names.",
                "spoken": "The scan produced no graded names, so the desk has "
                          "no read tonight."}

    pct = 100.0 * counts["Advancing"] / total
    label, implication = next((lab, imp) for floor, lab, imp in REGIME_LADDER
                              if pct >= floor)

    # SPY's own stage is a ceiling, never a promotion.
    if spy_stage == 4:
        label, implication = "DEFENSIVE", REGIME_LADDER[3][2]
    elif spy_stage == 3 and label in ("BROAD BULL", "NARROW BULL"):
        label, implication = "MIXED", REGIME_LADDER[2][2]

    session_date = report.get("session_date") or f"{datetime.now():%Y-%m-%d}"
    streak = _regime_streak(label, session_date)

    out = {
        "date": session_date,
        "label": label,
        "implication": implication,
        "pct_advancing": round(pct, 1),
        "advancing": counts["Advancing"],
        "declining": counts["Declining"],
        "basing": counts["Basing"],
        "topping": counts["Topping"],
        "total": total,
        "spy_stage": spy_stage,
        "streak": streak,
        "scan_hash": report.get("scan_hash", ""),
    }
    out["headline"] = (f"{label} · {pct:.0f}% advancing · "
                       f"{counts['Declining']} declining · {total} scanned")
    out["spoken"] = _regime_spoken(out)
    if journal:
        _journal_regime({k: out[k] for k in
                         ("date", "label", "pct_advancing", "advancing",
                          "declining", "total", "streak", "scan_hash")})
    return out


def _regime_spoken(o: dict) -> str:
    """The on-air sentence. Every number comes from the run, never from prose."""
    lead = {
        "BROAD BULL": "Broad bull.",
        "NARROW BULL": "Bull, but narrow.",
        "MIXED": "Mixed tape.",
        "DEFENSIVE": "Defensive.",
    }.get(o["label"], o["label"])
    s = (f"{lead} {o['advancing']} of {o['total']} names advancing, "
         f"{o['declining']} declining.")
    if o["streak"] > 1:
        s += f" That is {_ordinal(o['streak'])} straight session at this read."
    return s


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"the {n}{suffix}"
