"""
stage_analyzer.py — Stan Weinstein's Stage Analysis.

Per "Secrets for Profiting in Bull and Bear Markets" (Weinstein, 1988).

The four stages of every stock's lifecycle:

    Stage 1 — Basing:       Price consolidates around a flat 30-week MA.
                            Low volume. Wait for breakout. (Neutral)

    Stage 2 — Advancing:    Price breaks above a rising 30-week MA on
                            heavy volume with positive relative strength.
                            This is Weinstein's BUY zone.

    Stage 3 — Topping:      Price rolls over, 30-week MA flattens.
                            Distribution volume. TRIM/SELL zone.

    Stage 4 — Declining:    Price below a falling 30-week MA. Negative RS.
                            SHORT or stay out.

Key indicators (all computable from daily OHLCV):
    - 30-week SMA (150 trading days)
    - 30-week SMA slope (direction and steepness)
    - Price vs 30-week MA (percentage distance)
    - Mansfield Relative Strength vs SPY (normalized)
    - Volume surge ratio (current volume / 50-day avg)
    - Distance from 52-week high
    - Weeks in current stage

Output:
    StageResult with stage (1-4), bull_probability (0-1),
    and all supporting metrics for transparent scoring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (faithful to Weinstein's book)
# ---------------------------------------------------------------------------
WEEKS_IN_MA = 30
TRADING_DAYS_PER_WEEK = 5
MA_WINDOW_DAYS = WEEKS_IN_MA * TRADING_DAYS_PER_WEEK  # 150
VOLUME_AVG_WINDOW = 50
SLOPE_LOOKBACK_WEEKS = 4
SLOPE_LOOKBACK_DAYS = SLOPE_LOOKBACK_WEEKS * TRADING_DAYS_PER_WEEK  # 20
BENCHMARK_TICKER = "SPY"

# Thresholds (tunable — Weinstein prescribes ballparks, not exact values)
STAGE1_MAX_PRICE_DEVIATION = 0.05   # price within ±5% of flat MA
STAGE1_MAX_SLOPE_PCT = 0.005         # MA slope < 0.5%/month → "flat"
STAGE2_MIN_PRICE_ABOVE_MA = 0.02     # price must be 2%+ above MA
STAGE2_MIN_SLOPE_PCT = 0.005          # MA sloping up
STAGE4_MAX_PRICE_BELOW_MA = -0.02
STAGE4_MAX_SLOPE_PCT = -0.005

# --- Research-grade data hygiene (scan-level filters) ---
MIN_SCAN_PRICE = 1.0                  # drop sub-$1 names
MIN_SCAN_DOLLAR_VOLUME = 3_000_000    # drop illiquid names (< $3M avg daily $ volume)
RETURN_SINCE_BREAKOUT_SANITY = 3.0    # +300% cap; above this the breakout anchor is
                                      # treated as stale/unadjusted and is not scored

# --- Stronger Mansfield RS gate + stage hysteresis ---
# Beyond RS being positive (WEINSTEIN_MIN_MANSFIELD_RS), require the RS line to
# be RISING over the last ~4 weeks (Weinstein wants RS making new highs, not
# merely above zero). Stage assignment also uses 2-bar hysteresis: a name must
# hold its side of the 30-week MA for two consecutive weekly closes before it is
# promoted to Stage 2 or demoted to Stage 4, so it does not flip on one bar.

# Weinstein BUY confirmation gates (Ch. 1 + Ch. 2)
# Stage 2 must clear BOTH gates to trigger a confirmed BUY action.
# If either fails, downgrade to "WATCH" — the breakout is unconfirmed.
WEINSTEIN_MIN_VOLUME_SURGE = 1.5     # Ch. 1: "buy on volume expansion at breakout"
                                      # Current daily volume vs 50-day avg
WEINSTEIN_MIN_MANSFIELD_RS = 0.0      # Ch. 1: "never buy a stock when its
                                      # relative-strength line is in negative territory"

# Stage 2 EXTENDED detection (Ch. 1 + Ch. 2 — late stage warning)
# A stock that has run far above its 30-week MA is at elevated risk of
# rolling into Stage 3 (Topping). Weinstein: "the further above the moving
# average a stock has run, the less reward potential remains" and "investors
# who chase stocks much above the 30-week MA should reconsider."
# Weinstein Ch. 1 also explicitly warns: "a price advance on declining volume
# is bearish." A stock that is materially extended without fresh volume
# confirmation is showing this divergence pattern.
#
# A stock is flagged "extended" if ANY of:
#   (a) >40% above 30-week MA regardless of volume (chasing stage / hard stop)
#   (b) ≥25% above 30-week MA AND volume is very weak (≤ 1.0× baseline)
#   (c) ≥20% above 30-week MA AND volume gate is failing (< 1.5× baseline) —
#       captures the Weinstein "extended price + unconfirmed volume" pattern
#       that (b) misses when volume is borderline (e.g., 1.0–1.5×).
EXTENDED_HARD_PCT_ABOVE_MA = 0.40     # rule (a) — extended regardless of vol
EXTENDED_SOFT_PCT_ABOVE_MA = 0.25     # rule (b) requires very weak volume
EXTENDED_SOFT_MAX_VOLUME_SURGE = 1.0  # very weak = ≤ 1.0× baseline
EXTENDED_VOLDIV_PCT_ABOVE_MA = 0.20   # rule (c) — extended + vol gate failing
EXTENDED_PURE_PRICE_PCT_ABOVE_MA = 0.30  # rule (d) — pure price extension.
                                      # Weinstein Ch. 2: "the further above the
                                      # 30-week MA a stock runs, the less reward
                                      # potential remains." A stock ≥30% above a
                                      # rising MA is past the optimal entry and
                                      # is a chase regardless of how strong volume
                                      # looks. Closes the gap where a high-volume
                                      # extended leader (e.g. +27% on 1.6× vol)
                                      # would otherwise print a "fresh" BUY.


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class TradeSetup:
    """
    Weinstein classic Stage 2 breakout trade setup.

    - Buy-Stop: place a buy order just above the recent pivot high
    - Stop-Loss: exit if price closes below 30-week MA (Weinstein's invalidator)
    - Target:   prior swing high or measured move from base
    - Risk/Reward: target distance / stop distance
    """
    applicable: bool = False                # True only when stage allows a clean setup
    direction: str = "NONE"                 # "LONG" for Stage 2, "SHORT" for Stage 4
    entry_type: str = "NONE"                # "Buy-Stop" or "Sell-Stop"
    entry_price: float = 0.0                # Buy-stop trigger (above pivot high)
    stop_loss: float = 0.0                  # Invalidation level (30-week MA)
    target_1: float = 0.0                   # First target (prior resistance)
    risk_per_share: float = 0.0             # |entry - stop|
    reward_per_share: float = 0.0           # |target - entry|
    risk_reward_ratio: float = 0.0          # reward / risk
    pivot_high_50d: float = 0.0             # 50-day high (reference)
    narrative: str = ""                     # human-readable setup explanation


@dataclass
class StageResult:
    ticker: str
    stage: int                              # 1, 2, 3, or 4
    stage_name: str                         # "Basing", "Advancing", "Topping", "Declining"
    bull_probability: float                 # 0.0 to 1.0
    action: str                             # "BUY", "STRONG BUY", "HOLD", "TRIM", "SELL"
    confidence: float                       # 0.0 to 1.0 — how clearly the stock is in this stage

    # Raw indicators
    last_close: float = 0.0
    ma_30w: float = 0.0
    pct_above_ma: float = 0.0               # signed
    ma_slope_pct: float = 0.0               # % change of MA over last 4 weeks
    mansfield_rs: float = 0.0               # >0 = outperforming SPY
    volume_surge: float = 1.0               # current vol / 50d avg
    pct_from_52w_high: float = 0.0          # <0 if below high
    pct_from_52w_low: float = 0.0           # >0 if above low
    # Today vs trend — last close vs the prior daily close. Surfaced in
    # the Trader tab so the user can see "the stock pulled back 1.6%
    # today" alongside the trend verdict, instead of having to read
    # an intraday red candle as evidence the trend is broken.
    today_change_pct: float = 0.0           # signed

    # Weinstein BUY confirmation gates (Ch. 1 + Ch. 2)
    volume_confirmed: bool = False          # vol_surge >= WEINSTEIN_MIN_VOLUME_SURGE
    rs_confirmed: bool = False              # mansfield_rs > WEINSTEIN_MIN_MANSFIELD_RS
    weinstein_buy_confirmed: bool = False   # all gates pass → BUY action allowed
    gate_notes: list[str] = field(default_factory=list)  # human-readable gate reasons

    # Stage 2 sub-classification — distinguishes fresh setups from extended runs
    # "fresh"     — Stage 2, both gates pass, near a clean breakout point
    # "watching"  — Stage 2, one or both gates failing, breakout still forming
    # "extended"  — Stage 2 but >25% above MA on weak volume — likely topping
    # "n/a"       — not in Stage 2
    setup_type: str = "n/a"

    # Invalidation state. Weinstein exits on a COMPLETED weekly close
    # beyond the 30-week MA, never an intraday tick. is_invalidated is
    # True only when that weekly close has breached (long: closed below
    # the MA; short: closed above). invalidation_note carries the
    # human-readable AT RISK / INVALIDATED text for the UI.
    is_invalidated: bool = False
    invalidation_note: str = ""

    # Since-breakout scorecard (Stage 2 only). Anchored to the most
    # recent 30-week MA reclaim (the breakout bar), so a Stage 2 call is
    # judged by its return over weeks, not by today's candle.
    stage2_entry_date: Optional[str] = None
    stage2_entry_price: float = 0.0
    return_since_breakout: float = 0.0      # signed: last_close / entry - 1
    weeks_held: int = 0

    # Reference levels (from daily OHLCV)
    high_52w: float = 0.0
    low_52w: float = 0.0
    high_50d: float = 0.0
    low_50d: float = 0.0

    # Weinstein trade setup (computed when stage allows)
    trade_setup: Optional[TradeSetup] = None

    # Chapter 4 — overhead resistance analysis (populated by batch_scanner
    # for Stage 2 and Stage 3 candidates; left at defaults for Stage 1/4
    # and any ticker where resistance computation failed).
    #
    # Grade vocabulary:
    #   "A+"  virgin territory, no overhead in the 10y window
    #   "A"   nearest resistance > 25% above current price
    #   "B"   nearest resistance 10–25% above current price
    #   "C"   nearest resistance < 10% above current price
    #   "n/a" not computed (Stage 1/4 or error)
    resistance_grade: str = "n/a"
    nearest_resistance: Optional[float] = None
    nearest_resistance_pct: Optional[float] = None    # signed, +0.10 = 10% above
    virgin_territory: bool = False
    ten_year_high: Optional[float] = None
    overhead_supply_score: float = 0.0
    resistance_zones_count: int = 0
    resistance_narrative: str = ""
    resistance_error: Optional[str] = None             # set if compute failed

    # Stage 1 Transition Watch (computed for Stage 4 only by batch_scanner)
    # When all four signals fire on a Stage 4 stock, it becomes a Stage 1
    # Transition Watch candidate — the strict 30W MA rule still says
    # Stage 4 but the stock has bottomed, formed a base, and started
    # to rally on expanding volume. Surfaces in its own Trader-tab
    # panel separate from Stage 4 Breakdowns.
    transition_watch_flag: bool = False
    transition_signals_passed: int = 0                 # 0-4
    transition_rally_from_low: float = 0.0             # rally % off 90d low
    transition_pct_above_50d: float = 0.0              # price vs 50d MA
    transition_vol_ratio: float = 1.0                  # 5d avg vs 50d baseline
    transition_momentum_20d: float = 0.0               # 20d price momentum
    transition_narrative: str = ""

    # Entry risk overlay — populated post-scan by ml/entry_gates.py.
    # entry_gate_status is the market-regime gate verdict (pass / caution
    # / blocked). entry_quality_score is a 0-100 transparent blend of
    # extension, volume, breakout freshness, RS, and sentiment, graded
    # A to D. Sentiment and earnings fields are populated only for
    # confirmed buys (Step 3b cost discipline).
    entry_gate_status: str = "pass"
    entry_gate_reasons: list[str] = field(default_factory=list)
    entry_quality_score: float = 0.0
    entry_quality_grade: str = ""

    # Earnings blackout (Step 3b). Populated for confirmed buys only.
    # next_earnings_date is the next scheduled print after the scan ts,
    # in YYYY-MM-DD. days_to_earnings is trading-day distance from the
    # scan date to that print. None when yfinance returns nothing.
    next_earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None

    # News sentiment (Step 3b). VADER compound on the latest yfinance
    # headlines, populated for confirmed buys only. compound is in
    # [-1, +1]; None when no headlines were available.
    sentiment_score: Optional[float] = None
    sentiment_headline_count: int = 0
    sentiment_as_of: Optional[str] = None

    # Meta
    as_of_date: Optional[str] = None
    error: Optional[str] = None
    # error_kind separates an expected data exclusion from a real code
    # fault. "" = ok, "data" = benign (no history / delisted / sparse),
    # "exception" = analysis crashed (a bug — must be surfaced, not
    # silently dropped as if the ticker were delisted).
    error_kind: str = ""
    explanation: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _fetch_history(ticker: str, period: str = "2y") -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV via yfinance."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if df is None or df.empty or len(df) < MA_WINDOW_DAYS:
            return None
        return df
    except Exception as exc:
        logger.warning(f"yfinance history fetch failed for {ticker}: {exc}")
        return None


def _data_quality_check(df: pd.DataFrame) -> Optional[str]:
    """Return an error string if `df` doesn't have enough reliable data
    to run a Weinstein analysis on, else None.

    Rejects tickers where:
      - Close prices have any zero / null in the recent 60 days
      - Volume series has > 30% zeros in the last 60 days (illiquid /
        delisted / data gaps — gates can't evaluate properly)
      - Volume baseline is 0 (no measurable trading activity)

    A "Watching" stock with high RS but failing volume gate is fine —
    that's a real signal. This filter only excludes stocks where the
    underlying data itself is too sparse to trust.
    """
    if df is None or df.empty or len(df) < MA_WINDOW_DAYS:
        return "insufficient price history"
    last_60 = df.tail(60)
    if last_60["Close"].isna().any() or (last_60["Close"] == 0).any():
        return "missing or zero closes in last 60 days"
    zero_vol_pct = float((last_60["Volume"] == 0).mean())
    if zero_vol_pct > 0.30:
        return f"volume series too sparse ({zero_vol_pct:.0%} zero days in last 60)"
    if float(last_60["Volume"].mean()) == 0:
        return "no measurable trading volume"
    # Research-grade hygiene: drop penny names and illiquid tickers so they
    # never reach the leaderboards (this is what excludes the AXTI-style
    # micro-caps with stale, unbelievable breakout anchors).
    last_close = float(last_60["Close"].iloc[-1])
    if last_close < MIN_SCAN_PRICE:
        return f"price ${last_close:.2f} below ${MIN_SCAN_PRICE:.0f} minimum"
    dollar_vol = float((last_60["Close"] * last_60["Volume"]).tail(20).mean())
    if dollar_vol < MIN_SCAN_DOLLAR_VOLUME:
        return (f"avg dollar volume ${dollar_vol/1e6:.1f}M below "
                f"${MIN_SCAN_DOLLAR_VOLUME/1e6:.0f}M minimum")
    return None


# ---------------------------------------------------------------------------
# Indicator computation
# ---------------------------------------------------------------------------

def _compute_indicators(df: pd.DataFrame, bench_df: Optional[pd.DataFrame] = None) -> dict:
    """Compute all Weinstein indicators from daily OHLCV."""
    close = df["Close"]
    volume = df["Volume"]

    # 30-week MA
    ma = close.rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
    last_ma = float(ma.iloc[-1])
    last_close = float(close.iloc[-1])

    # MA slope over last 4 weeks — % change
    if len(ma.dropna()) >= SLOPE_LOOKBACK_DAYS:
        ma_then = float(ma.dropna().iloc[-SLOPE_LOOKBACK_DAYS])
        ma_slope_pct = (last_ma - ma_then) / ma_then if ma_then != 0 else 0.0
    else:
        ma_slope_pct = 0.0

    pct_above_ma = (last_close - last_ma) / last_ma if last_ma != 0 else 0.0

    # Directional context for the flat-MA stages (JP's BND catch, 2026-07-28):
    # Stage 1 and Stage 3 are locally identical — a flat MA with price
    # oscillating around it. Weinstein's definition is directional: a flat MA
    # you ROSE into is Topping; one you FELL into is Basing. Measure how the
    # MA approached its current level over ~26 weeks (130 trading days).
    _APPROACH_DAYS = 130
    if len(ma.dropna()) >= _APPROACH_DAYS:
        ma_then26 = float(ma.dropna().iloc[-_APPROACH_DAYS])
        ma_approach_pct = (last_ma - ma_then26) / ma_then26 if ma_then26 else 0.0
    else:
        ma_approach_pct = 0.0

    # Prior weekly bar (5 trading days back) for stage hysteresis. If the
    # price only crossed the MA this week, the prior bar was on the other
    # side, and the stage promotion/demotion is deferred until it persists.
    if len(close) >= 6 and len(ma.dropna()) >= 6 and not pd.isna(ma.iloc[-6]):
        prev_close = float(close.iloc[-6])
        prev_ma = float(ma.iloc[-6])
        pct_above_ma_prev = (prev_close - prev_ma) / prev_ma if prev_ma != 0 else pct_above_ma
    else:
        pct_above_ma_prev = pct_above_ma

    # Volume surge — Weinstein uses WEEKLY volume on breakouts, not a single day.
    # We approximate "breakout volume" as the AVERAGE daily volume over the
    # recent 5-day window (one trading week) vs the 50-day baseline BEFORE that
    # week. Using the 5-day MEAN (not the MAX) keeps the ratio centred on ~1.0
    # in quiet tape, so the 1.5x gate and the EXTENDED vol cutoffs below retain
    # their intended calibration. (A prior MAX-based version inflated this by
    # ~1.2-1.8x, which silently loosened the volume gate and made the EXTENDED
    # safety net under-fire — e.g. a stock 27% above its MA printing a false
    # "fresh STRONG BUY". See _classify_setup_type.)
    if len(volume) >= 55:
        recent_avg_vol = float(volume.iloc[-5:].mean())
        baseline_avg = float(volume.iloc[-55:-5].mean())
    else:
        # Fallback for short histories
        recent_avg_vol = float(volume.iloc[-5:].mean()) if len(volume) >= 5 else float(volume.iloc[-1])
        baseline_avg = float(volume.rolling(VOLUME_AVG_WINDOW, min_periods=10).mean().iloc[-1])
    volume_surge = recent_avg_vol / baseline_avg if baseline_avg > 0 else 1.0

    # 52-week and 50-day stats
    last_252 = close.tail(252)
    last_50 = close.tail(50)
    high_52w = float(last_252.max())
    low_52w = float(last_252.min())
    high_50d = float(last_50.max())
    low_50d = float(last_50.min())
    pct_from_52w_high = (last_close - high_52w) / high_52w
    pct_from_52w_low = (last_close - low_52w) / low_52w

    # Mansfield Relative Strength vs SPY
    # RS = (stock/bench) / SMA52(stock/bench) - 1, scaled
    mansfield_rs = 0.0
    mansfield_rs_rising = True   # default True so short histories don't fail the gate
    if bench_df is not None and not bench_df.empty:
        # Align by date
        merged = pd.merge(
            df[["Close"]].rename(columns={"Close": "stock"}),
            bench_df[["Close"]].rename(columns={"Close": "bench"}),
            left_index=True, right_index=True, how="inner",
        )
        if len(merged) >= MA_WINDOW_DAYS:
            ratio = merged["stock"] / merged["bench"]
            ratio_ma = ratio.rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
            last_ratio = float(ratio.iloc[-1])
            last_ratio_ma = float(ratio_ma.iloc[-1])
            if last_ratio_ma > 0:
                mansfield_rs = ((last_ratio / last_ratio_ma) - 1.0) * 100  # percent
            # RS "rising" over the last ~4 weeks (20 trading days): the RS line
            # today vs 20 sessions ago. Weinstein wants RS making new highs.
            rs_series = ((ratio / ratio_ma) - 1.0) * 100
            rs_clean = rs_series.dropna()
            if len(rs_clean) >= 21:
                mansfield_rs_rising = bool(rs_clean.iloc[-1] >= rs_clean.iloc[-21])

    return {
        "last_close": last_close,
        "ma_30w": last_ma,
        "ma_slope_pct": ma_slope_pct,
        "pct_above_ma": pct_above_ma,
        "pct_above_ma_prev": pct_above_ma_prev,
        "ma_approach_pct": ma_approach_pct,
        "volume_surge": volume_surge,
        "pct_from_52w_high": pct_from_52w_high,
        "pct_from_52w_low": pct_from_52w_low,
        "mansfield_rs": mansfield_rs,
        "mansfield_rs_rising": mansfield_rs_rising,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "high_50d": high_50d,
        "low_50d": low_50d,
    }


# ---------------------------------------------------------------------------
# Weinstein trade setup computation
# ---------------------------------------------------------------------------

def _compute_trade_setup(stage: int, ind: dict) -> TradeSetup:
    """
    Compute the Weinstein classic Stage 2 breakout trade setup.

    Rules from "Secrets for Profiting in Bull and Bear Markets":
      - Buy-Stop: place a buy order at (pivot high + 0.5% buffer) to avoid
        chasing but catch a confirmed breakout
      - Stop-Loss: 30-week MA is the invalidator. Close below it → exit.
      - Target 1: prior 52-week high (first resistance) or measured move
        from base (pivot high - 52-week low, projected up from entry)
    Stage 4 mirror applies for short setups.
    """
    setup = TradeSetup()
    last_close = ind["last_close"]
    ma_30w = ind["ma_30w"]
    high_50d = ind["high_50d"]
    low_50d = ind["low_50d"]
    high_52w = ind["high_52w"]
    low_52w = ind["low_52w"]

    if stage == 2:
        setup.applicable = True
        setup.direction = "LONG"
        setup.entry_type = "Buy-Stop"
        # Weinstein: enter just above the pivot high (0.5% buffer)
        setup.entry_price = round(high_50d * 1.005, 2)
        # If price has already broken out above pivot, use current close as entry
        if last_close > high_50d:
            setup.entry_price = round(last_close * 1.005, 2)
        # Stop-loss at 30-week MA (Weinstein's invalidator)
        setup.stop_loss = round(ma_30w, 2)
        # Target: measured move from 52-week base, projected from entry
        base_depth = high_52w - low_52w
        setup.target_1 = round(setup.entry_price + base_depth * 0.5, 2)
        setup.pivot_high_50d = round(high_50d, 2)

    elif stage == 4:
        setup.applicable = True
        setup.direction = "SHORT"
        setup.entry_type = "Sell-Stop"
        # Mirror: enter just below the pivot low (-0.5% buffer)
        setup.entry_price = round(low_50d * 0.995, 2)
        if last_close < low_50d:
            setup.entry_price = round(last_close * 0.995, 2)
        # Stop-loss at 30-week MA (above current price in Stage 4)
        setup.stop_loss = round(ma_30w, 2)
        # Target: measured move down
        base_depth = high_52w - low_52w
        setup.target_1 = round(setup.entry_price - base_depth * 0.5, 2)
        setup.pivot_high_50d = round(low_50d, 2)

    else:
        setup.applicable = False
        setup.narrative = (
            f"Stock is in Stage {stage} — no clean Weinstein setup right now. "
            f"Wait for a Stage 2 breakout (BUY) or Stage 4 breakdown (SHORT) "
            f"with a confirmed 30-week MA direction."
        )
        return setup

    # Compute risk/reward
    setup.risk_per_share = abs(setup.entry_price - setup.stop_loss)
    setup.reward_per_share = abs(setup.target_1 - setup.entry_price)
    if setup.risk_per_share > 0:
        setup.risk_reward_ratio = round(setup.reward_per_share / setup.risk_per_share, 2)

    # Narrative
    if stage == 2:
        setup.narrative = (
            f"**Classic Weinstein Stage 2 Setup:** Place a Buy-Stop order at "
            f"${setup.entry_price} (just above the 50-day pivot high of "
            f"${setup.pivot_high_50d}). Set a protective Stop-Loss at "
            f"${setup.stop_loss} (the 30-week moving average — Weinstein's "
            f"invalidation line). First target is ${setup.target_1}, a "
            f"measured move from the prior base. Risk/Reward: "
            f"{setup.risk_reward_ratio:.2f}:1."
        )
    else:
        setup.narrative = (
            f"**Classic Weinstein Stage 4 Setup (Short):** Place a Sell-Stop "
            f"order at ${setup.entry_price} (below recent pivot low). Cover "
            f"if price reclaims ${setup.stop_loss} (the 30-week MA — breakdown "
            f"invalidated). First target ${setup.target_1}. Risk/Reward: "
            f"{setup.risk_reward_ratio:.2f}:1."
        )
    return setup


# ---------------------------------------------------------------------------
# Stage classification
# ---------------------------------------------------------------------------

def _classify_stage(ind: dict) -> tuple[int, str, float, list[str]]:
    """
    Classify into one of Weinstein's 4 stages and compute confidence.
    Returns (stage_num, stage_name, confidence, explanation_lines).
    """
    pct_above = ind["pct_above_ma"]
    pct_above_prev = ind.get("pct_above_ma_prev", pct_above)
    slope = ind["ma_slope_pct"]
    rs = ind["mansfield_rs"]

    exp: list[str] = []
    exp.append(f"Price vs 30W MA: {pct_above:+.2%}")
    exp.append(f"30W MA slope (4wk): {slope:+.2%}")
    exp.append(f"Mansfield RS vs SPY: {rs:+.1f}")

    # Stage 4: Declining. Hysteresis: require the prior weekly close to also be
    # below the MA, so a one-week breakdown does not immediately flip to Stage 4.
    if pct_above < STAGE4_MAX_PRICE_BELOW_MA and slope < STAGE4_MAX_SLOPE_PCT:
        if pct_above_prev >= 0:
            exp.append("◑ Fresh break below MA (1 bar) → deferring Stage 4 "
                       "until it confirms (hysteresis)")
        else:
            confidence = min(1.0, (abs(pct_above) / 0.15) * 0.5 + (abs(slope) / 0.05) * 0.5)
            exp.append("✗ Price below declining 30W MA → Stage 4 Declining")
            return 4, "Declining", confidence, exp

    # Stage 2: Advancing. Hysteresis: require the prior weekly close to also be
    # above the MA, so a one-week reclaim does not immediately promote to Stage 2.
    if pct_above > STAGE2_MIN_PRICE_ABOVE_MA and slope > STAGE2_MIN_SLOPE_PCT:
        if pct_above_prev <= 0:
            exp.append("◑ Fresh reclaim of MA (1 bar) → deferring Stage 2 "
                       "promotion until it confirms (hysteresis)")
        else:
            confidence = min(1.0, (pct_above / 0.15) * 0.5 + (slope / 0.05) * 0.5)
            exp.append("✓ Price above rising 30W MA → Stage 2 Advancing")
            return 2, "Advancing", confidence, exp

    # Directional context (JP's BND catch, 2026-07-28): a flat MA is Stage 3
    # if the MA ROSE into the flat (top of an advance) and Stage 1 if it FELL
    # into the flat (floor of a decline). +1% over ~26 weeks is the tiebreak
    # (low-volatility instruments like bond ETFs move their MA in small steps).
    approach = ind.get("ma_approach_pct", 0.0)

    # Stage 3: Topping — a flat MA the price ROSE into (top of an advance).
    # BOTH sides of the MA are checked against the approach direction now:
    # JP's HOOD catch (2026-07-28) showed price ABOVE a flat MA that had
    # FALLEN -23% into place being called Topping — that shape is a repair
    # base after a decline, not a top. Stage 3 requires the MA to have
    # risen into the flat; price may sit slightly above OR below it.
    if (slope < STAGE2_MIN_SLOPE_PCT and approach > 0.01
            and pct_above > -STAGE1_MAX_PRICE_DEVIATION):
        confidence = 0.4 + min(0.4, abs(slope) * 20)
        exp.append(f"⚠ Flattening MA after an advance (MA {approach:+.1%} "
                   f"over 26w) → Stage 3 Topping")
        return 3, "Topping", confidence, exp
    if slope < STAGE2_MIN_SLOPE_PCT and approach < -0.01 and pct_above > 0:
        # Price has reclaimed a flat/falling MA that DECLINED into place —
        # Weinstein's post-markdown repair base, not a top.
        exp.append(f"◯ Price above a flat MA that FELL into place "
                   f"(MA {approach:+.1%} over 26w) → Stage 1 repair base")
        return 1, "Basing", 0.6, exp

    # Stage 1: Basing — price near flat MA that FELL into the flat (default)
    confidence = 0.5
    if abs(pct_above) < STAGE1_MAX_PRICE_DEVIATION and abs(slope) < STAGE1_MAX_SLOPE_PCT:
        confidence = 0.7
        exp.append(f"◯ Price near flat MA (MA {approach:+.1%} over 26w) "
                   f"→ Stage 1 Basing (wait for breakout)")
    else:
        exp.append("◯ Ambiguous setup → treating as Stage 1 Basing")
    return 1, "Basing", confidence, exp


# ---------------------------------------------------------------------------
# Bull probability
# ---------------------------------------------------------------------------

def _compute_bull_probability(stage: int, ind: dict, confidence: float) -> float:
    """
    Composite bull probability from stage + adjustments.

    Base by stage:
        Stage 1: 0.45 (neutral, awaiting direction)
        Stage 2: 0.75 (bullish)
        Stage 3: 0.40 (weakening)
        Stage 4: 0.15 (bearish)

    Adjustments:
        +RS and +volume_surge add up to +15pp
        -RS subtracts up to -10pp
        Slope steepness scales toward stage bias
    """
    base = {1: 0.45, 2: 0.75, 3: 0.40, 4: 0.15}[stage]

    # RS adjustment — scale where RS=+15 → +0.10, RS=-15 → -0.10
    rs = ind["mansfield_rs"]
    rs_adj = max(-0.10, min(0.15, rs / 150.0))

    # Volume surge bonus for Stage 2 breakouts
    vol_adj = 0.0
    if stage == 2 and ind["volume_surge"] >= 1.5:
        vol_adj = min(0.05, (ind["volume_surge"] - 1.0) * 0.02)

    # Slope magnitude — reinforces the stage direction
    slope = ind["ma_slope_pct"]
    if stage == 2:
        slope_adj = min(0.05, max(0.0, slope * 2))
    elif stage == 4:
        slope_adj = max(-0.05, min(0.0, slope * 2))
    else:
        slope_adj = 0.0

    prob = base + rs_adj + vol_adj + slope_adj
    return max(0.02, min(0.98, prob))


# ---------------------------------------------------------------------------
# Weinstein BUY confirmation gates (Ch. 1 + Ch. 2)
# ---------------------------------------------------------------------------

def _evaluate_buy_gates(stage: int, ind: dict) -> tuple[bool, bool, bool, list[str]]:
    """
    Evaluate Weinstein's BUY confirmation gates.

    Stan Weinstein, Chapter 1 ("Reading the Mansfield Chart"):
        "Never buy a stock when its relative-strength line is in negative
        territory, no matter how cheap it appears... it's very important
        that volume is large and expanding on a breakout."

    Stan Weinstein, Chapter 2 (Stage 2 entry):
        "Such a breakout above the 30-week MA must be on impressive volume."

    Returns
    -------
    (volume_confirmed, rs_confirmed, weinstein_buy_confirmed, notes)
        - volume_confirmed: vol_surge >= 1.5x → breakout backed by volume
        - rs_confirmed:     mansfield_rs > 0  → stock outperforming SPY
        - weinstein_buy_confirmed: ALL gates pass — only then is a Stage 2
          BUY a real Weinstein BUY (otherwise it's WATCH pending confirmation)
        - notes: human-readable explanations
    """
    vol_surge = ind["volume_surge"]
    rs = ind["mansfield_rs"]
    rs_rising = ind.get("mansfield_rs_rising", True)

    volume_confirmed = vol_surge >= WEINSTEIN_MIN_VOLUME_SURGE
    # Tighter RS gate: positive AND rising over the last ~4 weeks. A positive
    # but falling RS line is a deteriorating leader, not a fresh buy.
    rs_confirmed = (rs > WEINSTEIN_MIN_MANSFIELD_RS) and rs_rising

    notes: list[str] = []
    if volume_confirmed:
        notes.append(f"✓ Volume gate: {vol_surge:.2f}x ≥ 1.5x (breakout confirmed by volume)")
    else:
        notes.append(f"✗ Volume gate: {vol_surge:.2f}x < 1.5x (no volume expansion — breakout unconfirmed)")
    if rs_confirmed:
        notes.append(f"✓ Mansfield RS gate: {rs:+.1f} > 0 and rising (leading SPY)")
    elif rs > WEINSTEIN_MIN_MANSFIELD_RS and not rs_rising:
        notes.append(f"✗ Mansfield RS gate: {rs:+.1f} positive but FALLING "
                     "(deteriorating leadership — wait for RS to turn back up)")
    else:
        notes.append(f"✗ Mansfield RS gate: {rs:+.1f} ≤ 0 (stock is a market laggard — Weinstein bans buying)")

    # Buy is confirmed ONLY if Stage 2 AND both gates pass
    weinstein_buy_confirmed = (stage == 2) and volume_confirmed and rs_confirmed
    return volume_confirmed, rs_confirmed, weinstein_buy_confirmed, notes


# ---------------------------------------------------------------------------
# Stage 2 sub-classification (fresh / watching / extended)
# ---------------------------------------------------------------------------

def _classify_setup_type(stage: int, ind: dict, buy_confirmed: bool) -> str:
    """
    Sub-classify a Stage 2 stock for the leaderboard.

    Stan Weinstein, Chapter 2: "the further above the moving average a stock
    has run, the less reward potential remains." A stock at >25% above its
    30-week MA without fresh volume is past the optimal entry — it's running
    out of fuel and may roll to Stage 3 (Topping).

    Returns
    -------
    "fresh"     — Stage 2 with both gates passing → buy candidate now
    "watching"  — Stage 2 with one+ gate failing AND not extended → forming
    "extended"  — Stage 2 but stock has run too far on weak volume — caution
    "n/a"       — not in Stage 2
    """
    if stage != 2:
        return "n/a"
    pct_above = ind["pct_above_ma"]
    vol_surge = ind["volume_surge"]

    # Rule (d) PURE PRICE: ≥30% above MA is a chase regardless of volume.
    # Strong volume does NOT redeem an over-extended price — Weinstein wants
    # entries near the breakout, not 30%+ into the advance. This closes the
    # gap where a high-volume leader well above its MA printed a "fresh" BUY.
    if pct_above >= EXTENDED_PURE_PRICE_PCT_ABOVE_MA:
        return "extended"
    # Rule (a) HARD: anything >40% above MA is extended regardless of vol.
    # The optimal entry has passed; new buyers are chasing.
    if pct_above >= EXTENDED_HARD_PCT_ABOVE_MA:
        return "extended"
    # Rule (b) SOFT: >25% above MA AND volume very weak (≤1.0×).
    if (pct_above >= EXTENDED_SOFT_PCT_ABOVE_MA
            and vol_surge <= EXTENDED_SOFT_MAX_VOLUME_SURGE):
        return "extended"
    # Rule (c) NEW — Volume Divergence: ≥20% above MA AND volume gate failing
    # (vol < 1.5× baseline). Captures the Weinstein "advance on weak volume"
    # pattern where price has run far enough to require fresh volume
    # confirmation, but the gate hasn't fired. Catches mid-tier extensions
    # like EQIX (+25% / vol 1.25×) that fall through the SOFT rule.
    if (pct_above >= EXTENDED_VOLDIV_PCT_ABOVE_MA
            and vol_surge < WEINSTEIN_MIN_VOLUME_SURGE):
        return "extended"
    if buy_confirmed:
        return "fresh"
    return "watching"


# ---------------------------------------------------------------------------
# Action label
# ---------------------------------------------------------------------------

def _action_label(stage: int, bull_prob: float,
                  weinstein_buy_confirmed: bool = True,
                  setup_type: str = "n/a") -> str:
    """Map (stage, bull_prob, setup_type) → action verb.

    Stage 2 actions:
      - "fresh"     + confirmed → BUY / STRONG BUY
      - "watching"             → WATCH (waiting for confirmation)
      - "extended"             → AVOID (run is mature; not a buy)
    """
    if stage == 2:
        if setup_type == "extended":
            return "AVOID"  # late Stage 2, no fresh fuel — Weinstein says skip
        if not weinstein_buy_confirmed:
            return "WATCH"  # breakout unconfirmed — waiting for vol + RS
        if bull_prob >= 0.85:
            return "STRONG BUY"
        return "BUY"
    if stage == 1:
        return "WATCH"
    if stage == 3:
        return "TRIM"
    if stage == 4 and bull_prob <= 0.15:
        return "STRONG SELL"
    if stage == 4:
        return "SELL"
    return "HOLD"


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

# Cache for the benchmark (fetched once per session)
_benchmark_cache: Optional[pd.DataFrame] = None

# In-memory TTL cache for analyze_stage results
_stage_cache: dict[str, tuple[float, "StageResult"]] = {}
_STAGE_CACHE_TTL = 15 * 60  # 15 minutes


def _get_benchmark() -> Optional[pd.DataFrame]:
    global _benchmark_cache
    if _benchmark_cache is None:
        _benchmark_cache = _fetch_history(BENCHMARK_TICKER, period="2y")
    return _benchmark_cache


def _compute_invalidation_and_breakout(df: pd.DataFrame, ind: dict,
                                       stage: int) -> dict:
    """
    Two risk reads, both anchored to the 30-week MA already in `ind`.

    1. Invalidation state. Weinstein exits a position on a *weekly close*
       beyond the 30-week MA, never an intraday tick. We compare the last
       COMPLETED weekly close to the MA line:
         long  (Stage 2): invalidated when that close sits below the MA
         short (Stage 4): invalidated when that close sits above the MA
       If only the live intraday price has crossed (no confirmed weekly
       close yet) the state is "AT RISK", not "INVALIDATED". This keeps
       the word "invalidated" meaning a real, earned exit trigger.

    2. Since-breakout return (Stage 2 only). Anchored to the most recent
       30-week MA reclaim, which is the breakout bar. This judges a
       Stage 2 call over weeks instead of by today's candle.

    Returns a plain dict so analyze_stage can copy the keys onto the
    StageResult without this helper needing to import the dataclass.
    """
    out = {
        "is_invalidated": False,
        "invalidation_note": "",
        "stage2_entry_date": None,
        "stage2_entry_price": 0.0,
        "return_since_breakout": 0.0,
        "weeks_held": 0,
    }
    ma_line = ind.get("ma_30w", 0.0)
    last_close = ind.get("last_close", 0.0)
    if not ma_line or ma_line <= 0:
        return out

    close = df["Close"]

    # --- 1. Invalidation: last completed weekly close vs the MA line ----
    try:
        weekly = close.resample("W-FRI").last().dropna()
    except Exception:
        weekly = pd.Series(dtype=float)
    # weekly.iloc[-1] is the current, possibly unfinished week.
    # weekly.iloc[-2] is the last week that has actually closed.
    weekly_close = float(weekly.iloc[-2]) if len(weekly) >= 2 else last_close

    if stage == 2:                                   # long position
        breached = weekly_close < ma_line
        at_risk = (last_close < ma_line) and not breached
        if breached:
            out["is_invalidated"] = True
            out["invalidation_note"] = (
                f"INVALIDATED · weekly close ${weekly_close:,.2f} below "
                f"30-week MA ${ma_line:,.2f}"
            )
        elif at_risk:
            out["invalidation_note"] = (
                f"AT RISK · price ${last_close:,.2f} below the 30-week MA "
                f"${ma_line:,.2f} intraweek, no weekly close yet"
            )
    elif stage == 4:                                 # short position
        breached = weekly_close > ma_line
        at_risk = (last_close > ma_line) and not breached
        if breached:
            out["is_invalidated"] = True
            out["invalidation_note"] = (
                f"INVALIDATED · weekly close ${weekly_close:,.2f} above "
                f"30-week MA ${ma_line:,.2f}"
            )
        elif at_risk:
            out["invalidation_note"] = (
                f"AT RISK · price ${last_close:,.2f} above the 30-week MA "
                f"${ma_line:,.2f} intraweek, no weekly close yet"
            )

    # --- 2. Since-breakout return (Stage 2 only) ------------------------
    if stage == 2:
        ma = close.rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
        # NaN MA bars compare False, so the run never reaches into the
        # warm-up window. above[-1] True means price is above the MA now.
        above = (close > ma).to_numpy()
        if len(above) and bool(above[-1]):
            # Walk back through the contiguous run of closes-above-MA.
            # The first bar of that run is the most recent MA reclaim.
            i = len(above) - 1
            while i > 0 and bool(above[i - 1]):
                i -= 1
            entry_price = float(close.iloc[i])
            if entry_price > 0:
                ret = last_close / entry_price - 1.0
                if ret > RETURN_SINCE_BREAKOUT_SANITY:
                    # Implausible since-breakout return (e.g. +4000%) means a
                    # stale or unadjusted anchor. Leave the fields at their
                    # defaults so the name is excluded from the Stage 2
                    # scorecard instead of skewing the average.
                    pass
                else:
                    entry_date = close.index[i]
                    out["stage2_entry_date"] = entry_date.strftime("%Y-%m-%d")
                    out["stage2_entry_price"] = entry_price
                    out["return_since_breakout"] = ret
                    held_days = (close.index[-1] - entry_date).days
                    out["weeks_held"] = max(0, held_days // 7)

    return out


def analyze_stage(ticker: str, df: Optional[pd.DataFrame] = None,
                   use_cache: bool = True) -> StageResult:
    """
    Run full Weinstein stage analysis on a ticker.

    Parameters
    ----------
    ticker : str
    df : optional pre-loaded OHLCV dataframe (otherwise fetched from yfinance)
    use_cache : if True, return cached result within TTL (~15 min)

    Returns
    -------
    StageResult
    """
    import time
    ticker = ticker.upper()

    # In-memory cache
    if use_cache and df is None:
        cached = _stage_cache.get(ticker)
        if cached:
            ts_cached, r_cached = cached
            if time.time() - ts_cached < _STAGE_CACHE_TTL:
                return r_cached

    result = StageResult(
        ticker=ticker,
        stage=0,
        stage_name="Unknown",
        bull_probability=0.5,
        action="UNKNOWN",
        confidence=0.0,
    )

    if df is None:
        df = _fetch_history(ticker, period="2y")
    if df is None or df.empty:
        result.error = f"No price data available for {ticker}"
        result.error_kind = "data"
        return result

    # Quality gate: exclude tickers with sparse/unreliable data so they
    # don't pollute the Watching list. A clean Watch list = stocks where
    # gates are forming, not stocks where the data itself is broken.
    quality_issue = _data_quality_check(df)
    if quality_issue:
        result.error = f"{ticker}: data quality issue — {quality_issue}"
        result.error_kind = "data"
        return result

    bench = _get_benchmark()

    try:
        ind = _compute_indicators(df, bench_df=bench)
        stage, stage_name, confidence, explanation = _classify_stage(ind)
        bull_prob = _compute_bull_probability(stage, ind, confidence)

        # Evaluate Weinstein BUY confirmation gates (volume + Mansfield RS)
        vol_ok, rs_ok, buy_confirmed, gate_notes = _evaluate_buy_gates(stage, ind)
        # Sub-classify Stage 2 setups (fresh / watching / extended)
        setup_type = _classify_setup_type(stage, ind, buy_confirmed)
        action = _action_label(
            stage, bull_prob,
            weinstein_buy_confirmed=buy_confirmed,
            setup_type=setup_type,
        )

        result.stage = stage
        result.stage_name = stage_name
        result.bull_probability = bull_prob
        result.confidence = confidence
        result.action = action
        result.last_close = ind["last_close"]
        result.ma_30w = ind["ma_30w"]
        # Today vs prior daily close — small intraday read so the UI
        # can show "the stock is down today but the trend is intact"
        # right next to the bull-probability badge.
        if len(df) >= 2:
            prior_close = float(df["Close"].iloc[-2])
            if prior_close > 0:
                result.today_change_pct = (
                    (result.last_close - prior_close) / prior_close
                )
        result.pct_above_ma = ind["pct_above_ma"]
        result.ma_slope_pct = ind["ma_slope_pct"]
        result.mansfield_rs = ind["mansfield_rs"]
        result.volume_surge = ind["volume_surge"]
        result.pct_from_52w_high = ind["pct_from_52w_high"]
        result.pct_from_52w_low = ind["pct_from_52w_low"]
        result.high_52w = ind["high_52w"]
        result.low_52w = ind["low_52w"]
        result.high_50d = ind["high_50d"]
        result.low_50d = ind["low_50d"]
        result.trade_setup = _compute_trade_setup(stage, ind)
        result.as_of_date = df.index[-1].strftime("%Y-%m-%d")
        result.volume_confirmed = vol_ok
        result.rs_confirmed = rs_ok
        result.weinstein_buy_confirmed = buy_confirmed
        result.gate_notes = gate_notes
        result.setup_type = setup_type
        # Append a setup-type explanation line for transparency, identifying
        # WHICH of the three Late Stage 2 rules fired
        if setup_type == "extended":
            pct = ind["pct_above_ma"]
            vol = ind["volume_surge"]
            if pct >= EXTENDED_HARD_PCT_ABOVE_MA:
                rule_label = (
                    f"⚠ Late Stage 2 [hard rule]: +{pct:.0%} above 30W MA "
                    f"(>40% threshold) — chasing zone regardless of volume"
                )
            elif pct >= EXTENDED_PURE_PRICE_PCT_ABOVE_MA:
                rule_label = (
                    f"⚠ Late Stage 2 [price-extension rule]: +{pct:.0%} above "
                    f"30W MA (≥30%) — past the optimal entry; strong volume "
                    f"does not redeem an over-extended price (Weinstein Ch. 2)"
                )
            elif pct >= EXTENDED_SOFT_PCT_ABOVE_MA and vol <= EXTENDED_SOFT_MAX_VOLUME_SURGE:
                rule_label = (
                    f"⚠ Late Stage 2 [soft rule]: +{pct:.0%} above 30W MA "
                    f"on very weak volume ({vol:.2f}x ≤ 1.0×)"
                )
            else:
                rule_label = (
                    f"⚠ Late Stage 2 [volume divergence rule]: +{pct:.0%} above "
                    f"30W MA AND volume gate failing ({vol:.2f}x < 1.5×) — "
                    f"Weinstein Ch. 1: price advance on unconfirmed volume"
                )
            gate_notes = list(gate_notes) + [rule_label]
        result.explanation = explanation + gate_notes

        # Invalidation state + since-breakout scorecard. Both read off the
        # 30-week MA. See _compute_invalidation_and_breakout for the why.
        risk = _compute_invalidation_and_breakout(df, ind, stage)
        result.is_invalidated = risk["is_invalidated"]
        result.invalidation_note = risk["invalidation_note"]
        result.stage2_entry_date = risk["stage2_entry_date"]
        result.stage2_entry_price = risk["stage2_entry_price"]
        result.return_since_breakout = risk["return_since_breakout"]
        result.weeks_held = risk["weeks_held"]

        # Cache successful results
        if use_cache:
            import time
            _stage_cache[ticker] = (time.time(), result)
    except Exception as exc:
        # A real code fault, not a data problem. Log the full traceback
        # and tag it "exception" so the scan surfaces it as a bug rather
        # than silently dropping the ticker as if it were delisted.
        logger.exception(f"analyze_stage crashed for {ticker}")
        result.error = str(exc)
        result.error_kind = "exception"

    return result


def clear_stage_cache() -> int:
    """Manually clear the in-memory stage cache. Returns count cleared."""
    count = len(_stage_cache)
    _stage_cache.clear()
    return count


def build_buy_justification(ticker: str, r: "StageResult") -> dict:
    """
    Build a plain-English "why this is on the list" summary for a Confirmed
    BUY (Stage 2 with both gates passing).

    Returns a dict with:
        - stage_2_since: ISO date the stock entered Stage 2 (most recent
          Stage 1→2 cross), or None if no clean transition in last 12 months
        - weeks_in_stage_2: int (None if unknown)
        - rs_persistence_pct: % of last 12 weeks the Mansfield RS line was
          positive (>0)
        - volume_breakout_date: date the 5-day max volume first exceeded
          1.5× the 50-day baseline in the last 90 days, or None
        - narrative: human-readable summary string

    Used by Trader Mode to justify each Confirmed BUY:
        "DLR has been in Stage 2 since 2025-08-14 (12 weeks).
         Mansfield RS positive for 11 of the last 12 weeks (consistent
         outperformer). Volume breakout confirmed on 2026-04-25."
    """
    out = {
        "stage_2_since": None,
        "weeks_in_stage_2": None,
        "rs_persistence_pct": None,
        "volume_breakout_date": None,
        "narrative": "",
    }
    df = _fetch_history(ticker, period="2y")
    if df is None or df.empty:
        out["narrative"] = "Insufficient price history to justify."
        return out

    bench = _get_benchmark()

    # Stage 2 entry: find most recent up-cross of 30-week MA
    df = df.copy()
    df["MA30W"] = df["Close"].rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
    above = (df["Close"] > df["MA30W"]).astype(int)
    cross = above.diff().fillna(0)
    up_crosses = df.index[cross == 1]
    if len(up_crosses) > 0:
        last_cross = up_crosses[-1]
        out["stage_2_since"] = last_cross.strftime("%Y-%m-%d")
        out["weeks_in_stage_2"] = max(1, (df.index[-1] - last_cross).days // 7)

    # RS persistence: % of last 12 weekly observations where stock outperformed SPY
    if bench is not None and not bench.empty:
        merged = pd.merge(
            df[["Close"]].rename(columns={"Close": "stock"}),
            bench[["Close"]].rename(columns={"Close": "bench"}),
            left_index=True, right_index=True, how="inner",
        )
        if len(merged) >= MA_WINDOW_DAYS + 60:
            ratio = merged["stock"] / merged["bench"]
            ratio_ma = ratio.rolling(MA_WINDOW_DAYS, min_periods=MA_WINDOW_DAYS).mean()
            rs_line = ((ratio / ratio_ma) - 1.0) * 100
            # Sample weekly (every 5 trading days) over last 60 days
            weekly = rs_line.tail(60).iloc[::5]
            if len(weekly) > 0:
                positive_pct = float((weekly > 0).mean())
                out["rs_persistence_pct"] = positive_pct

    # Volume breakout date: most recent day in last 90 days where max
    # 5-day-trailing volume crossed 1.5× the 50-day baseline going forward
    vol = df["Volume"].tail(90)
    base = df["Volume"].rolling(VOLUME_AVG_WINDOW, min_periods=10).mean().tail(90)
    if len(vol) >= 5:
        rolling_max = vol.rolling(5).max()
        ratio = (rolling_max / base).fillna(0)
        breakout_days = ratio.index[ratio >= WEINSTEIN_MIN_VOLUME_SURGE]
        if len(breakout_days) > 0:
            out["volume_breakout_date"] = breakout_days[-1].strftime("%Y-%m-%d")

    # Compose human-readable narrative
    parts: list[str] = []
    if out["stage_2_since"]:
        parts.append(
            f"In Stage 2 since **{out['stage_2_since']}** "
            f"({out['weeks_in_stage_2']} weeks)"
        )
    else:
        parts.append("Stage 2 transition predates the 2-year price history")
    if out["rs_persistence_pct"] is not None:
        pct = out["rs_persistence_pct"]
        weeks = int(round(pct * 12))
        if pct >= 0.75:
            parts.append(
                f"Mansfield RS positive **{weeks}/12 weeks** — consistent outperformer"
            )
        elif pct >= 0.5:
            parts.append(
                f"Mansfield RS positive {weeks}/12 weeks — moderately strong"
            )
        else:
            parts.append(
                f"Mansfield RS positive only {weeks}/12 weeks — weaker outperformance"
            )
    if out["volume_breakout_date"]:
        parts.append(
            f"Volume gate confirmed on **{out['volume_breakout_date']}**"
        )
    out["narrative"] = ". ".join(parts) + "."
    return out


def compute_position_projection(
    r: "StageResult",
    shares: int = 0,
    dollars: float = 0.0,
) -> dict:
    """
    Project P&L for a Weinstein-style entry on this ticker.

    Uses the existing TradeSetup (entry, stop_loss) plus two projected
    target levels derived from the 52-week base depth (Weinstein Ch. 3
    "measured move"):

        base_depth   = 52w_high − 52w_low
        short_target = entry + 25% × base_depth   (first major resistance)
        long_target  = entry + 100% × base_depth  (full measured move)

    Time-window estimates come from Weinstein Ch. 3:
        Short-term swing  ~ 4–8 weeks for a Stage 2 stock to reach 25%
                            of its base depth on average
        Long-term move    ~ 4–12 months for the full measured move,
                            depending on MA slope strength

    Steeper MA slopes shorten the windows (the trend is moving faster).

    Parameters
    ----------
    r       : StageResult (must have a valid trade_setup or last_close)
    shares  : if > 0, size the position by share count
    dollars : if > 0 (and shares == 0), size by capital and floor to whole shares

    Returns
    -------
    dict with keys:
        applicable, error, shares, capital, entry, stop_loss,
        max_loss, max_loss_pct,
        short_target, short_profit, short_pct, short_rr, short_days_low, short_days_high,
        long_target,  long_profit,  long_pct,  long_rr,  long_days_low,  long_days_high,
        reasoning (list[str]) — human-readable derivation
    """
    out: dict = {"applicable": False, "error": None}

    if r is None or r.error:
        out["error"] = r.error if r else "no result"
        return out
    if r.stage != 2:
        # Descriptive — just state the stage. The user can decide what
        # to do; the calculator is simply not applicable for non-Stage-2.
        stage_descriptions = {
            1: f"{r.ticker} is in Stage 1 (Basing). The 30-week MA is flat "
               f"and the breakout hasn't occurred yet — once it does, the "
               f"ticker will appear in the BUY or Watching panel.",
            3: f"{r.ticker} is in Stage 3 (Topping). The 30-week MA has "
               f"flattened or rolled over after a prior Stage 2 advance. "
               f"If the MA turns back up, the ticker will re-enter Stage 2 "
               f"and appear in BUY/Watching on the next scan.",
            4: f"{r.ticker} is in Stage 4 (Declining). The 30-week MA is "
               f"falling. The position calculator projects long-side gains, "
               f"which don't apply here.",
        }
        out["error"] = stage_descriptions.get(
            r.stage,
            f"{r.ticker} is in Stage {r.stage} ({r.stage_name}). "
            f"The calculator projects Stage 2 long-side gains.",
        )
        return out
    setup = getattr(r, "setup_type", "n/a")
    if setup == "extended":
        out["error"] = (
            f"{r.ticker} is Late Stage 2 (+{r.pct_above_ma:.0%} above the "
            f"30-week MA). The MA is still rising, so the stock is technically "
            f"still in Stage 2, but the high-probability entry has passed. "
            f"The measured-move projection here would assume a fresh breakout, "
            f"which isn't the case — wait for a pullback to support."
        )
        return out
    if r.trade_setup is None or not r.trade_setup.applicable:
        out["error"] = "No valid trade setup on this ticker."
        return out

    ts = r.trade_setup
    entry = float(ts.entry_price)
    stop = float(ts.stop_loss)
    if entry <= 0 or stop <= 0:
        out["error"] = "Invalid entry or stop price."
        return out

    # Size the position
    if shares <= 0 and dollars <= 0:
        out["error"] = "Provide shares > 0 OR dollars > 0."
        return out
    if shares <= 0:
        shares = int(dollars // entry)
        if shares < 1:
            out["error"] = (
                f"${dollars:,.2f} is less than the entry price of "
                f"${entry:,.2f}. Increase the budget or enter share count."
            )
            return out

    capital = shares * entry

    # Risk: what we lose if stopped out at the 30-week MA invalidator
    risk_per_share = entry - stop
    max_loss = shares * risk_per_share
    max_loss_pct = (risk_per_share / entry) if entry > 0 else 0.0

    # Targets — Weinstein measured-move from the 52-week base
    base_depth = float(r.high_52w) - float(r.low_52w)
    if base_depth <= 0:
        # Fallback: use 12% / 25% rules of thumb if base data missing
        short_target = entry * 1.07
        long_target = entry * 1.25
        base_depth_used = 0.0
    else:
        short_target = entry + 0.25 * base_depth
        long_target = entry + 1.00 * base_depth
        base_depth_used = base_depth

    # Profit projections
    short_profit = (short_target - entry) * shares
    long_profit = (long_target - entry) * shares
    short_pct = (short_target - entry) / entry if entry > 0 else 0.0
    long_pct = (long_target - entry) / entry if entry > 0 else 0.0
    short_rr = (short_target - entry) / risk_per_share if risk_per_share > 0 else 0.0
    long_rr = (long_target - entry) / risk_per_share if risk_per_share > 0 else 0.0

    # Time-window estimates — adjust by MA slope steepness
    # Baseline (Weinstein Ch. 3 averages):
    #   short-term: 4–8 weeks  →  28–56 trading days
    #   long-term:  4–12 months → ~84–252 trading days
    slope = float(r.ma_slope_pct)  # e.g., 0.03 = +3%/4w
    # Steeper slopes accelerate the move (clamp to a sensible factor 0.6-1.4)
    speed_factor = max(0.6, min(1.4, 1.0 - (slope - 0.02) * 8))
    short_days_low = max(7, int(round(28 * speed_factor)))
    short_days_high = max(short_days_low + 7, int(round(56 * speed_factor)))
    long_days_low = max(short_days_high + 14, int(round(84 * speed_factor)))
    long_days_high = max(long_days_low + 30, int(round(252 * speed_factor)))

    reasoning = [
        f"Position sized at {shares} share(s) × ${entry:,.2f} entry "
        f"= ${capital:,.2f} capital deployed.",
        f"Stop-loss at the 30-week moving average (${stop:,.2f}) — "
        f"Weinstein's invalidation level. If stopped, max loss is "
        f"${max_loss:,.2f} ({max_loss_pct:.1%} of entry capital).",
    ]
    if base_depth_used > 0:
        reasoning.append(
            f"Base depth = 52-week high (${r.high_52w:,.2f}) − 52-week low "
            f"(${r.low_52w:,.2f}) = ${base_depth_used:,.2f}. Per Weinstein "
            f"Ch. 3, project this as a measured move from the breakout entry."
        )
    else:
        reasoning.append(
            "52-week range data unavailable; using rule-of-thumb targets "
            "(short +7%, long +25% from entry)."
        )
    reasoning.append(
        f"Short-term target = entry + 25% × base depth "
        f"= ${short_target:,.2f} ({short_pct:+.1%}). Typical Weinstein "
        f"swing window is 4–8 weeks; adjusted for the current MA slope "
        f"({slope:+.1%}/4w), expect ~{short_days_low}–{short_days_high} "
        f"trading days."
    )
    reasoning.append(
        f"Long-term target = entry + 100% × base depth "
        f"= ${long_target:,.2f} ({long_pct:+.1%}). Full measured move "
        f"typically plays out in 4–12 months; adjusted for slope, expect "
        f"~{long_days_low}–{long_days_high} trading days."
    )
    reasoning.append(
        "Disclaimer: these are technical projections, not predictions. "
        "Real-world outcomes depend on macro shifts, sector rotation, and "
        "earnings events. Always size positions to the stop distance, not "
        "the target."
    )

    out.update({
        "applicable": True,
        "shares": shares,
        "capital": capital,
        "entry": entry,
        "stop_loss": stop,
        "risk_per_share": risk_per_share,
        "max_loss": max_loss,
        "max_loss_pct": max_loss_pct,
        "short_target": short_target,
        "short_profit": short_profit,
        "short_pct": short_pct,
        "short_rr": short_rr,
        "short_days_low": short_days_low,
        "short_days_high": short_days_high,
        "long_target": long_target,
        "long_profit": long_profit,
        "long_pct": long_pct,
        "long_rr": long_rr,
        "long_days_low": long_days_low,
        "long_days_high": long_days_high,
        "base_depth": base_depth_used,
        "ma_slope_pct": slope,
        "reasoning": reasoning,
    })
    return out


def format_result(r: StageResult) -> str:
    """Format a StageResult as a compact human-readable string."""
    if r.error:
        return f"{r.ticker}: ERROR — {r.error}"
    icon = {1: "◯", 2: "✓", 3: "⚠", 4: "✗"}.get(r.stage, "?")
    return (
        f"{r.ticker:<6}  Stage {r.stage} {icon} {r.stage_name:<11}  "
        f"Bull {r.bull_probability:.0%}  "
        f"vs MA {r.pct_above_ma:+.1%}  "
        f"slope {r.ma_slope_pct:+.1%}  "
        f"RS {r.mansfield_rs:+.1f}  "
        f"vol {r.volume_surge:.1f}x  "
        f"→ {r.action}"
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "AAPL", "MSFT", "O", "JPM", "XOM", "WMT", "PFE"]
    print(f"Running Weinstein Stage Analysis on {len(tickers)} tickers...\n")
    for t in tickers:
        r = analyze_stage(t)
        print(format_result(r))
