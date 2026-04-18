"""
live_scorer.py — Live bull probability scorer.

Replaces the static JSON bull_probability from the old system with a
transparent, live-computed composite score. Features are pulled fresh
each call:
    - 3-month price momentum (yfinance)
    - Revenue growth YoY (SEC XBRL)
    - Operating cash flow growth YoY (SEC XBRL)
    - News sentiment compound (VADER on yfinance headlines)
    - Debt-to-equity trend (SEC XBRL)

Each feature contributes a signed component in [-1, +1], combined
into a logistic-squashed probability in [0, 1].

This is deliberately rule-based / transparent rather than an opaque
RF — users can explain every number.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature weights — tuned for balance, not back-fit
# ---------------------------------------------------------------------------
WEIGHTS = {
    "momentum_3m":        1.2,   # price strength
    "revenue_growth_yoy": 1.0,   # top-line trend
    "ocf_growth_yoy":     0.8,   # cash flow quality
    "sentiment":          0.6,   # news tone
    "leverage_delta":    -0.5,   # rising leverage = headwind
}


@dataclass
class ScoringResult:
    ticker: str
    bull_prob: float
    features: dict[str, Optional[float]]
    components: dict[str, float]
    explanation: str


# ---------------------------------------------------------------------------
# Feature extractors
# ---------------------------------------------------------------------------

def _momentum_3m(ticker: str) -> Optional[float]:
    """Return 3-month price return."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="6mo")
        if hist is None or hist.empty or len(hist) < 60:
            return None
        recent = hist["Close"].iloc[-1]
        ago_3m = hist["Close"].iloc[-63] if len(hist) >= 63 else hist["Close"].iloc[0]
        return float((recent - ago_3m) / ago_3m)
    except Exception as exc:
        logger.warning(f"momentum_3m failed for {ticker}: {exc}")
        return None


def _revenue_growth_yoy(ticker: str) -> Optional[float]:
    try:
        from data.sec_client import get_income_statement
        data = get_income_statement(ticker, years=3)
        series = data.get("Revenue", [])
        if len(series) < 2:
            return None
        latest = series[-1].get("val")
        prior = series[-2].get("val")
        if latest is None or prior is None or prior == 0:
            return None
        return float((latest - prior) / prior)
    except Exception as exc:
        logger.warning(f"revenue_growth_yoy failed for {ticker}: {exc}")
        return None


def _ocf_growth_yoy(ticker: str) -> Optional[float]:
    try:
        from data.sec_client import get_cash_flow
        data = get_cash_flow(ticker, years=3)
        series = data.get("OperatingCashFlow", [])
        if len(series) < 2:
            return None
        latest = series[-1].get("val")
        prior = series[-2].get("val")
        if latest is None or prior is None or prior == 0:
            return None
        return float((latest - prior) / abs(prior))
    except Exception as exc:
        logger.warning(f"ocf_growth_yoy failed for {ticker}: {exc}")
        return None


def _sentiment_compound(ticker: str) -> Optional[float]:
    try:
        import yfinance as yf
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        news = yf.Ticker(ticker).news or []
        if not news:
            return None
        analyzer = SentimentIntensityAnalyzer()
        scores = []
        for item in news[:15]:
            title = item.get("title") or item.get("content", {}).get("title", "")
            if title:
                scores.append(analyzer.polarity_scores(title)["compound"])
        return float(sum(scores) / len(scores)) if scores else None
    except Exception as exc:
        logger.warning(f"sentiment failed for {ticker}: {exc}")
        return None


def _leverage_delta(ticker: str) -> Optional[float]:
    """Return change in debt-to-equity ratio over last year."""
    try:
        from data.sec_client import get_balance_sheet
        data = get_balance_sheet(ticker, periods=3)
        debt_series = data.get("LongTermDebt", [])
        equity_series = data.get("TotalEquity", [])
        if len(debt_series) < 2 or len(equity_series) < 2:
            return None

        # Build de_by_year: fiscal year → D/E ratio
        debt_by_year = {o.get("fy"): o.get("val") for o in debt_series}
        equity_by_year = {o.get("fy"): o.get("val") for o in equity_series}
        years = sorted([y for y in debt_by_year.keys() & equity_by_year.keys() if y])
        if len(years) < 2:
            return None

        latest_de = debt_by_year[years[-1]] / equity_by_year[years[-1]] \
            if equity_by_year[years[-1]] else None
        prior_de = debt_by_year[years[-2]] / equity_by_year[years[-2]] \
            if equity_by_year[years[-2]] else None
        if latest_de is None or prior_de is None or prior_de == 0:
            return None
        return float((latest_de - prior_de) / prior_de)
    except Exception as exc:
        logger.warning(f"leverage_delta failed for {ticker}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Component normalization — each feature → signed value in roughly [-1, +1]
# ---------------------------------------------------------------------------

def _norm_pct_return(x: Optional[float], scale: float = 0.20) -> float:
    """Normalize a percentage return. +20% → +1.0, -20% → -1.0."""
    if x is None:
        return 0.0
    return max(-1.0, min(1.0, x / scale))


def _norm_sentiment(x: Optional[float]) -> float:
    """VADER compound already in [-1, +1]."""
    return x if x is not None else 0.0


def _norm_leverage(x: Optional[float], scale: float = 0.20) -> float:
    """Leverage delta — +20% change → +1.0 (bad, so we'll negate via weight)."""
    if x is None:
        return 0.0
    return max(-1.0, min(1.0, x / scale))


# ---------------------------------------------------------------------------
# Main scoring
# ---------------------------------------------------------------------------

def compute_bull_prob(ticker: str) -> ScoringResult:
    """
    Compute a live bull probability for a ticker.

    Returns ScoringResult with features, components, and explanation.
    """
    ticker = ticker.upper()

    # Pull features
    momentum = _momentum_3m(ticker)
    rev_growth = _revenue_growth_yoy(ticker)
    ocf_growth = _ocf_growth_yoy(ticker)
    sentiment = _sentiment_compound(ticker)
    leverage = _leverage_delta(ticker)

    features = {
        "momentum_3m": momentum,
        "revenue_growth_yoy": rev_growth,
        "ocf_growth_yoy": ocf_growth,
        "sentiment": sentiment,
        "leverage_delta": leverage,
    }

    # Normalize each to [-1, +1]
    normalized = {
        "momentum_3m": _norm_pct_return(momentum, scale=0.15),
        "revenue_growth_yoy": _norm_pct_return(rev_growth, scale=0.20),
        "ocf_growth_yoy": _norm_pct_return(ocf_growth, scale=0.25),
        "sentiment": _norm_sentiment(sentiment),
        "leverage_delta": _norm_leverage(leverage, scale=0.20),
    }

    # Weighted component contributions
    components = {k: WEIGHTS[k] * v for k, v in normalized.items()}
    total_score = sum(components.values())

    # Sigmoid squash → [0, 1]
    bull_prob = 1.0 / (1.0 + math.exp(-total_score))

    # Build explanation
    exp_lines = [f"Live bull probability for {ticker}: {bull_prob:.2%}"]
    exp_lines.append("Feature contributions:")
    for name, comp in components.items():
        raw = features[name]
        raw_str = f"{raw:+.1%}" if raw is not None else "N/A"
        sign = "+" if comp >= 0 else ""
        exp_lines.append(f"  • {name:<22} raw={raw_str:<8}  contribution={sign}{comp:.2f}")
    exp_lines.append(f"Total logit: {total_score:+.2f}  →  bull_prob: {bull_prob:.2%}")

    return ScoringResult(
        ticker=ticker,
        bull_prob=bull_prob,
        features=features,
        components=components,
        explanation="\n".join(exp_lines),
    )


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["O", "PWR", "AAPL", "JPM"]
    for t in tickers:
        print(compute_bull_prob(t).explanation)
        print("-" * 70)
