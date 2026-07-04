"""
sentiment_read.py — News sentiment read for the entry gate.

A thin wrapper that pulls a VADER compound score over the latest
yfinance headlines for one ticker and returns it alongside the
headline count and the as_of timestamp.

We already have:

  * ml.live_scorer._sentiment_compound — same idea but returns the
    compound score only (no count, no timestamp).
  * agents.specialists.sentiment_analyst — full four-signal Sentiment
    Analyst.

The Step 3b entry gate doesn't need the full analyst (insider flow,
analyst actions, earnings reactions). It just needs the news read on a
confirmed buy to flag a negative drumbeat before entry. So this module
keeps cost low: VADER over headlines, nothing else.

Never raises. Returns (None, 0, None) when no headlines are available
or any failure occurs, so the gate degrades quietly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# How many of the most recent headlines we score. Matches the Sentiment
# Analyst's window in ml.live_scorer for consistency.
_HEADLINE_LIMIT = 15

# Compound score below this flips the entry gate from pass to caution.
# Headlines are noisy in absolute terms but a deeply negative aggregate
# read on the day of a fresh breakout is the ORA-style warning.
SENTIMENT_CAUTION_THRESH = -0.30


def compute_sentiment(ticker: str) -> tuple[Optional[float], int, Optional[str]]:
    """Return (vader_compound, headline_count, as_of_iso).

    compound is mean VADER compound on the latest headlines, in
    [-1, +1]. count is how many headlines actually contributed. as_of
    is the wall-clock timestamp of the fetch. All three are None / 0
    when the lookup fails or the ticker has no headlines.
    """
    ticker = ticker.upper()
    try:
        import yfinance as yf
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        news = yf.Ticker(ticker).news or []
        if not news:
            return None, 0, None

        analyzer = SentimentIntensityAnalyzer()
        scores: list[float] = []
        for item in news[:_HEADLINE_LIMIT]:
            title = item.get("title") or item.get("content", {}).get("title", "")
            if title:
                scores.append(analyzer.polarity_scores(title)["compound"])

        if not scores:
            return None, 0, None

        compound = float(sum(scores) / len(scores))
        return compound, len(scores), datetime.utcnow().isoformat() + "Z"
    except Exception as exc:
        logger.warning(f"sentiment_read: failed for {ticker}: {exc}")
        return None, 0, None


def caution_for_sentiment(compound: Optional[float]) -> bool:
    """True when this compound score warrants a caution downgrade."""
    if compound is None:
        return False
    return compound <= SENTIMENT_CAUTION_THRESH


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "ORA", "GS"]
    for t in tickers:
        c, n, ts = compute_sentiment(t)
        if c is None:
            print(f"{t:<6} no headlines available")
        else:
            flag = " (caution)" if caution_for_sentiment(c) else ""
            print(f"{t:<6} compound {c:+.2f}  headlines {n}  as_of {ts}{flag}")
