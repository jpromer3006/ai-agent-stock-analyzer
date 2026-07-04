"""
transition_watch.py — Stage 1 Transition Watch detector.

The strict Weinstein rules classify a stock as Stage 4 (Declining)
whenever price is below a falling 30 week MA. By design that
classification lags. A stock that has bottomed, completed a base, and
begun a fresh rally still reads Stage 4 for weeks before the 30 week
MA flattens and turns up — which is when the classifier promotes it
to Stage 1 or Stage 2.

This module overlays four leading signals on top of Stage 4
classification so the Trader tab can surface "Stage 4 stocks that look
like they are repairing" as their own watch list, distinct from
"Stage 4 stocks still bleeding."

The four signals (all must fire):

    1. Rally signal. Price has rallied at least 25 percent off the
       90 day low. The bottom is in.
    2. 50 day MA signal. Price is at least 5 percent above the 50 day
       MA. The faster trend has already turned up.
    3. Volume signal. Last 5 days average volume is at least 1.2 times
       the 50 day baseline. Real buying behind the move.
    4. Momentum signal. Price is at least 10 percent above where it
       was 20 trading days ago. This is raw price momentum, not an MA
       slope, because both the 50 day and 30 week MAs still roll out
       older pre-crash prices and will lag the actual recovery by
       weeks even when the stock has clearly bottomed and rallied.
       Momentum measured directly off price reads the move as it
       happens.

When all four pass, the ticker is a Stage 1 Transition Watch
candidate. The official stage label stays Stage 4 (Weinstein's strict
rule), but the UI surfaces the candidate in its own panel and the
action label changes from STRONG SELL to WATCH so it does not get
mistaken for a fresh short setup.

Public interface:
    analyze_transition_watch(ticker, df=None) -> TransitionWatch
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Signal thresholds
# ---------------------------------------------------------------------------

RALLY_MIN_PCT = 0.25            # 25% off 90 day low
ABOVE_50D_MA_MIN_PCT = 0.05     # 5% above 50d MA
RECENT_VOL_LOOKBACK = 5         # average last 5 days
VOL_BASELINE_LOOKBACK = 50      # vs 50 day baseline
VOL_MIN_RATIO = 1.2             # at least 1.2x
MOMENTUM_LOOKBACK_DAYS = 20     # 4 weeks for momentum measurement
MOMENTUM_MIN_PCT = 0.10         # price must be 10% above 4-weeks-ago close
LOW_WINDOW_DAYS = 90            # 90 day low for rally reference


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class TransitionWatch:
    ticker: str

    # Headline flag — true only if all four signals fire
    is_transition: bool = False

    # Per-signal booleans
    rally_signal: bool = False
    above_50d_signal: bool = False
    volume_signal: bool = False
    momentum_signal: bool = False        # price > 10% above 20-trading-day-ago close

    # Quantitative measures (raw values for display)
    rally_from_90d_low: float = 0.0      # signed pct
    pct_above_50d_ma: float = 0.0        # signed pct
    recent_vol_ratio: float = 1.0
    momentum_20d: float = 0.0            # signed pct, price now vs 20 days ago
    ma_slope_now: float = 0.0            # 30W 4w slope (informational only)
    ma_50d_slope: float = 0.0            # 50d 4w slope (informational only)

    # Reference values
    low_90d: float = 0.0
    ma_50d: float = 0.0
    last_close: float = 0.0

    # How many of the four signals fired (0-4)
    signals_passed: int = 0

    # Plain language narrative
    narrative: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def analyze_transition_watch(ticker: str,
                              df: Optional[pd.DataFrame] = None
                              ) -> TransitionWatch:
    """Compute the four transition watch signals for one ticker.

    Parameters
    ----------
    ticker : str
    df : optional pre-loaded daily OHLCV (with Close, Volume columns).
        If None, fetched via yfinance period 1y.

    Returns
    -------
    TransitionWatch with per signal flags and a narrative. is_transition
    is True only when all four signals pass.
    """
    ticker = ticker.upper()
    out = TransitionWatch(ticker=ticker)

    if df is None:
        try:
            import yfinance as yf
            df = yf.Ticker(ticker).history(period="1y", interval="1d",
                                            auto_adjust=True)
        except Exception as exc:
            out.error = f"yfinance fetch failed: {exc}"
            return out

    if df is None or df.empty or len(df) < 150:
        out.error = "insufficient daily history (need at least 150 bars)"
        return out

    closes = df["Close"]
    volumes = df["Volume"]
    last_close = float(closes.iloc[-1])
    out.last_close = round(last_close, 2)

    # ── Signal 1: Rally from 90 day low ──────────────────────────────
    last_90 = closes.tail(LOW_WINDOW_DAYS)
    low_90 = float(last_90.min())
    out.low_90d = round(low_90, 2)
    rally = (last_close - low_90) / low_90 if low_90 > 0 else 0.0
    out.rally_from_90d_low = round(rally, 4)
    out.rally_signal = rally >= RALLY_MIN_PCT

    # ── Signal 2: Price above 50 day MA ──────────────────────────────
    ma_50d = closes.rolling(50, min_periods=50).mean()
    last_ma50 = float(ma_50d.iloc[-1]) if not pd.isna(ma_50d.iloc[-1]) else 0.0
    out.ma_50d = round(last_ma50, 2)
    if last_ma50 > 0:
        pct_above_50 = (last_close - last_ma50) / last_ma50
        out.pct_above_50d_ma = round(pct_above_50, 4)
        out.above_50d_signal = pct_above_50 >= ABOVE_50D_MA_MIN_PCT

    # ── Signal 3: Recent volume expansion ────────────────────────────
    if len(volumes) >= VOL_BASELINE_LOOKBACK + RECENT_VOL_LOOKBACK:
        recent_vol = float(volumes.tail(RECENT_VOL_LOOKBACK).mean())
        baseline_vol = float(
            volumes.iloc[-(VOL_BASELINE_LOOKBACK + RECENT_VOL_LOOKBACK):
                          -RECENT_VOL_LOOKBACK].mean()
        )
        if baseline_vol > 0:
            ratio = recent_vol / baseline_vol
            out.recent_vol_ratio = round(ratio, 2)
            out.volume_signal = ratio >= VOL_MIN_RATIO

    # ── Signal 4: Raw 4 week price momentum ──────────────────────────
    # Direct price-on-price check. We want to know "did the stock
    # actually move up over the last 4 weeks?" Both the 50 day and
    # 30 week MAs lag because their windows still ingest pre-crash
    # prices for many weeks after the actual bottom. Raw 20-day
    # momentum reads the move as it happens. We also store the 30W
    # slope and 50d slope for informational display in the dossier.
    if len(closes) >= MOMENTUM_LOOKBACK_DAYS + 1:
        close_4w_ago = float(closes.iloc[-1 - MOMENTUM_LOOKBACK_DAYS])
        if close_4w_ago > 0:
            momentum = (last_close - close_4w_ago) / close_4w_ago
            out.momentum_20d = round(momentum, 4)
            out.momentum_signal = momentum >= MOMENTUM_MIN_PCT

    # Informational: 30W and 50d slope (NOT used for signal pass/fail,
    # but kept on the result for the dossier display).
    ma_30w = closes.rolling(150, min_periods=150).mean()
    if len(ma_30w.dropna()) >= MOMENTUM_LOOKBACK_DAYS + 1:
        ma_now_30w = float(ma_30w.iloc[-1])
        ma_4w_ago_30w = float(ma_30w.iloc[-1 - MOMENTUM_LOOKBACK_DAYS])
        if ma_4w_ago_30w > 0:
            out.ma_slope_now = round(
                (ma_now_30w - ma_4w_ago_30w) / ma_4w_ago_30w, 4
            )
    if len(ma_50d.dropna()) >= MOMENTUM_LOOKBACK_DAYS + 1:
        ma_now_50d = float(ma_50d.iloc[-1])
        ma_4w_ago_50d = float(ma_50d.iloc[-1 - MOMENTUM_LOOKBACK_DAYS])
        if ma_4w_ago_50d > 0:
            out.ma_50d_slope = round(
                (ma_now_50d - ma_4w_ago_50d) / ma_4w_ago_50d, 4
            )

    # ── Aggregate ─────────────────────────────────────────────────────
    out.signals_passed = sum([
        out.rally_signal,
        out.above_50d_signal,
        out.volume_signal,
        out.momentum_signal,
    ])
    out.is_transition = out.signals_passed == 4

    out.narrative = _build_narrative(out)
    return out


def _build_narrative(t: TransitionWatch) -> str:
    """Build a plain language description of the transition picture."""
    parts: list[str] = []
    if t.is_transition:
        parts.append(
            f"All four Stage 1 Transition signals are firing for "
            f"{t.ticker}. The stock is technically still Stage 4 by the "
            f"30 week MA rule, but a base has formed and early buying "
            f"is showing up."
        )
    elif t.signals_passed >= 2:
        parts.append(
            f"{t.ticker} has {t.signals_passed} of 4 Stage 1 Transition "
            f"signals firing. The pattern is forming but not yet "
            f"complete."
        )
    else:
        parts.append(
            f"{t.ticker} is in Stage 4 with {t.signals_passed} of 4 "
            f"Stage 1 Transition signals — still mostly bleeding, not yet "
            f"basing."
        )

    # Per signal lines
    signal_lines: list[str] = []
    icon = lambda passed: "✓" if passed else "✗"
    signal_lines.append(
        f"{icon(t.rally_signal)} Rally: {t.rally_from_90d_low:+.0%} "
        f"from 90 day low of ${t.low_90d:.2f} (threshold {RALLY_MIN_PCT:.0%})"
    )
    signal_lines.append(
        f"{icon(t.above_50d_signal)} 50d MA: price "
        f"{t.pct_above_50d_ma:+.1%} vs 50d MA of ${t.ma_50d:.2f} "
        f"(threshold +{ABOVE_50D_MA_MIN_PCT:.0%})"
    )
    signal_lines.append(
        f"{icon(t.volume_signal)} Volume: last 5d avg "
        f"{t.recent_vol_ratio:.2f}x vs 50d baseline "
        f"(threshold {VOL_MIN_RATIO:.1f}x)"
    )
    signal_lines.append(
        f"{icon(t.momentum_signal)} Momentum: {t.momentum_20d:+.1%} "
        f"over last 20 trading days (threshold {MOMENTUM_MIN_PCT:+.0%}). "
        f"For context: 50d MA slope {t.ma_50d_slope:+.2%}/4w, "
        f"30W MA slope {t.ma_slope_now:+.2%}/4w (both still lag the move)."
    )

    return parts[0] + " Signals: " + "; ".join(signal_lines) + "."


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["ZS", "PLTR", "META", "MSFT"]
    print(f"Stage 1 Transition Watch on {len(tickers)} tickers\n")
    for t in tickers:
        r = analyze_transition_watch(t)
        if r.error:
            print(f"{t}: ERROR — {r.error}")
            continue
        flag = "🚨 TRANSITION" if r.is_transition else f"{r.signals_passed}/4"
        print(f"{t:<6}  {flag:<14}  close ${r.last_close:>8.2f}  "
              f"rally {r.rally_from_90d_low:+.0%}  "
              f"vs50d {r.pct_above_50d_ma:+.1%}  "
              f"vol {r.recent_vol_ratio:.2f}x  "
              f"momentum {r.momentum_20d:+.1%}/4w")
        print(f"          {r.narrative[:160]}...")
        print()
