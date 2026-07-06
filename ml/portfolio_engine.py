"""
portfolio_engine.py - Integrated, regime-aware portfolio construction (Phase 5 spine).

This is the integration layer the project is really about. It does NOT hand the
user separate lists to reconcile. It takes the universe, scores every name across
all available strategies on one common 0..1 scale, lets the market regime decide
how much to trust each strategy and how much capital to deploy, then selects and
sizes a single concentrated portfolio with stops, sector caps, and a cash sleeve.

Strategies blended (via rag.strategy_router):
  * weinstein  - stage + gates, from ml.universe_scanner (cached Weinstein scan)
  * momentum   - relative strength + volume (O'Neil / Minervini quant proxy)
  * ipo        - ml.new_listing_scanner, for names too young for a 30-week MA
  * value      - Graham principles scored from fundamentals (P/E, P/B)
  * growth     - Lynch / O'Neil principles scored from fundamentals (PEG, growth)

All strategies encode the published frameworks in code, so they run without any book
PDFs present. The Finance Books are an optional RAG reference layer for grounded
memos, not a dependency of the engine.

    PYTHONPATH=. python3 ml/portfolio_engine.py
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from data import price_cache
from ml.new_listing_scanner import assess_new_listing
from ml.news_signals import news_signal, vix_read
from ml.stage_analyzer import analyze_stage
from ml.universe_scanner import scan_cached
from rag.strategy_router import route

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Portfolio parameters (concentrated default per the mandate decision)
# ---------------------------------------------------------------------------
@dataclass
class PortfolioConfig:
    account_size: float = 100_000.0
    max_positions: int = 12          # concentrated 10-15
    risk_per_trade: float = 0.0125   # 1.25% of account risked to the stop
    max_position_pct: float = 0.12   # single-name cap
    max_per_sector: int = 3
    mandate: str = "swing_momentum"
    # News / catalyst layer
    use_av_news: bool = False        # Alpha Vantage (25/day) -> enable for finalists only
    use_social: bool = True          # StockTwits attention/froth (free)
    news_weight: float = 0.15        # how much news sentiment nudges the quant score
    use_memory: bool = True          # tilt strategy weights by what worked in similar past states
    min_price: float = 5.0           # liquidity gate: drop sub-$5 / penny names
    min_dollar_vol: float = 25_000_000.0  # min 20d avg dollar volume (real, liquid names)
    # Account-level risk layer (JP, 2026-07-06): the risk WALLET and the
    # cold-streak THROTTLE. Heat = total dollars lost if every stop hit at
    # once; capped as a hard build constraint. The throttle scales risk per
    # trade by the mandate's own trailing expectancy (graduated steps).
    max_heat: float = 0.06           # risk wallet: max total open risk, 6% of account
    use_throttle: bool = True        # graduated cold-streak throttle on/off
    # Manual override: tickers the user pins are ALWAYS placed in the book,
    # bypassing the liquidity / eligibility / composite gates, with a
    # protective fallback stop if no strategy supplies one. Flagged manual.
    force_include: list[str] = field(default_factory=list)


@dataclass
class Holding:
    ticker: str
    sector: str
    composite: float
    strategy_scores: dict[str, float]
    entry: float
    stop: float
    shares: int
    dollars: float
    weight_pct: float
    risk_dollars: float
    rationale: str
    sentiment: float = 0.0
    earnings_risk: bool = False
    froth: bool = False
    news_summary: str = ""
    manual: bool = False             # user-pinned (forced into the book)


@dataclass
class PortfolioPlan:
    regime: str
    mandate: str
    weights: dict[str, float]
    max_invested: float
    active_strategies: list[str]
    pending_strategies: list[str]
    holdings: list[Holding] = field(default_factory=list)
    invested: float = 0.0
    cash: float = 0.0
    note: str = ""


# ---------------------------------------------------------------------------
# Market regime (reuse our own engine: SPY stage -> regime)
# ---------------------------------------------------------------------------
def detect_regime() -> str:
    """Regime = SPY stage, then a VIX override. (market_context adds breadth in UI.)"""
    df = price_cache.get_history("SPY")
    if df is None:
        price_cache.refresh_prices(["SPY"])
        df = price_cache.get_history("SPY")
    base = "neutral"
    if df is not None:
        r = analyze_stage("SPY", df=df, use_cache=False)
        base = {2: "bull", 4: "bear"}.get(r.stage, "neutral")
    # VIX override: fear downgrades risk appetite one notch.
    vix = vix_read()
    if vix.get("hint") == "risk_off":
        base = {"bull": "neutral", "neutral": "bear"}.get(base, base)
    return base


def _current_market_state(symbols: list[str]) -> dict:
    """Live market-state vector (matches ml.market_memory STATE_COLS)."""
    vols, moms = [], []
    for s in symbols[:300]:
        df = price_cache.get_history(s)
        if df is None or len(df) < 252:
            continue
        c = df["Close"]
        vols.append(float(c.pct_change().tail(60).std()))
        moms.append(1 if float(c.iloc[-21] / c.iloc[-252] - 1) > 0 else 0)
    state = {"avg_vol": float(np.mean(vols)) if vols else 0.02,
             "breadth": float(np.mean(moms)) if moms else 0.5,
             "yield_slope": 0.5, "credit_spread": 3.0}
    try:
        from ml.meta_features import fred_series
        d10, d2, hy = fred_series("DGS10"), fred_series("DGS2"), fred_series("BAMLH0A0HYM2")
        state["yield_slope"] = float(d10.iloc[-1]) - float(d2.iloc[-1])
        state["credit_spread"] = float(hy.iloc[-1])
    except Exception:
        pass
    return state


def _sector(ticker: str) -> str:
    try:
        from data.tickers import get_ticker
        m = get_ticker(ticker)
        if m is not None:
            return getattr(m, "category", "UNKNOWN") or "UNKNOWN"
    except Exception:
        pass
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Per-strategy scores (0..1)
# ---------------------------------------------------------------------------
def _weinstein_score(r) -> float:
    if r.error or r.stage != 2 or getattr(r, "is_invalidated", False):
        return 0.0
    s = float(r.bull_probability)
    if getattr(r, "weinstein_buy_confirmed", False):
        s = min(1.0, s + 0.05)
    return s


def _momentum_score(r) -> float:
    if r.error:
        return 0.0
    s = max(0.0, min(1.0, float(r.mansfield_rs) / 30.0))   # RS%>=30 -> full
    if getattr(r, "volume_confirmed", False):
        s = min(1.0, s + 0.10)
    if r.stage not in (1, 2):
        s *= 0.3
    return s


# ---------------------------------------------------------------------------
# Entry-quality RISK gates (Item 2 — the ATEX/NVDA lessons from the honest
# ruler). These are risk gates, not style gates: they apply to every book.
#
#   NVDA lesson: a stop 0.7% below entry is inside daily noise — a guaranteed
#   whipsaw. Stops are widened to a volatility floor (the position is then
#   risk-sized on the wider stop, so dollars at risk stay constant).
#   ATEX/BE lesson: when the nearest valid structure is >15% below entry,
#   the name has run too far from support — that is a chase, and the honest
#   ruler showed those entries getting stopped for -15% to -23%. Rejected,
#   not clamped: an artificial stop with no structure under it is how the
#   old book bled.
# ---------------------------------------------------------------------------
STOP_MIN_PCT = 0.03          # noise floor: stop at least 3% away
STOP_MAX_PCT = 0.15          # structure ceiling: stop further than 15% = chase
EXTENSION_MAX = 0.25         # >25% above the ~30-week MA = extended, reject
_ATR_MULT_FLOOR = 1.5        # ... and at least 1.5x ATR(20) away


def _atr20_pct(df) -> float:
    """ATR(20) as a fraction of the last close (true range when OHLC exists,
    close-to-close otherwise). Cheap, cached-data only."""
    try:
        c = df["Close"].astype(float)
        if {"High", "Low"}.issubset(df.columns):
            h, low = df["High"].astype(float), df["Low"].astype(float)
            pc = c.shift(1)
            tr = (h - low).combine(abs(h - pc), max).combine(abs(low - pc), max)
            atr = float(tr.tail(20).mean())
        else:
            atr = float(c.diff().abs().tail(20).mean())
        last = float(c.iloc[-1])
        return atr / last if last > 0 else 0.02
    except Exception:
        return 0.02


def drawdown_throttle(mandate: str) -> tuple[float, str]:
    """The cold-streak throttle (graduated). Scales risk per trade by the
    mandate's own trailing expectancy on the honest scoreboard:

        expectancy > +0.5%   -> 1.00 (winning: full size)
        -0.5% .. +0.5%       -> 0.75 (flat: three-quarter size)
        -3.0% .. -0.5%       -> 0.50 (losing: half size)
        below -3.0%          -> 0.25 (severe streak: quarter size)

    Full size must be earned back with wins. No throttle until at least 10
    positions have been graded — a thin record is noise, not evidence."""
    try:
        from ml.portfolio_journal import compare_by_mandate
        v = compare_by_mandate().get(mandate) or {}
    except Exception:
        return 1.0, ""
    n = int(v.get("positions_scored") or 0)
    exp = v.get("expectancy_pct")
    if n < 10 or exp is None:
        return 1.0, ""
    if exp > 0.5:
        return 1.0, f"throttle: full size (expectancy {exp:+.2f}% over {n} positions)"
    if exp >= -0.5:
        return 0.75, f"throttle: 3/4 size (flat expectancy {exp:+.2f}% over {n} positions)"
    if exp >= -3.0:
        return 0.50, f"throttle: HALF size (negative expectancy {exp:+.2f}% over {n} positions)"
    return 0.25, f"throttle: QUARTER size (severe streak, expectancy {exp:+.2f}% over {n} positions)"


def entry_risk_gate(df, entry: float, stop: float):
    """Apply the risk gates. Returns (adjusted_stop, note), or (None, reason)
    when the name should be rejected outright."""
    # 1) Extension: too far above the ~30-week MA is a chase, whatever the book.
    if df is not None and len(df) >= 150:
        ma150 = float(df["Close"].tail(150).mean())
        if ma150 > 0:
            ext = entry / ma150 - 1.0
            if ext > EXTENSION_MAX:
                return None, f"extended {ext:+.0%} above 30w MA (max {EXTENSION_MAX:.0%})"
    # 2) Noise floor: widen stops tighter than volatility can respect.
    note = ""
    floor_frac = max(STOP_MIN_PCT, _ATR_MULT_FLOOR * _atr20_pct(df))
    dist_frac = (entry - stop) / entry if entry > 0 else 0.0
    if dist_frac < floor_frac:
        stop = round(entry * (1.0 - floor_frac), 2)
        note = f"stop widened to {floor_frac:.1%} (noise floor)"
        dist_frac = floor_frac
    # 3) Structure ceiling: support too far below means the entry is late.
    if dist_frac > STOP_MAX_PCT:
        return None, f"nearest support {dist_frac:.0%} below entry (max {STOP_MAX_PCT:.0%})"
    return stop, note


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def _pinned_holding(sym: str, weights: dict, cfg: PortfolioConfig,
                    regime: str, mandate: str, cikmap: dict) -> Optional[Holding]:
    """Build a Holding for a user-pinned ticker, bypassing the funnel gates.

    Scores the name best-effort across all strategies (for display), takes the
    last close as entry, and uses the best eligible strategy's stop or an 8%
    protective fallback so the position can still be risk-sized. Returns None
    only when there is no usable price at all.
    """
    from ml.strategy_registry import ScoringContext, score_all

    sym = sym.upper()
    df = price_cache.get_history(sym)
    if df is None or len(df) < 20:
        try:
            price_cache.refresh_prices([sym])
            df = price_cache.get_history(sym)
        except Exception:
            df = None
    if df is None or len(df) < 20:
        return None
    entry = float(df["Close"].iloc[-1])
    if entry <= 0:
        return None

    scores: dict[str, float] = {}
    stop = 0.0
    try:
        sc = score_all(ScoringContext(sym, df))
        scores = {k: round(float(sc[k].score), 3) for k in sc}
        elig = [sc[k] for k in sc if getattr(sc[k], "eligible", False) and sc[k].stop]
        if elig:
            stop = float(max(elig, key=lambda x: x.score).stop)
    except Exception:
        pass
    if stop <= 0 or stop >= entry:
        stop = round(entry * 0.92, 2)            # 8% protective fallback
    # Pins bypass selection gates by design, but stop SANITY still applies:
    # clamp the distance into the [noise floor, structure ceiling] band.
    floor_frac = max(STOP_MIN_PCT, _ATR_MULT_FLOOR * _atr20_pct(df))
    dist = (entry - stop) / entry
    if dist < floor_frac:
        stop = round(entry * (1.0 - floor_frac), 2)
    elif dist > STOP_MAX_PCT:
        stop = round(entry * (1.0 - STOP_MAX_PCT), 2)

    wsum = sum(weights.values()) or 1.0
    composite = round(sum(weights.get(k, 0.0) * scores.get(k, 0.0) for k in scores) / wsum, 4)

    sector = _sector(sym)
    try:
        from data.sectors import get_sector
        sector = get_sector(sym, cikmap.get(sym)) or sector
    except Exception:
        pass

    risk_dollars = cfg.account_size * cfg.risk_per_trade
    max_pos_dollars = cfg.account_size * cfg.max_position_pct
    per_share_risk = entry - stop
    shares = int(risk_dollars / per_share_risk) if per_share_risk > 0 else 0
    dollars = shares * entry
    if dollars > max_pos_dollars:
        shares = int(max_pos_dollars / entry)
        dollars = shares * entry

    note = "📌 manual pin"
    if not scores:
        note += " (no strategy score available)"
    return Holding(
        ticker=sym, sector=sector, composite=composite,
        strategy_scores=scores, entry=round(entry, 2), stop=round(stop, 2),
        shares=shares, dollars=round(dollars, 2),
        weight_pct=round(100 * dollars / cfg.account_size, 2),
        risk_dollars=round(shares * per_share_risk, 2),
        rationale=(f"{note} — forced into the {mandate}/{regime} book by user; "
                   f"entry {entry:.2f}, stop {stop:.2f}, "
                   f"risk ${shares * per_share_risk:,.0f}"),
        manual=True,
    )


def build_portfolio(symbols: list[str], config: Optional[PortfolioConfig] = None,
                    regime: Optional[str] = None,
                    weights: Optional[dict] = None) -> PortfolioPlan:
    from ml.strategy_registry import ScoringContext, score_all

    cfg = config or PortfolioConfig()
    if regime is None:
        regime = detect_regime()
    plan = route(cfg.mandate, regime)

    # Weights: if the orchestrator supplied them (already memory-tilted), use those;
    # otherwise compute here from the router + memory.
    memory_note = ""
    if weights is None:
        weights = dict(plan.weights)                 # per-strategy keys, regime-tilted
        if cfg.use_memory:
            try:
                from ml.market_memory import memory_tilt
                state = _current_market_state(symbols)
                tilt = memory_tilt(state)
                weights = {k: weights.get(k, 0.0) * tilt.get(k, 1.0) for k in weights}
                wsum = sum(weights.values()) or 1.0
                weights = {k: v / wsum for k, v in weights.items()}
                top = sorted(tilt.items(), key=lambda x: -x[1])[:2]
                memory_note = ("memory tilt applied (boosted "
                               + ", ".join(f"{k} x{v:.2f}" for k, v in top) + "); ")
            except Exception:
                memory_note = ""

    # Cheap price strategies run over everything; fundamentals (value/growth) call
    # yfinance per name, so they run only on the shortlist (the funnel).
    PRICE_KEYS = ["weinstein", "sepa", "canslim", "technical", "swing", "ipo"]
    FUND_KEYS = ["value", "growth"]

    def _blend(scores: dict, keys: list[str]) -> float:
        wsum = sum(weights.get(k, 0.0) for k in keys) or 1.0
        return sum(weights.get(k, 0.0) * float(scores.get(k, 0.0)) for k in keys) / wsum

    BENCHMARKS = {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI"}
    candidates: list[Holding] = []
    gate_rejects, stops_widened = 0, 0
    for sym in symbols:
        if sym in BENCHMARKS:
            continue
        df = price_cache.get_history(sym)
        if df is None or len(df) < 20:
            continue
        # Liquidity gate: skip penny / illiquid names (not investable, noisy, and
        # they crowd out real picks). Standard quant screen.
        c, v = df["Close"], df["Volume"]
        if float(c.iloc[-1]) < cfg.min_price or float((c.tail(20) * v.tail(20)).mean()) < cfg.min_dollar_vol:
            continue
        ctx = ScoringContext(sym, df)
        sc = score_all(ctx, only=PRICE_KEYS)        # pass 1: price strategies
        eligible_keys = [k for k in PRICE_KEYS if sc[k].eligible]
        if not eligible_keys:
            continue                                # no strategy considers it a buy
        scores = {k: sc[k].score for k in PRICE_KEYS}
        composite = _blend(scores, PRICE_KEYS)
        if composite <= 0:
            continue
        entry = float(df["Close"].iloc[-1])
        best = max((sc[k] for k in eligible_keys), key=lambda x: x.score)
        stop = float(best.stop) if best.stop else 0.0
        if entry <= 0 or stop <= 0 or stop >= entry:
            continue
        # Item 2 risk gates: reject extended chases and structureless stops,
        # widen noise-tight stops (the NVDA 0.7% / ATEX +37% lessons).
        stop, _gate_note = entry_risk_gate(df, entry, stop)
        if stop is None:
            gate_rejects += 1
            continue
        if _gate_note:
            stops_widened += 1
        candidates.append(Holding(
            ticker=sym, sector=_sector(sym), composite=round(composite, 4),
            strategy_scores={k: round(scores[k], 3) for k in PRICE_KEYS},
            entry=round(entry, 2), stop=round(stop, 2), shares=0, dollars=0.0,
            weight_pct=0.0, risk_dollars=0.0, rationale="",
        ))

    # News/catalyst enrichment on the top candidates only (funnel: keeps the
    # Alpha Vantage 25/day limit safe). Sentiment tilts the quant score, hard
    # red flags (bankruptcy/delisting/auditor change) drop the name.
    candidates.sort(key=lambda h: h.composite, reverse=True)
    try:
        from data.universe import fetch_sec_cik_map
        cikmap = fetch_sec_cik_map()
    except Exception:
        cikmap = {}
    # Enrich more than we need so selection can backfill past names that get
    # dropped for red flags, sector caps, or zero-share sizing and still reach
    # max_positions. (News here is free StockTwits social; AV stays off.)
    shortlist = candidates[: max(cfg.max_positions * 3, 60)]
    enriched: list[Holding] = []
    for h in shortlist:
        # Real sector from SEC SIC (fixes UNKNOWN; enables the diversification cap).
        try:
            from data.sectors import get_sector
            h.sector = get_sector(h.ticker, cikmap.get(h.ticker)) or h.sector
        except Exception:
            pass
        # Pass 2: add the fundamentals strategies (Graham value, Lynch growth) on
        # the shortlist only, then re-blend over ALL strategies present.
        df = price_cache.get_history(h.ticker)
        if df is not None:
            fsc = score_all(ScoringContext(h.ticker, df), only=FUND_KEYS)
            for k in FUND_KEYS:
                h.strategy_scores[k] = round(fsc[k].score, 3)
        h.composite = round(_blend(h.strategy_scores, list(h.strategy_scores.keys())), 4)

        sig = news_signal(h.ticker, cik=cikmap.get(h.ticker),
                          use_av=cfg.use_av_news, use_social=cfg.use_social)
        if sig.red_flags:                              # hard exclude
            continue
        h.sentiment = sig.sentiment_score
        h.earnings_risk = sig.earnings_risk
        h.froth = sig.froth
        h.news_summary = sig.summary
        tilt = 1.0 + cfg.news_weight * sig.sentiment_score
        if sig.froth:
            tilt -= 0.05                               # fade one-sided euphoria
        h.composite = round(max(0.0, h.composite * tilt), 4)
        enriched.append(h)

    # Select + size in ONE resilient pass. Rank by news-adjusted conviction, then
    # walk the list: size each name and only KEEP it if it earns >= 1 share and
    # fits the sector cap. Names whose stop is too far for the risk budget round
    # to zero shares and are SKIPPED (the slot goes to the next candidate), so the
    # book reaches max_positions instead of stalling below it. This replaces the
    # old "select 25, then size, then delete the zero-share ones with no backfill"
    # flow that left holdings short whenever the risk budget was small.
    enriched.sort(key=lambda h: h.composite, reverse=True)
    # Cold-streak throttle: the system responds to its own losing record the
    # way its stops respond to a single stock's.
    throttle, throttle_note = (drawdown_throttle(cfg.mandate)
                               if cfg.use_throttle else (1.0, ""))
    risk_dollars = cfg.account_size * cfg.risk_per_trade * throttle
    max_pos_dollars = cfg.account_size * cfg.max_position_pct

    def _size(h: "Holding") -> int:
        per_share_risk = h.entry - h.stop
        if per_share_risk <= 0:
            h.shares, h.dollars, h.risk_dollars = 0, 0.0, 0.0
            return 0
        eff_risk = risk_dollars * (0.5 if h.earnings_risk else 1.0)
        shares = int(eff_risk / per_share_risk)
        if shares * h.entry > max_pos_dollars:           # single-name size cap
            shares = int(max_pos_dollars / h.entry)
        h.shares = shares
        h.dollars = round(shares * h.entry, 2)
        h.risk_dollars = round(shares * per_share_risk, 2)
        return shares

    # Earnings blackout (project rule: "avoid buying when earnings or major
    # news risk is imminent"). Hard skip for short-horizon mandates, where one
    # print dominates the whole holding period; longer books keep the existing
    # half-size treatment instead.
    _EARNINGS_BLACKOUT_MANDATES = {"fast_momentum", "swing_momentum"}
    selected: list[Holding] = []
    sector_count: dict[str, int] = {}
    heat_budget = cfg.max_heat * cfg.account_size
    heat_used = 0.0
    for h in enriched:
        if len(selected) >= cfg.max_positions:
            break
        if h.earnings_risk and cfg.mandate in _EARNINGS_BLACKOUT_MANDATES:
            continue
        if h.sector not in ("UNKNOWN", "Other") and sector_count.get(h.sector, 0) >= cfg.max_per_sector:
            continue
        if _size(h) < 1:                                 # stop too far for budget — skip, don't waste a slot
            continue
        # The risk WALLET (hard heat cap): total worst-case loss if every stop
        # hit at once may never exceed max_heat of the account. When the
        # wallet can only partly afford this name, shrink it to fit; when it
        # can't afford a single share, the wallet is spent — stop adding,
        # whatever the next signal looks like.
        if heat_used + h.risk_dollars > heat_budget:
            per_share_risk = h.entry - h.stop
            fit = int((heat_budget - heat_used) / per_share_risk) if per_share_risk > 0 else 0
            if fit < 1:
                break
            h.shares = min(h.shares, fit)
            h.dollars = round(h.shares * h.entry, 2)
            h.risk_dollars = round(h.shares * per_share_risk, 2)
        heat_used += h.risk_dollars
        selected.append(h)
        sector_count[h.sector] = sector_count.get(h.sector, 0) + 1

    # Enforce the regime's max-invested cap. Scale positions down proportionally
    # but keep at least 1 share so the cap never silently deletes holdings.
    invested = sum(h.dollars for h in selected)
    cap = plan.max_invested * cfg.account_size
    if invested > cap and invested > 0:
        scale = cap / invested
        for h in selected:
            h.shares = max(1, int(h.shares * scale))
            h.dollars = round(h.shares * h.entry, 2)
            h.risk_dollars = round(h.shares * (h.entry - h.stop), 2)
        invested = sum(h.dollars for h in selected)

    # Final guard: only real positions (sizing above already keeps shares >= 1).
    selected = [h for h in selected if h.shares > 0]
    invested = sum(h.dollars for h in selected)

    for h in selected:
        h.weight_pct = round(100 * h.dollars / cfg.account_size, 2)
        top = max(h.strategy_scores, key=h.strategy_scores.get) if h.strategy_scores else "n/a"
        flags = []
        if h.earnings_risk:
            flags.append("earnings-risk: half size")
        if h.froth:
            flags.append("social-froth")
        h.rationale = (f"{plan.mandate}/{regime}: led by {top} "
                       f"({h.strategy_scores.get(top, 0):.2f}); entry {h.entry}, stop {h.stop}, "
                       f"risk ${h.risk_dollars:,.0f}"
                       + (f"; {h.news_summary}" if h.news_summary else "")
                       + (f"; {', '.join(flags)}" if flags else ""))

    # --- Manual pins: force user-specified tickers into the book ----------
    # Pins bypass the funnel gates and lead the book. A pinned name already
    # selected organically is simply flagged. Pins are honored even past the
    # regime deploy cap (a deliberate user override), so invested is recomputed.
    pinned = list(dict.fromkeys(s.upper() for s in (cfg.force_include or [])))
    if pinned:
        have = {h.ticker for h in selected}
        for sym in pinned:
            if sym in have:
                for h in selected:
                    if h.ticker == sym:
                        h.manual = True
                        if "📌" not in h.rationale:
                            h.rationale = "📌 manual pin — " + h.rationale
                continue
            ph = _pinned_holding(sym, weights, cfg, regime, plan.mandate, cikmap)
            if ph is not None:
                selected.insert(0, ph)
        for h in selected:
            h.weight_pct = round(100 * h.dollars / cfg.account_size, 2)
        invested = sum(h.dollars for h in selected)

    active = [k for k, v in weights.items() if v > 0]
    return PortfolioPlan(
        regime=regime, mandate=plan.mandate,
        weights={k: round(v, 3) for k, v in weights.items()},
        max_invested=plan.max_invested,
        active_strategies=active, pending_strategies=[],
        holdings=selected, invested=round(invested, 2),
        cash=round(cfg.account_size - invested, 2),
        note=(memory_note
              + (f"Risk gates: {gate_rejects} chase(s) rejected, "
                 f"{stops_widened} noise stop(s) widened. " if (gate_rejects or stops_widened) else "")
              + f"Risk wallet: {sum(h.risk_dollars for h in selected) / max(cfg.account_size, 1.0):.1%} "
                f"heat used of {cfg.max_heat:.0%} cap. "
              + (throttle_note + ". " if throttle_note else "")
              + "All 8 strategies live and blended per regime "
              "(value/growth from fundamentals). Weinstein is one peer, not the base."),
    )


def format_plan(p: PortfolioPlan, account: float) -> str:
    lines = [
        f"Regime: {p.regime.upper()}   Mandate: {p.mandate}   Deploy cap: {p.max_invested:.0%}",
        f"Active strategies: {p.active_strategies}  (pending: {p.pending_strategies})",
        f"Blend weights: {p.weights}",
        f"\n{'Ticker':7}{'Sector':16}{'Comp':>6}{'Shares':>8}{'$Pos':>11}{'Wt%':>7}{'Entry':>9}{'Stop':>9}",
        "-" * 80,
    ]
    for h in p.holdings:
        lines.append(f"{h.ticker:7}{h.sector[:15]:16}{h.composite:6.2f}{h.shares:8d}"
                     f"{h.dollars:11,.0f}{h.weight_pct:7.1f}{h.entry:9.2f}{h.stop:9.2f}")
    lines.append("-" * 80)
    lines.append(f"Holdings: {len(p.holdings)}   Invested: ${p.invested:,.0f} "
                 f"({p.invested/account:.0%})   Cash: ${p.cash:,.0f} ({p.cash/account:.0%})")
    lines.append(p.note)
    return "\n".join(lines)


def _selftest() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sample = [
        "AAPL", "NVDA", "MSFT", "PLTR", "O", "PWR", "EME", "FSLR", "ENPH",
        "JPM", "XOM", "LLY", "TSLA", "AMD", "SMCI", "ANET", "VRT", "COST",
        "WMT", "PG", "KO", "T", "PFE", "DIS", "SPCX", "SPY",
    ]
    price_cache.refresh_prices(sample)
    cfg = PortfolioConfig()
    plan = build_portfolio(sample, cfg)
    print(format_plan(plan, cfg.account_size))


if __name__ == "__main__":
    _selftest()
