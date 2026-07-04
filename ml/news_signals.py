"""
news_signals.py - News, catalyst, sentiment, and macro signals (the "stay current" layer).

Per the project charter's "signals beyond Weinstein." All sources are API-first
and licensed, never scraped:

  * SEC 8-K  - hard, typed catalysts (earnings, M&A, exec change, red flags).
               Highest signal-to-noise; item codes classify the event.
  * AV news  - Alpha Vantage NEWS_SENTIMENT: scored, ticker-relevance-weighted.
               Free tier (25/day) -> only call on the shortlist, cached 24h.
  * VADER    - ml.sentiment_read fallback over yfinance headlines (unlimited, free).
  * StockTwits - optional, low-weight crowd attention + froth flag (noisy).
  * VIX      - market fear gauge for the regime dial (not per-stock).

Design rule: these flag, route, and size risk. They never trigger an entry on
their own; the technical breakout still confirms.

    PYTHONPATH=. python3 ml/news_signals.py
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "news"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_TTL = 12 * 60 * 60
SEC_UA = os.environ.get("SEC_USER_AGENT", "OROS Research admin@adminaillc.com")

# 8-K item-code -> (label, kind). kind: catalyst / earnings / risk / info
_ITEM_CODES = {
    "1.01": ("Material agreement", "catalyst"),
    "1.03": ("Bankruptcy", "risk"),
    "2.01": ("Completion of acquisition/disposition", "catalyst"),
    "2.02": ("Earnings / results", "earnings"),
    "2.03": ("New debt obligation", "info"),
    "3.01": ("Delisting / listing-rule notice", "risk"),
    "4.01": ("Auditor change", "risk"),
    "4.02": ("Non-reliance on prior financials", "risk"),
    "5.01": ("Change in control", "risk"),
    "5.02": ("Executive / board change", "info"),
    "7.01": ("Reg FD disclosure", "info"),
    "8.01": ("Other material event", "info"),
}


@dataclass
class NewsSignal:
    ticker: str
    sentiment_score: float = 0.0          # -1..1
    sentiment_source: str = "none"
    news_count: int = 0
    catalysts: list[str] = field(default_factory=list)
    earnings_risk: bool = False
    red_flags: list[str] = field(default_factory=list)
    social_attention: int = 0
    froth: bool = False
    summary: str = ""


# ---------------------------------------------------------------------------
def _cache(ticker: str, payload: Optional[dict] = None) -> Optional[dict]:
    p = _CACHE_DIR / f"{ticker.upper()}.json"
    if payload is None:
        if p.exists() and time.time() - p.stat().st_mtime < _TTL:
            try:
                return json.loads(p.read_text())
            except Exception:
                return None
        return None
    p.write_text(json.dumps(payload))
    return payload


def _get(url: str, headers: dict, timeout: int = 20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout).read()


# ---------------------------------------------------------------------------
# Market: VIX
# ---------------------------------------------------------------------------
def vix_read() -> dict:
    """Return {level, avg20, hint} for the VIX, or empty on failure."""
    try:
        import yfinance as yf
        h = yf.Ticker("^VIX").history(period="1mo")["Close"]
        level, avg = float(h.iloc[-1]), float(h.mean())
        hint = "risk_off" if level > 22 else ("elevated" if level > 18 else "risk_on")
        return {"level": round(level, 1), "avg20": round(avg, 1), "hint": hint}
    except Exception as e:  # noqa: BLE001
        logger.warning("VIX read failed: %s", e)
        return {}


# ---------------------------------------------------------------------------
# SEC 8-K typed catalysts
# ---------------------------------------------------------------------------
def get_8k_catalysts(ticker: str, cik: Optional[str] = None, lookback_days: int = 120) -> dict:
    """Recent 8-K events classified by item code."""
    if cik is None:
        try:
            from data.universe import fetch_sec_cik_map
            cik = fetch_sec_cik_map().get(ticker.upper())
        except Exception:
            cik = None
    out = {"catalysts": [], "earnings_recent": False, "red_flags": []}
    if not cik:
        return out
    try:
        sub = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                              {"User-Agent": SEC_UA}))
    except Exception as e:  # noqa: BLE001
        logger.warning("SEC submissions failed for %s: %s", ticker, e)
        return out
    rec = sub.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    dates = rec.get("filingDate", [])
    items = rec.get("items", [""] * len(forms))
    today = date.today()
    for f, d, it in zip(forms, dates, items):
        if f != "8-K":
            continue
        try:
            days_ago = (today - date.fromisoformat(d)).days
        except Exception:
            continue
        if days_ago > lookback_days:
            continue
        for code in [c.strip() for c in (it or "").split(",") if c.strip()]:
            label, kind = _ITEM_CODES.get(code, (f"Item {code}", "info"))
            tag = f"{label} ({d})"
            if kind == "earnings":
                if days_ago <= 14:
                    out["earnings_recent"] = True
                out["catalysts"].append(tag)
            elif kind == "catalyst":
                out["catalysts"].append(tag)
            elif kind == "risk":
                out["red_flags"].append(tag)
    return out


# ---------------------------------------------------------------------------
# Sentiment: Alpha Vantage primary, VADER fallback
# ---------------------------------------------------------------------------
def av_sentiment(ticker: str) -> Optional[tuple[float, int]]:
    key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if not key:
        return None
    try:
        data = json.loads(_get(
            f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT"
            f"&tickers={ticker}&limit=50&apikey={key}", {"User-Agent": "OROS"}))
        feed = data.get("feed", [])
        if not feed:
            return None
        num, den = 0.0, 0.0
        for a in feed:
            for t in a.get("ticker_sentiment", []):
                if t.get("ticker") == ticker.upper():
                    rel = float(t.get("relevance_score", 0) or 0)
                    sc = float(t.get("ticker_sentiment_score", 0) or 0)
                    num += rel * sc
                    den += rel
        if den == 0:
            return None
        return num / den, len(feed)
    except Exception as e:  # noqa: BLE001
        logger.info("AV news unavailable for %s (%s); falling back", ticker, e)
        return None


def vader_sentiment(ticker: str) -> Optional[tuple[float, int]]:
    try:
        from ml.sentiment_read import compute_sentiment
        comp, count, _ = compute_sentiment(ticker)
        if comp is None:
            return None
        return float(comp), int(count)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Social: StockTwits attention + froth (optional, low weight)
# ---------------------------------------------------------------------------
def stocktwits(ticker: str) -> dict:
    try:
        d = json.loads(_get(f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json",
                            {"User-Agent": "Mozilla/5.0 research"}))
        m = d.get("messages", [])
        bull = sum(1 for x in m if (x.get("entities", {}).get("sentiment") or {}).get("basic") == "Bullish")
        bear = sum(1 for x in m if (x.get("entities", {}).get("sentiment") or {}).get("basic") == "Bearish")
        froth = len(m) >= 20 and bull >= 8 and bear == 0      # one-sided euphoria
        return {"count": len(m), "bull": bull, "bear": bear, "froth": froth}
    except Exception:
        return {"count": 0, "bull": 0, "bear": 0, "froth": False}


# ---------------------------------------------------------------------------
# Unified per-ticker signal
# ---------------------------------------------------------------------------
def news_signal(ticker: str, cik: Optional[str] = None,
                use_av: bool = True, use_social: bool = False) -> NewsSignal:
    ticker = ticker.upper()
    cached = _cache(ticker)
    if cached is not None:
        return NewsSignal(**cached)

    sig = NewsSignal(ticker=ticker)

    sent, source = None, "none"
    if use_av:
        sent = av_sentiment(ticker)          # called at most once
        source = "alpha_vantage"
    if sent is None:
        sent = vader_sentiment(ticker)
        source = "vader"
    if sent:
        sig.sentiment_score = round(sent[0], 3)
        sig.news_count = sent[1]
        sig.sentiment_source = source

    cat = get_8k_catalysts(ticker, cik=cik)
    sig.catalysts = cat["catalysts"][:6]
    sig.earnings_risk = cat["earnings_recent"]
    sig.red_flags = cat["red_flags"][:4]

    if use_social:
        st = stocktwits(ticker)
        sig.social_attention = st["count"]
        sig.froth = st["froth"]

    bits = []
    if sig.sentiment_score:
        bits.append(f"news {sig.sentiment_score:+.2f} ({sig.sentiment_source}, n={sig.news_count})")
    if sig.earnings_risk:
        bits.append("EARNINGS-RISK")
    if sig.red_flags:
        bits.append("RED-FLAG: " + "; ".join(sig.red_flags))
    if sig.catalysts:
        bits.append("catalysts: " + "; ".join(sig.catalysts[:2]))
    if sig.froth:
        bits.append("social-froth")
    sig.summary = " | ".join(bits) or "no recent news/catalysts"

    _cache(ticker, sig.__dict__)
    return sig


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print("VIX:", vix_read())
    print("\nPer-ticker signals (AV used only for the first to save quota):")
    for i, t in enumerate(["NVDA", "AAPL", "O"]):
        sig = news_signal(t, use_av=(i == 0), use_social=True)
        print(f"\n {t}: {sig.summary}")
        print(f"    sentiment={sig.sentiment_score} src={sig.sentiment_source} "
              f"earnings_risk={sig.earnings_risk} social={sig.social_attention} froth={sig.froth}")


if __name__ == "__main__":
    _selftest()
