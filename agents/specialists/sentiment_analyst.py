"""
sentiment_analyst.py — The Sentiment Analyst (seventeenth specialist).

Orthogonal to the sector specialists and the Chartist Strategist, like
the Chartist. Reads four independent sentiment signals and synthesizes
them into a 0 to 100 sentiment score plus a plain English narrative.

Four signal sources:

    1. News tone. VADER compound score on the most recent Yahoo
       Finance headlines (already wired in ml/live_scorer.py).
       Captures journalist tone and recent narrative.

    2. Analyst rating changes. yfinance recommendations history over
       the last 90 days. Net upgrades minus downgrades. Captures
       sell side conviction shifts.

    3. Insider flow. yfinance insider transactions over the last 90
       days. Net dollar value of insider buys minus insider sells.
       Insiders are noisy sellers (diversification, taxes) but rarely
       buy without conviction; net buying is the strong signal.

    4. Earnings reaction. Average one day post-earnings move over the
       last four reported quarters. Pattern of pops or drops tells us
       how the market reads the company's prints.

The four components are aggregated into a weighted score (each 25
percent) with sign normalization to 0 to 100. Score is mapped to a
band (Bullish, Neutral, Mixed, Bearish) and a narrative paragraph is
composed in plain English.

The score also feeds into ml/confidence_scorer.py as the seventh input
to the overall Setup Confidence Score (Structure B Plus).

Phase 3 ships deterministic compose with no LLM call. Same pattern as
the Chartist. LLM enhancement hook is reserved for later.

Public interface:
    analyze_sentiment(ticker) -> SentimentReading
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Component weights inside the sentiment score (sum to 1.0)
WEIGHT_NEWS = 0.30
WEIGHT_ANALYSTS = 0.25
WEIGHT_INSIDERS = 0.25
WEIGHT_EARNINGS = 0.20

# Lookback windows
ANALYST_LOOKBACK_DAYS = 90
INSIDER_LOOKBACK_DAYS = 90
EARNINGS_LOOKBACK_QUARTERS = 4

# Band thresholds
BANDS = [
    (75.0, "Bullish",  "#3fb950"),
    (60.0, "Positive", "#56d364"),
    (40.0, "Mixed",    "#ffc107"),
    (25.0, "Cautious", "#ff7b1a"),
    (0.0,  "Bearish",  "#f85149"),
]


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class SentimentReading:
    ticker: str

    # Aggregate
    sentiment_score: float = 50.0          # 0-100
    sentiment_band: str = "Mixed"
    sentiment_color: str = "#ffc107"
    divergence_with_stage: bool = False    # vs technical stage

    # Per-component (each 0-100 after normalization)
    news_score: float = 50.0
    analyst_score: float = 50.0
    insider_score: float = 50.0
    earnings_score: float = 50.0

    # Raw measures for transparency
    vader_compound: Optional[float] = None        # -1 to +1
    analyst_net_actions: int = 0                  # upgrades minus downgrades
    insider_net_value: float = 0.0                # USD net flow
    earnings_avg_reaction: Optional[float] = None # avg 1d post-earnings %

    narrative: str = ""
    component_notes: list = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Individual signal fetchers
# ---------------------------------------------------------------------------

def _fetch_news_sentiment(ticker: str) -> tuple[Optional[float], str]:
    """Reuse the existing VADER pipeline in ml/live_scorer.py.

    Returns (compound_score, note). compound_score is in [-1, +1] or None.
    """
    try:
        from ml.live_scorer import _sentiment_compound
        compound = _sentiment_compound(ticker)
        if compound is None:
            return None, "no recent news headlines available"
        if compound >= 0.2:
            tone = "Bullish news tone"
        elif compound <= -0.2:
            tone = "Bearish news tone"
        else:
            tone = "Neutral news tone"
        return compound, f"{tone} (VADER compound {compound:+.2f})"
    except Exception as exc:
        return None, f"news sentiment fetch failed: {exc}"


def _fetch_analyst_actions(ticker: str) -> tuple[int, str]:
    """Count analyst rating changes over the last ANALYST_LOOKBACK_DAYS.

    Returns (net_upgrades_minus_downgrades, narrative).
    """
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).recommendations
        if df is None or df.empty:
            return 0, "no analyst rating data"
        # Some yfinance versions expose recommendations with a "To Grade"
        # column. Newer versions have a "strongBuy/buy/hold/sell/strongSell"
        # rolled-up shape. Handle both by inspecting columns.
        if "To Grade" in df.columns and "From Grade" in df.columns:
            recent = df.copy()
            recent.index = pd.to_datetime(recent.index, utc=True, errors="coerce")
            recent = recent.dropna(subset=["To Grade"])
            cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(
                days=ANALYST_LOOKBACK_DAYS
            )
            recent = recent[recent.index >= cutoff]
            up_keywords = {"buy", "strong buy", "outperform", "overweight",
                            "positive", "accumulate"}
            down_keywords = {"sell", "strong sell", "underperform",
                              "underweight", "negative", "reduce"}
            ups = 0
            downs = 0
            for _, row in recent.iterrows():
                to_grade = str(row.get("To Grade", "")).lower().strip()
                if any(k in to_grade for k in up_keywords):
                    ups += 1
                elif any(k in to_grade for k in down_keywords):
                    downs += 1
            net = ups - downs
            return net, (
                f"Last {ANALYST_LOOKBACK_DAYS} days: {ups} upgrade-style "
                f"ratings, {downs} downgrade-style ratings (net {net:+d})"
            )
        # Rolled-up format — use the most recent row
        # Columns are like strongBuy / buy / hold / sell / strongSell
        latest = df.iloc[0] if "period" in df.columns else df.iloc[-1]
        buys = int(latest.get("strongBuy", 0) or 0) + int(latest.get("buy", 0) or 0)
        sells = int(latest.get("strongSell", 0) or 0) + int(latest.get("sell", 0) or 0)
        net = buys - sells
        return net, (
            f"Current analyst consensus: {buys} buys/strong-buys, "
            f"{sells} sells/strong-sells (net {net:+d})"
        )
    except Exception as exc:
        return 0, f"analyst data unavailable: {exc}"


def _fetch_insider_flow(ticker: str) -> tuple[float, str]:
    """Compute net insider transaction value over the lookback window.

    Returns (net_dollar_flow, narrative). Positive = net buying.
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        df = getattr(t, "insider_transactions", None)
        if df is None or (hasattr(df, "empty") and df.empty):
            return 0.0, "no insider transaction data"
        # yfinance schemas vary across versions. Try common column names.
        df = df.copy()
        # Find a date-like column to filter
        date_col = None
        for c in ("Date", "Start Date", "startDate"):
            if c in df.columns:
                date_col = c
                break
        if date_col:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=INSIDER_LOOKBACK_DAYS)
            df = df[df[date_col] >= cutoff]
        # Value column
        value_col = None
        for c in ("Value", "value", "Transaction Value"):
            if c in df.columns:
                value_col = c
                break
        if value_col is None:
            return 0.0, "insider data has no usable value column"
        # Buy / Sell direction
        text_col = None
        for c in ("Transaction", "Text", "transactionDescription"):
            if c in df.columns:
                text_col = c
                break
        net_value = 0.0
        buy_count = 0
        sell_count = 0
        for _, row in df.iterrows():
            val = row.get(value_col, 0)
            try:
                val = float(val) if val is not None else 0.0
            except Exception:
                val = 0.0
            text = (str(row.get(text_col, "")) if text_col else "").lower()
            if "buy" in text or "purchase" in text:
                net_value += abs(val)
                buy_count += 1
            elif "sale" in text or "sell" in text or "disposition" in text:
                net_value -= abs(val)
                sell_count += 1
            else:
                # Default: treat positive value as buy, negative as sell
                net_value += val
        if buy_count + sell_count == 0:
            return 0.0, "no qualifying insider buy or sell activity"
        return net_value, (
            f"Insiders last {INSIDER_LOOKBACK_DAYS} days: {buy_count} buys, "
            f"{sell_count} sells, net ${net_value:,.0f}"
        )
    except Exception as exc:
        return 0.0, f"insider flow unavailable: {exc}"


def _fetch_earnings_reaction(ticker: str) -> tuple[Optional[float], str]:
    """Average one-day post-earnings price reaction over the last
    EARNINGS_LOOKBACK_QUARTERS quarters.

    Returns (avg_reaction_pct, narrative). avg_reaction_pct in decimal
    (e.g., +0.045 = average +4.5% the day after earnings).
    """
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        # earnings_dates returns past + future; we filter to past
        ed = getattr(t, "earnings_dates", None)
        if ed is None or (hasattr(ed, "empty") and ed.empty):
            return None, "no earnings calendar available"
        ed = ed.copy()
        if ed.index.tz is not None:
            ed.index = ed.index.tz_localize(None)
        past = ed[ed.index < datetime.now()].sort_index(ascending=False).head(
            EARNINGS_LOOKBACK_QUARTERS
        )
        if past.empty:
            return None, "no past earnings reactions in history"

        # Pull daily price history covering the earliest needed date
        oldest_date = past.index.min()
        hist = t.history(start=oldest_date - pd.Timedelta(days=10),
                         end=datetime.now(), auto_adjust=True, interval="1d")
        if hist is None or hist.empty:
            return None, "no price history for earnings reaction"
        if hist.index.tz is not None:
            hist.index = hist.index.tz_localize(None)

        reactions: list[float] = []
        for earn_date in past.index:
            # Find the price the day before and the day of/after the print
            try:
                pre_idx = hist.index.searchsorted(earn_date, side="left") - 1
                post_idx = pre_idx + 1
                if 0 <= pre_idx < len(hist) and 0 < post_idx < len(hist):
                    pre = float(hist["Close"].iloc[pre_idx])
                    post = float(hist["Close"].iloc[post_idx])
                    if pre > 0:
                        reactions.append((post - pre) / pre)
            except Exception:
                continue

        if not reactions:
            return None, "could not compute earnings reactions"
        avg = sum(reactions) / len(reactions)
        return avg, (
            f"Last {len(reactions)} earnings reactions averaged "
            f"{avg:+.1%} one-day move"
        )
    except Exception as exc:
        return None, f"earnings reaction unavailable: {exc}"


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------

def _norm_news(compound: Optional[float]) -> float:
    """VADER compound in [-1, +1] -> [0, 100]."""
    if compound is None:
        return 50.0
    return max(0.0, min(100.0, 50.0 + compound * 50.0))


def _norm_analysts(net: int) -> float:
    """Net upgrades minus downgrades -> [0, 100].

    +5 net upgrades = 90, 0 = 50, -5 net downgrades = 10.
    """
    return max(0.0, min(100.0, 50.0 + net * 8.0))


def _norm_insiders(net_value: float) -> float:
    """Net insider dollar flow -> [0, 100].

    Uses log scale because insider transactions vary by orders of
    magnitude across ticker market caps.
    """
    if net_value == 0:
        return 50.0
    sign = 1 if net_value > 0 else -1
    magnitude = math.log10(abs(net_value) + 1.0)
    # Anchor: $1M (log10 = 6) maps to a 25 point shift
    shift = min(40.0, magnitude * 4.0)
    return max(0.0, min(100.0, 50.0 + sign * shift))


def _norm_earnings(avg_reaction: Optional[float]) -> float:
    """Average earnings reaction -> [0, 100].

    +5% average reaction = 85, 0% = 50, -5% = 15.
    """
    if avg_reaction is None:
        return 50.0
    return max(0.0, min(100.0, 50.0 + avg_reaction * 700.0))


# ---------------------------------------------------------------------------
# Band lookup + narrative
# ---------------------------------------------------------------------------

def _band_for(score: float) -> tuple[str, str]:
    for threshold, name, color in BANDS:
        if score >= threshold:
            return name, color
    return "Bearish", "#f85149"


def _build_narrative(s: SentimentReading) -> str:
    parts = [f"Sentiment Score {s.sentiment_score:.0f} of 100 — {s.sentiment_band}."]

    # Surface the strongest and weakest components
    components = {
        "News tone":           s.news_score,
        "Analyst actions":     s.analyst_score,
        "Insider flow":        s.insider_score,
        "Earnings reaction":   s.earnings_score,
    }
    sorted_c = sorted(components.items(), key=lambda x: -x[1])
    strongest = sorted_c[0]
    weakest = sorted_c[-1]

    if strongest[1] >= 65:
        parts.append(f"Strongest signal: {strongest[0]} ({strongest[1]:.0f}).")
    if weakest[1] <= 40:
        parts.append(f"Weakest signal: {weakest[0]} ({weakest[1]:.0f}).")
    if s.component_notes:
        # The most informative note from each component
        for note in s.component_notes[:2]:
            parts.append(note + ".")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def analyze_sentiment(ticker: str,
                      technical_stage: Optional[int] = None
                      ) -> SentimentReading:
    """Compute the Sentiment Analyst reading for one ticker.

    Parameters
    ----------
    ticker : str
    technical_stage : optional int 1-4. If provided, divergence_with_stage
        is set to True when sentiment strongly contradicts the technical
        read (e.g., Stage 4 stock with Bullish sentiment, or Stage 2 with
        Bearish sentiment).

    Returns
    -------
    SentimentReading with score, band, components, and narrative.
    """
    ticker = ticker.upper()
    out = SentimentReading(ticker=ticker)
    notes: list[str] = []

    # 1) News tone (VADER)
    compound, news_note = _fetch_news_sentiment(ticker)
    out.vader_compound = compound
    out.news_score = _norm_news(compound)
    notes.append(news_note)

    # 2) Analyst actions
    net_actions, analyst_note = _fetch_analyst_actions(ticker)
    out.analyst_net_actions = net_actions
    out.analyst_score = _norm_analysts(net_actions)
    notes.append(analyst_note)

    # 3) Insider flow
    insider_value, insider_note = _fetch_insider_flow(ticker)
    out.insider_net_value = insider_value
    out.insider_score = _norm_insiders(insider_value)
    notes.append(insider_note)

    # 4) Earnings reactions
    avg_reaction, earnings_note = _fetch_earnings_reaction(ticker)
    out.earnings_avg_reaction = avg_reaction
    out.earnings_score = _norm_earnings(avg_reaction)
    notes.append(earnings_note)

    # Weighted aggregate
    score = (
        WEIGHT_NEWS * out.news_score
        + WEIGHT_ANALYSTS * out.analyst_score
        + WEIGHT_INSIDERS * out.insider_score
        + WEIGHT_EARNINGS * out.earnings_score
    )
    out.sentiment_score = round(score, 1)
    out.sentiment_band, out.sentiment_color = _band_for(score)
    out.component_notes = notes

    # Divergence detection vs technical stage
    if technical_stage is not None:
        if technical_stage == 2 and out.sentiment_score <= 35:
            out.divergence_with_stage = True
        elif technical_stage == 4 and out.sentiment_score >= 65:
            out.divergence_with_stage = True

    out.narrative = _build_narrative(out)
    return out


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["ZS", "NVDA", "ORA", "PLTR"]
    print(f"Sentiment Analyst on {len(tickers)} tickers\n")
    for t in tickers:
        s = analyze_sentiment(t)
        print(f"{t:<6}  Score {s.sentiment_score:>5.1f}  {s.sentiment_band:<10}"
              f"  News {s.news_score:>4.0f}  Analyst {s.analyst_score:>4.0f}"
              f"  Insider {s.insider_score:>4.0f}  Earnings {s.earnings_score:>4.0f}")
        print(f"          {s.narrative}")
        print()
