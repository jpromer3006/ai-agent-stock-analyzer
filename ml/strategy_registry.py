"""
strategy_registry.py - Interchangeable, book-equal strategy engine.

The platform treats every book as a peer strategy. Each one implements the same
interface, `score(ctx) -> StrategyScore`, and registers itself. Nothing is
hard-coded as a foundation: Weinstein is one registry entry like the rest, and any
strategy can be weighted to zero by the router. The market (regime) decides which
strategies are active and how much each is trusted, so the strategies work
interchangeably rather than one ruling the others.

Strategies (book -> key):
  weinstein  Weinstein   stage analysis (wraps ml.stage_analyzer)
  sepa       Minervini   SEPA trend template
  canslim    O'Neil      CANSLIM chartable signals (+ earnings growth if available)
  technical  Tech book   classical TA composite (RSI, MACD, trend)
  swing      Swing book  short-term breakout/pullback
  value      Graham      Graham value from fundamentals
  growth     Lynch       GARP (PEG + growth) from fundamentals
  ipo        O'Neil/Minervini  new-issue momentum (wraps ml.new_listing_scanner)

Quant strategies compute from price data; value/growth read fundamentals (yfinance,
cached) and degrade gracefully when unavailable. All return one common shape so the
portfolio engine blends them identically.

    PYTHONPATH=. python3 ml/strategy_registry.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
@dataclass
class StrategyScore:
    key: str
    book: str
    score: float = 0.0        # 0..1 conviction
    eligible: bool = False    # does this strategy consider it a buy candidate
    reason: str = ""
    stop: Optional[float] = None


class ScoringContext:
    """Lazily provides everything a strategy might need for one ticker."""

    def __init__(self, ticker: str, df: pd.DataFrame, as_of=None):
        self.ticker = ticker.upper()
        self.df = df
        self.as_of = as_of                 # a date -> point-in-time fundamentals (backtest)
        self._stage = None
        self._fund = None
        self.days_listed = len(df) if df is not None else 0

    def stage(self):
        if self._stage is None and self.df is not None:
            from ml.stage_analyzer import analyze_stage
            self._stage = analyze_stage(self.ticker, df=self.df, use_cache=False)
        return self._stage

    def fundamentals(self) -> dict:
        if self._fund is not None:
            return self._fund
        # Backtest: point-in-time P/E and P/B from SEC XBRL (no lookahead).
        if self.as_of is not None:
            try:
                from ml.fundamentals_pit import pit_pe_pb
                price = float(self.df["Close"].iloc[-1]) if self.df is not None else 0.0
                pe, pb, _ = pit_pe_pb(self.ticker, price, self.as_of)
                self._fund = {"pe": pe, "pb": pb, "peg": None,
                              "earnings_growth": None, "revenue_growth": None}
            except Exception:
                self._fund = {}
            return self._fund
        # Live: yfinance snapshot.
        try:
            import yfinance as yf
            info = yf.Ticker(self.ticker).info or {}
            self._fund = {
                "pe": info.get("trailingPE"), "pb": info.get("priceToBook"),
                "peg": info.get("pegRatio"),
                "earnings_growth": info.get("earningsGrowth"),
                "revenue_growth": info.get("revenueGrowth"),
            }
        except Exception:
            self._fund = {}
        return self._fund


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------
def _sma(s: pd.Series, n: int) -> Optional[float]:
    return float(s.tail(n).mean()) if len(s) >= n else None


def _rsi(s: pd.Series, n: int = 14) -> Optional[float]:
    if len(s) < n + 1:
        return None
    d = s.diff().dropna()
    up = d.clip(lower=0).tail(n).mean()
    dn = -d.clip(upper=0).tail(n).mean()
    if dn == 0:
        return 100.0
    rs = up / dn
    return float(100 - 100 / (1 + rs))


# ---------------------------------------------------------------------------
# Strategies — each returns StrategyScore
# ---------------------------------------------------------------------------
def _weinstein(ctx: ScoringContext) -> StrategyScore:
    r = ctx.stage()
    s = StrategyScore("weinstein", "weinstein")
    if r is None or r.error:
        return s
    if r.stage == 2 and not getattr(r, "is_invalidated", False):
        s.score = float(r.bull_probability)
        s.eligible = True
        s.stop = float(getattr(r, "ma_30w", 0.0) or 0.0)
        s.reason = f"Stage 2, bull {r.bull_probability:.2f}, RS {r.mansfield_rs:+.0f}"
    else:
        s.reason = f"Stage {r.stage} ({r.stage_name})"
    return s


def _sepa(ctx: ScoringContext) -> StrategyScore:
    """Minervini trend template (8 checks) -> fraction passed."""
    s = StrategyScore("sepa", "minervini")
    df = ctx.df
    if df is None or len(df) < 200:
        s.reason = "needs 200+ days"
        return s
    c = df["Close"]
    price = float(c.iloc[-1])
    sma50, sma150, sma200 = _sma(c, 50), _sma(c, 150), _sma(c, 200)
    sma200_prev = _sma(c.iloc[:-20], 200)
    hi52, lo52 = float(c.tail(252).max()), float(c.tail(252).min())
    checks = [
        price > sma150 and price > sma200,
        sma150 > sma200,
        sma200_prev is not None and sma200 > sma200_prev,
        sma50 > sma150 > sma200,
        price > sma50,
        price >= lo52 * 1.25,
        price >= hi52 * 0.75,
        price > (sma50 or price) and (sma50 or 0) > (sma150 or 0),
    ]
    passed = sum(1 for x in checks if x)
    s.score = passed / len(checks)
    s.eligible = s.score >= 0.75
    s.stop = sma50
    s.reason = f"trend template {passed}/8"
    return s


def _canslim(ctx: ScoringContext) -> StrategyScore:
    """O'Neil chartable signals: near high, RS, volume; + earnings growth if known."""
    s = StrategyScore("canslim", "oneil")
    df = ctx.df
    if df is None or len(df) < 60:
        return s
    c, v = df["Close"], df["Volume"]
    price = float(c.iloc[-1]); hi52 = float(c.tail(252).max())
    near_high = price >= hi52 * 0.90
    ret6m = float(c.iloc[-1] / c.iloc[-min(126, len(c))] - 1.0)
    vol_surge = float(v.iloc[-1] / (v.tail(50).mean() or 1))
    parts = [0.4 if near_high else 0.0,
             min(0.3, max(0.0, ret6m)) , 0.15 if vol_surge >= 1.3 else 0.0]
    fg = ctx.fundamentals().get("earnings_growth")
    if fg is not None:
        parts.append(0.15 if fg and fg > 0.20 else 0.0)
    s.score = round(min(1.0, sum(parts)), 3)
    s.eligible = near_high and ret6m > 0
    s.stop = _sma(c, 50)
    s.reason = f"near-high={near_high}, 6m={ret6m:+.0%}, vol={vol_surge:.1f}x"
    return s


def _technical(ctx: ScoringContext) -> StrategyScore:
    """Classical TA composite from the technical book: trend + RSI + MACD."""
    s = StrategyScore("technical", "technical")
    df = ctx.df
    if df is None or len(df) < 60:
        return s
    c = df["Close"]; price = float(c.iloc[-1])
    sma20, sma50 = _sma(c, 20), _sma(c, 50)
    ema12 = c.ewm(span=12).mean(); ema26 = c.ewm(span=26).mean()
    macd = float(ema12.iloc[-1] - ema26.iloc[-1])
    signal = float((ema12 - ema26).ewm(span=9).mean().iloc[-1])
    rsi = _rsi(c) or 50.0
    parts = [0.35 if price > sma50 else 0.0,
             0.20 if sma20 and price > sma20 else 0.0,
             0.25 if macd > signal else 0.0,
             0.20 if 45 <= rsi <= 70 else (0.10 if rsi < 45 else 0.0)]
    s.score = round(sum(parts), 3)
    s.eligible = price > sma50 and macd > signal
    s.stop = sma50
    s.reason = f"px>SMA50={price>sma50}, MACD{'>' if macd>signal else '<'}sig, RSI {rsi:.0f}"
    return s


def _swing(ctx: ScoringContext) -> StrategyScore:
    """Short-term swing: above short MAs with a recent higher-low pullback."""
    s = StrategyScore("swing", "swingtrade")
    df = ctx.df
    if df is None or len(df) < 25:
        return s
    c = df["Close"]; price = float(c.iloc[-1])
    sma10, sma20 = _sma(c, 10), _sma(c, 20)
    recent_low = float(c.tail(10).min())
    pulled_back = price <= float(c.tail(5).max()) * 0.99
    up = price > (sma10 or price) and (sma10 or 0) > (sma20 or 0)
    parts = [0.5 if up else 0.0, 0.3 if pulled_back and up else 0.0,
             0.2 if price > recent_low * 1.02 else 0.0]
    s.score = round(sum(parts), 3)
    s.eligible = up
    s.stop = recent_low
    s.reason = f"above 10/20 MA={up}, pullback={pulled_back}"
    return s


def _value(ctx: ScoringContext) -> StrategyScore:
    """Graham value from fundamentals: low P/E, low P/B, Graham number proxy."""
    s = StrategyScore("value", "graham")
    f = ctx.fundamentals()
    pe, pb = f.get("pe"), f.get("pb")
    if not pe or not pb or pe <= 0 or pb <= 0:
        s.reason = "no fundamentals"
        return s
    graham = pe * pb                          # Graham: P/E * P/B < 22.5 is value
    score = max(0.0, min(1.0, (22.5 - graham) / 22.5 + 0.5)) if graham < 45 else 0.0
    s.score = round(score, 3)
    s.eligible = graham <= 22.5
    if ctx.df is not None:
        s.stop = round(float(ctx.df["Close"].iloc[-1]) * 0.85, 2)
    s.reason = f"P/E {pe:.1f} * P/B {pb:.1f} = {graham:.1f} (value if <=22.5)"
    return s


def _growth(ctx: ScoringContext) -> StrategyScore:
    """Lynch GARP: PEG-driven, growth at a reasonable price."""
    s = StrategyScore("growth", "lynch")
    f = ctx.fundamentals()
    peg, eg = f.get("peg"), f.get("earnings_growth")
    if peg is None or peg <= 0:
        s.reason = "no PEG"
        return s
    score = max(0.0, min(1.0, (2.0 - peg) / 2.0))    # PEG<1 great, ~2 poor
    s.score = round(score, 3)
    s.eligible = peg <= 1.5 and (eg is None or eg > 0)
    if ctx.df is not None:
        s.stop = round(float(ctx.df["Close"].iloc[-1]) * 0.85, 2)
    s.reason = f"PEG {peg:.2f}" + (f", growth {eg:+.0%}" if eg is not None else "")
    return s


def _ipo(ctx: ScoringContext) -> StrategyScore:
    s = StrategyScore("ipo", "oneil/minervini")
    if ctx.days_listed >= 150:
        s.reason = "mature -> not an IPO play"
        return s
    from ml.new_listing_scanner import assess_new_listing
    r = assess_new_listing(ctx.ticker, df=ctx.df)
    s.score = r.score if r.setup == "BREAKOUT" else (0.4 if r.setup == "BASING" else 0.0)
    s.eligible = r.setup == "BREAKOUT"
    s.stop = r.suggested_stop
    s.reason = f"{r.setup}"
    return s


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
@dataclass
class StrategyDef:
    key: str
    book: str
    fn: Callable[[ScoringContext], StrategyScore]
    kind: str = "quant"       # quant | fundamental | hybrid


REGISTRY: dict[str, StrategyDef] = {
    "weinstein": StrategyDef("weinstein", "weinstein", _weinstein, "quant"),
    "sepa": StrategyDef("sepa", "minervini", _sepa, "quant"),
    "canslim": StrategyDef("canslim", "oneil", _canslim, "hybrid"),
    "technical": StrategyDef("technical", "technical", _technical, "quant"),
    "swing": StrategyDef("swing", "swingtrade", _swing, "quant"),
    "value": StrategyDef("value", "graham", _value, "fundamental"),
    "growth": StrategyDef("growth", "lynch", _growth, "fundamental"),
    "ipo": StrategyDef("ipo", "oneil/minervini", _ipo, "quant"),
}


def all_keys() -> list[str]:
    return list(REGISTRY.keys())


# ---------------------------------------------------------------------------
# Display names — author attribution for the UI.
#
# The Quant team asked what investment theory each strategy represents, so the
# Attribution rule (JP, 2026-07-27): an author's NAME appears only where the
# source book is physically in Finance Books/, so fidelity is verifiable
# against the text and the name never reads as endorsement. Strategies whose
# source texts are not on the shelf get descriptive labels instead. Weinstein
# and O'Neil are on the shelf; Minervini, Lynch, and Graham are not (their
# scorers are inspired-by implementations of published ideas).
# This is the single source of truth: UI surfaces import display_name() rather
# than hard-coding their own mapping.
# ---------------------------------------------------------------------------
DISPLAY_NAMES: dict[str, str] = {
    "weinstein": "Weinstein",
    "sepa": "Trend Momentum",
    "canslim": "O'Neil",
    "growth": "GARP Growth",
    "value": "Classic Value",
    "technical": "Technical",
    "swing": "Swing",
    "ipo": "New-Issue Momentum",
}


def display_name(key: str) -> str:
    """Author-facing label for a strategy key (e.g. 'sepa' -> 'Minervini').

    Falls back to a title-cased key so a newly added strategy never renders as
    a blank or raw lowercase token before its display name is registered.
    """
    return DISPLAY_NAMES.get(key, (key or "").replace("_", " ").title())


# ---------------------------------------------------------------------------
# Optional score-once cache.
#
# A StrategyScore is read-only once built, so the same (ticker, price snapshot,
# strategy) result can be reused across several portfolio builds in one run.
# The Compare view builds four mandates over the SAME pool, so memoizing here
# turns four full scoring passes (including the per-name yfinance fundamentals
# and the stage analyzer) into one. Disabled by default — a caller opts in with
# enable_score_cache() and MUST clear_score_cache() afterwards so a later run on
# fresh prices never reuses a stale score.
# ---------------------------------------------------------------------------
_SCORE_CACHE: Optional[dict] = None


def enable_score_cache() -> None:
    global _SCORE_CACHE
    _SCORE_CACHE = {}


def clear_score_cache() -> None:
    global _SCORE_CACHE
    _SCORE_CACHE = None


def _df_fingerprint(df) -> str:
    """Cheap, stable signature of a price frame: length + last bar + last close.

    Two contexts with the same fingerprint are the same data for scoring
    purposes, so their per-strategy scores are interchangeable within one run.
    """
    if df is None or len(df) == 0:
        return "none"
    try:
        last_close = float(df["Close"].iloc[-1])
        last_ts = getattr(df.index[-1], "value", df.index[-1])
        return f"{len(df)}_{last_ts}_{last_close:.4f}"
    except Exception:
        return f"{len(df) if df is not None else 0}"


def score_all(ctx: ScoringContext, only: Optional[list[str]] = None) -> dict[str, StrategyScore]:
    out: dict[str, StrategyScore] = {}
    cache = _SCORE_CACHE
    fp = _df_fingerprint(ctx.df) if cache is not None else None
    for key, sd in REGISTRY.items():
        if only and key not in only:
            continue
        ck = (ctx.ticker, fp, ctx.as_of, key) if cache is not None else None
        if ck is not None:
            hit = cache.get(ck)
            if hit is not None:
                out[key] = hit
                continue
        try:
            res = sd.fn(ctx)
        except Exception as e:  # noqa: BLE001
            logger.warning("strategy %s failed for %s: %s", key, ctx.ticker, e)
            res = StrategyScore(key, sd.book, reason=f"error: {e}")
        out[key] = res
        if ck is not None:
            cache[ck] = res
    return out


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from data import price_cache
    sample = ["NVDA", "AAPL", "KO", "O"]
    price_cache.refresh_prices(sample)
    for t in sample:
        df = price_cache.get_history(t)
        if df is None:
            print(f"{t}: no data"); continue
        ctx = ScoringContext(t, df)
        scores = score_all(ctx)
        print(f"\n{t}: every book scored ->")
        for k, sc in scores.items():
            mark = "✓" if sc.eligible else " "
            print(f"  [{mark}] {k:10}({sc.book:16}) {sc.score:.2f}  {sc.reason}")


if __name__ == "__main__":
    _selftest()
