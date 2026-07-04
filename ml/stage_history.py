"""
stage_history.py — Walk-forward stage classification across a ticker's
10-year weekly history.

The existing stage_analyzer.py computes the CURRENT stage (1-4) for a
ticker. This module reconstructs the full sequence of stage transitions
over the lookback window so the Chartist Strategist can write lines like:

    "ORA has been in Stage 2 for 12 weeks. The prior Stage 2 episode
     ran 38 weeks. Before that, Stage 1 basing for 22 weeks following
     a 47-week Stage 4 decline."

Classification rules mirror ml/stage_analyzer.py exactly so the
history is consistent with the live engine.

Public interface:
    analyze_stage_history(ticker, weekly=None) -> StageHistory
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from ml.stage_analyzer import (
    MA_WINDOW_DAYS,
    SLOPE_LOOKBACK_DAYS,
    STAGE1_MAX_PRICE_DEVIATION,
    STAGE1_MAX_SLOPE_PCT,
    STAGE2_MIN_PRICE_ABOVE_MA,
    STAGE2_MIN_SLOPE_PCT,
    STAGE4_MAX_PRICE_BELOW_MA,
    STAGE4_MAX_SLOPE_PCT,
)

logger = logging.getLogger(__name__)


# Convert the daily thresholds to a weekly-bar equivalent.
# stage_analyzer.py uses 150 trading days (30 weeks) for MA and 20
# trading days (4 weeks) for slope. On weekly bars those become 30 and
# 4 directly.
WEEKLY_MA_WINDOW = 30      # weeks
WEEKLY_SLOPE_LOOKBACK = 4   # weeks


_STAGE_NAME = {
    0: "Unknown",
    1: "Basing",
    2: "Advancing",
    3: "Topping",
    4: "Declining",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StageEpisode:
    """A run of consecutive weeks in the same stage."""
    stage: int                       # 1, 2, 3, or 4
    stage_name: str
    start_date: str                  # ISO yyyy-mm-dd
    end_date: str                    # ISO yyyy-mm-dd
    weeks: int                       # length of the run
    start_price: float
    end_price: float
    return_pct: float                # signed % return over the episode


@dataclass
class StageHistory:
    ticker: str
    history_weeks: int = 0
    history_start: Optional[str] = None
    history_end: Optional[str] = None

    episodes: list[StageEpisode] = field(default_factory=list)

    # The most recent (current) episode is convenient to surface directly
    current_stage: int = 0
    current_stage_name: str = ""
    current_stage_since: Optional[str] = None
    current_weeks_in_stage: int = 0

    # Aggregate stats over the full window
    stage_2_episodes: int = 0
    stage_4_episodes: int = 0
    longest_stage_2_weeks: int = 0
    longest_stage_4_weeks: int = 0

    # Plain-English narrative for the Chartist to consume directly
    narrative: str = ""

    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Walk-forward classifier
# ---------------------------------------------------------------------------

def _classify_week(close_now: float, ma_now: float, ma_prior: float) -> int:
    """Classify a single weekly bar into a stage 1-4.

    Uses the same numeric thresholds as stage_analyzer.py._classify_stage
    so live current-stage and historical stage agree on definitions.
    """
    pct_above = (close_now - ma_now) / ma_now if ma_now else 0.0
    slope = (ma_now - ma_prior) / ma_prior if ma_prior else 0.0

    # Stage 4: price below a declining MA
    if pct_above < STAGE4_MAX_PRICE_BELOW_MA and slope < STAGE4_MAX_SLOPE_PCT:
        return 4
    # Stage 2: price above a rising MA
    if pct_above > STAGE2_MIN_PRICE_ABOVE_MA and slope > STAGE2_MIN_SLOPE_PCT:
        return 2
    # Stage 3: price above MA but slope flattened or rolling over
    if pct_above > 0 and slope < STAGE2_MIN_SLOPE_PCT:
        return 3
    # Stage 1: everything else (basing / unclassified)
    return 1


# ---------------------------------------------------------------------------
# Episode aggregation
# ---------------------------------------------------------------------------

def _collapse_to_episodes(weekly: pd.DataFrame,
                          stage_series: pd.Series) -> list[StageEpisode]:
    """Collapse a per-week stage series into consecutive same-stage episodes.

    Filters out very short runs (≤ 2 weeks) — those are usually
    classification noise at stage boundaries and inflate the episode
    count without adding signal.
    """
    if len(stage_series) == 0:
        return []

    episodes: list[StageEpisode] = []
    current_stage = int(stage_series.iloc[0])
    start_idx = 0
    closes = weekly["Close"].values
    dates = weekly.index

    for i in range(1, len(stage_series)):
        if int(stage_series.iloc[i]) != current_stage:
            # Boundary reached — emit episode for the run that just ended
            end_idx = i - 1
            length = end_idx - start_idx + 1
            if length >= 3:  # skip 1- and 2-week noise runs
                start_price = float(closes[start_idx])
                end_price = float(closes[end_idx])
                episodes.append(StageEpisode(
                    stage=current_stage,
                    stage_name=_STAGE_NAME[current_stage],
                    start_date=dates[start_idx].strftime("%Y-%m-%d"),
                    end_date=dates[end_idx].strftime("%Y-%m-%d"),
                    weeks=length,
                    start_price=round(start_price, 2),
                    end_price=round(end_price, 2),
                    return_pct=round(
                        (end_price - start_price) / start_price
                        if start_price else 0.0,
                        4,
                    ),
                ))
            current_stage = int(stage_series.iloc[i])
            start_idx = i

    # Emit the final (current) episode regardless of length — it's the
    # one the user cares most about
    end_idx = len(stage_series) - 1
    length = end_idx - start_idx + 1
    start_price = float(closes[start_idx])
    end_price = float(closes[end_idx])
    episodes.append(StageEpisode(
        stage=current_stage,
        stage_name=_STAGE_NAME[current_stage],
        start_date=dates[start_idx].strftime("%Y-%m-%d"),
        end_date=dates[end_idx].strftime("%Y-%m-%d"),
        weeks=length,
        start_price=round(start_price, 2),
        end_price=round(end_price, 2),
        return_pct=round(
            (end_price - start_price) / start_price if start_price else 0.0,
            4,
        ),
    ))

    return episodes


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------

def _build_narrative(hist: StageHistory) -> str:
    """Compose a short plain-English summary of the stage history.

    Three to four sentences. Reads as if a chart analyst is talking,
    not a script.
    """
    if not hist.episodes:
        return "No stage history available."

    parts: list[str] = []

    # Current stage line
    current = hist.episodes[-1]
    if current.stage == 2:
        if current.weeks <= 8:
            parts.append(
                f"{hist.ticker} is fresh into Stage 2, only {current.weeks} "
                f"weeks since the breakout from {current.start_price} on "
                f"{current.start_date}."
            )
        elif current.weeks <= 20:
            parts.append(
                f"{hist.ticker} has been in Stage 2 for {current.weeks} "
                f"weeks (since {current.start_date}), up "
                f"{current.return_pct:+.0%} since the breakout."
            )
        else:
            parts.append(
                f"{hist.ticker} has been advancing for {current.weeks} "
                f"weeks (since {current.start_date}), now "
                f"{current.return_pct:+.0%} from the breakout level. "
                f"This is a mature Stage 2 run."
            )
    elif current.stage == 1:
        parts.append(
            f"{hist.ticker} has been basing in Stage 1 for {current.weeks} "
            f"weeks (since {current.start_date}), awaiting a breakout."
        )
    elif current.stage == 3:
        parts.append(
            f"{hist.ticker} has been in Stage 3 topping for {current.weeks} "
            f"weeks (since {current.start_date}). The 30 week MA is "
            f"flattening or rolling over."
        )
    elif current.stage == 4:
        parts.append(
            f"{hist.ticker} has been declining in Stage 4 for "
            f"{current.weeks} weeks (since {current.start_date}), down "
            f"{current.return_pct:+.0%}."
        )

    # Prior episode line (if we have at least one prior)
    if len(hist.episodes) >= 2:
        prior = hist.episodes[-2]
        parts.append(
            f"The prior episode was {prior.weeks} weeks in Stage "
            f"{prior.stage} ({prior.stage_name}), running "
            f"{prior.return_pct:+.0%} from "
            f"{prior.start_date} to {prior.end_date}."
        )

    # Aggregate context
    if hist.stage_2_episodes >= 2:
        parts.append(
            f"Over the {hist.history_weeks // 52}-year window, "
            f"{hist.ticker} has had {hist.stage_2_episodes} distinct Stage 2 "
            f"advances (longest {hist.longest_stage_2_weeks} weeks) and "
            f"{hist.stage_4_episodes} Stage 4 declines (longest "
            f"{hist.longest_stage_4_weeks} weeks)."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def analyze_stage_history(ticker: str,
                          weekly: Optional[pd.DataFrame] = None,
                          history_years: int = 10) -> StageHistory:
    """Walk through a ticker's weekly history and identify every stage transition.

    Parameters
    ----------
    ticker : str
    weekly : optional pre-loaded weekly OHLCV (with Close column). If
        None, fetched from yfinance.
    history_years : how far back to look (default 10).

    Returns
    -------
    StageHistory with episodes, current-stage stats, and a narrative.
    """
    ticker = ticker.upper()
    if weekly is None:
        try:
            import yfinance as yf
            weekly = yf.Ticker(ticker).history(
                period=f"{history_years}y",
                interval="1wk",
                auto_adjust=True,
            )
        except Exception as exc:
            return StageHistory(ticker=ticker, error=str(exc))

    if weekly is None or weekly.empty or len(weekly) < WEEKLY_MA_WINDOW + 5:
        return StageHistory(
            ticker=ticker,
            error="insufficient weekly history",
        )

    # Compute 30-week MA on the weekly series
    closes = weekly["Close"].copy()
    ma = closes.rolling(WEEKLY_MA_WINDOW, min_periods=WEEKLY_MA_WINDOW).mean()
    ma_prior = ma.shift(WEEKLY_SLOPE_LOOKBACK)

    # Classify every week that has enough history
    stages = []
    valid_idx = []
    for i in range(len(weekly)):
        if pd.isna(ma.iloc[i]) or pd.isna(ma_prior.iloc[i]):
            continue
        s = _classify_week(closes.iloc[i], ma.iloc[i], ma_prior.iloc[i])
        stages.append(s)
        valid_idx.append(weekly.index[i])

    if not stages:
        return StageHistory(
            ticker=ticker,
            error="no valid weeks after MA warmup",
        )

    stage_series = pd.Series(stages, index=valid_idx)
    classified_weekly = weekly.loc[valid_idx]
    episodes = _collapse_to_episodes(classified_weekly, stage_series)

    # Aggregates
    s2_eps = [e for e in episodes if e.stage == 2]
    s4_eps = [e for e in episodes if e.stage == 4]
    longest_s2 = max((e.weeks for e in s2_eps), default=0)
    longest_s4 = max((e.weeks for e in s4_eps), default=0)

    # Current stage details (last episode)
    last = episodes[-1] if episodes else None

    hist = StageHistory(
        ticker=ticker,
        history_weeks=len(stage_series),
        history_start=valid_idx[0].strftime("%Y-%m-%d"),
        history_end=valid_idx[-1].strftime("%Y-%m-%d"),
        episodes=episodes,
        current_stage=last.stage if last else 0,
        current_stage_name=last.stage_name if last else "",
        current_stage_since=last.start_date if last else None,
        current_weeks_in_stage=last.weeks if last else 0,
        stage_2_episodes=len(s2_eps),
        stage_4_episodes=len(s4_eps),
        longest_stage_2_weeks=longest_s2,
        longest_stage_4_weeks=longest_s4,
    )
    hist.narrative = _build_narrative(hist)
    return hist


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "O", "INTC", "AAPL"]
    for t in tickers:
        h = analyze_stage_history(t)
        if h.error:
            print(f"{t}: ERROR — {h.error}")
            continue
        print(f"\n{t} — {h.history_weeks} weeks of history "
              f"({h.history_start} to {h.history_end}):")
        print(f"  Current: Stage {h.current_stage} ({h.current_stage_name}), "
              f"{h.current_weeks_in_stage} weeks since {h.current_stage_since}")
        print(f"  Stage 2 episodes: {h.stage_2_episodes} "
              f"(longest {h.longest_stage_2_weeks}w)")
        print(f"  Stage 4 episodes: {h.stage_4_episodes} "
              f"(longest {h.longest_stage_4_weeks}w)")
        print(f"  Narrative: {h.narrative}")
