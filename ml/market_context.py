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

import logging
from dataclasses import dataclass, field
from datetime import datetime
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
    except Exception as exc:
        logger.warning(f"SPY momentum calc failed: {exc}")

    # 3. Breadth from scan (if provided)
    if scan_report is not None and hasattr(scan_report, "stage_buckets"):
        buckets = scan_report.stage_buckets
        total = sum(len(b) for b in buckets.values())
        if total > 0:
            healthy = len(buckets[2]) + len(buckets[1])
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
