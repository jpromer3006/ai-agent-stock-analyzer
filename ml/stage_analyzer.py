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

    # Reference levels (from daily OHLCV)
    high_52w: float = 0.0
    low_52w: float = 0.0
    high_50d: float = 0.0
    low_50d: float = 0.0

    # Weinstein trade setup (computed when stage allows)
    trade_setup: Optional[TradeSetup] = None

    # Meta
    as_of_date: Optional[str] = None
    error: Optional[str] = None
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

    # Volume surge
    vol_avg = volume.rolling(VOLUME_AVG_WINDOW, min_periods=10).mean()
    last_vol_avg = float(vol_avg.iloc[-1]) if not pd.isna(vol_avg.iloc[-1]) else 1.0
    last_vol = float(volume.iloc[-1])
    volume_surge = last_vol / last_vol_avg if last_vol_avg > 0 else 1.0

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

    return {
        "last_close": last_close,
        "ma_30w": last_ma,
        "ma_slope_pct": ma_slope_pct,
        "pct_above_ma": pct_above_ma,
        "volume_surge": volume_surge,
        "pct_from_52w_high": pct_from_52w_high,
        "pct_from_52w_low": pct_from_52w_low,
        "mansfield_rs": mansfield_rs,
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
    slope = ind["ma_slope_pct"]
    rs = ind["mansfield_rs"]

    exp: list[str] = []
    exp.append(f"Price vs 30W MA: {pct_above:+.2%}")
    exp.append(f"30W MA slope (4wk): {slope:+.2%}")
    exp.append(f"Mansfield RS vs SPY: {rs:+.1f}")

    # Stage 4: Declining
    if pct_above < STAGE4_MAX_PRICE_BELOW_MA and slope < STAGE4_MAX_SLOPE_PCT:
        # Confidence grows with how deep below MA and how steep the decline
        confidence = min(1.0, (abs(pct_above) / 0.15) * 0.5 + (abs(slope) / 0.05) * 0.5)
        exp.append("✗ Price below declining 30W MA → Stage 4 Declining")
        return 4, "Declining", confidence, exp

    # Stage 2: Advancing
    if pct_above > STAGE2_MIN_PRICE_ABOVE_MA and slope > STAGE2_MIN_SLOPE_PCT:
        confidence = min(1.0, (pct_above / 0.15) * 0.5 + (slope / 0.05) * 0.5)
        exp.append("✓ Price above rising 30W MA → Stage 2 Advancing")
        return 2, "Advancing", confidence, exp

    # Stage 3: Topping — price still above MA but MA has flattened/rolled over
    if pct_above > 0 and slope < STAGE2_MIN_SLOPE_PCT:
        # Topping: weakening momentum
        confidence = 0.4 + min(0.4, abs(slope) * 20)
        exp.append("⚠ Price above MA but slope flattening → Stage 3 Topping")
        return 3, "Topping", confidence, exp

    # Stage 1: Basing — price near flat MA (default)
    confidence = 0.5
    if abs(pct_above) < STAGE1_MAX_PRICE_DEVIATION and abs(slope) < STAGE1_MAX_SLOPE_PCT:
        confidence = 0.7
        exp.append("◯ Price near flat MA → Stage 1 Basing (wait for breakout)")
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
# Action label
# ---------------------------------------------------------------------------

def _action_label(stage: int, bull_prob: float) -> str:
    if stage == 2 and bull_prob >= 0.85:
        return "STRONG BUY"
    if stage == 2:
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


def _get_benchmark() -> Optional[pd.DataFrame]:
    global _benchmark_cache
    if _benchmark_cache is None:
        _benchmark_cache = _fetch_history(BENCHMARK_TICKER, period="2y")
    return _benchmark_cache


def analyze_stage(ticker: str, df: Optional[pd.DataFrame] = None) -> StageResult:
    """
    Run full Weinstein stage analysis on a ticker.

    Parameters
    ----------
    ticker : str
    df : optional pre-loaded OHLCV dataframe (otherwise fetched from yfinance)

    Returns
    -------
    StageResult
    """
    ticker = ticker.upper()
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
        return result

    bench = _get_benchmark()

    try:
        ind = _compute_indicators(df, bench_df=bench)
        stage, stage_name, confidence, explanation = _classify_stage(ind)
        bull_prob = _compute_bull_probability(stage, ind, confidence)
        action = _action_label(stage, bull_prob)

        result.stage = stage
        result.stage_name = stage_name
        result.bull_probability = bull_prob
        result.confidence = confidence
        result.action = action
        result.last_close = ind["last_close"]
        result.ma_30w = ind["ma_30w"]
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
        result.explanation = explanation
    except Exception as exc:
        result.error = str(exc)

    return result


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
