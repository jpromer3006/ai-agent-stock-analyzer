"""
multi_strategy_mode.py - Side-by-side multi-strategy portfolio comparison.

Pick any combination of the four mandates (Fast Momentum, Swing Momentum,
Growth Hold, Value Hold), choose a position count N, and build all of them over
the SAME locked candidate pool. Each book is sized against its own capital
(equal split by default for honest research). The view shows each book side by
side with an honest "X of N filled" count, a consensus panel ranking names by
how many books surfaced them, and a one-click handoff that sends the deduped
union into Trader Mode as the scan universe.

Why same pool: feeding every mandate the identical candidate list is a
correctness requirement. If the inputs differ, a name dropping out of one book
is ambiguous (strategy weighting vs never a candidate). One locked pool makes
the diff apples to apples.

Additive component: does not touch Portfolio, Trader, or Research internals. It
reuses build_portfolio (per mandate), route_to_trader (handoff), record_proposal
(journal), and collect_snapshot (training warehouse).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import pandas as pd
import streamlit as st

from data import price_cache
from data.watchlists import DEFAULT_NAME, list_watchlists, load_watchlist, save_watchlist
from ml.portfolio_engine import PortfolioConfig, build_portfolio
from rag.strategy_router import ARCHETYPES
from ui.components.disclaimer import render_disclaimer
from ui.components.portfolio_mode import _candidate_symbols
from ui.components.trader_mode import route_to_trader

# Order is fixed so the columns are stable across reruns.
_MANDATES = ["fast_momentum", "swing_momentum", "growth_hold", "value_hold"]
_DISPLAY = {
    "fast_momentum": "⚡ Fast Momentum",
    "swing_momentum": "🌊 Swing Momentum",
    "growth_hold": "🌱 Growth Hold",
    "value_hold": "🏛 Value Hold",
}
_REGIME_COLOR = {"bull": "#1a7f37", "neutral": "#9a6700", "bear": "#cf222e"}
_UNIVERSE_NAME = "Multi-Strategy Universe"


def _logic_summary(mandate: str) -> str:
    """Plain-English description of what drives a mandate's selection."""
    a = ARCHETYPES[mandate]
    top = sorted(a.weights.items(), key=lambda kv: -kv[1])[:3]
    top_str = ", ".join(f"{k} {v:.0%}" for k, v in top)
    return (
        f"**Horizon:** {a.horizon}\n\n"
        f"**Books in scope:** {', '.join(a.books)}\n\n"
        f"**Concept focus:** {', '.join(a.concepts)}\n\n"
        f"**Heaviest strategies (before regime tilt):** {top_str}\n\n"
        f"The market regime then tilts these weights up or down, so the same "
        f"book leans more defensive in a bear tape and more aggressive in a bull."
    )


def _book_table(plan) -> pd.DataFrame:
    return pd.DataFrame([{
        "Ticker": h.ticker,
        "Conv": round(h.composite, 2),
        "$ Pos": round(h.dollars),
        "Wt%": h.weight_pct,
        "Led by": (max(h.strategy_scores, key=h.strategy_scores.get)
                   if h.strategy_scores else "n/a"),
        "Flag": "📌" if getattr(h, "manual", False) else (
            "⚠️" if h.earnings_risk else ""),
    } for h in plan.holdings])


def render_multi_strategy_mode() -> None:
    st.markdown(
        "<div style='background:#0d1117;padding:14px 20px;border-radius:8px;"
        "margin-bottom:12px'><span style='color:#e6edf3;font-size:22px;"
        "font-weight:700'>⚖️ Compare Strategies</span><span style='color:#8b949e;"
        "margin-left:12px;font-size:13px'>Build several mandates over one locked "
        "pool, then send the union to Trader</span></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "Check the books you want, pick a position count, and build them all over "
        "the SAME candidate pool so the differences are real. Each book is sized "
        "against its own capital. Educational, not financial advice."
    )

    # ---- Sidebar: shared run controls ----
    st.sidebar.markdown("### ⚖️ Comparison settings")
    watchlists = list_watchlists() or [DEFAULT_NAME]
    source = st.sidebar.selectbox(
        "🔭 Scan pool (locked for all books)",
        ["Dynamic universe (liquid subset)"] + watchlists, index=0,
        help="Every checked book scores this exact same pool, so a name dropping "
             "from one book is a strategy decision, not a missing candidate.",
    )
    limit = st.sidebar.slider("Universe cap (dynamic only)", 100, 1500, 300, 50)
    n_positions = st.sidebar.slider("Positions per book (N)", 5, 30, 15)
    risk_pct = st.sidebar.slider("Risk per trade (%)", 0.25, 3.0, 1.25, 0.25) / 100.0
    min_liq_m = st.sidebar.slider("Min liquidity ($M daily volume)", 5, 200, 25, 5)
    total_capital = st.sidebar.number_input(
        "Total research capital ($)", 1_000, 100_000_000, 100_000, 1_000)
    equal_split = st.sidebar.checkbox(
        "Equal split across books (research default)", value=True,
        help="On: each checked book gets total / number of books. Off: set each "
             "book's dollar allocation yourself (e.g. 10k fast, 20k swing, "
             "50k growth, 5k value).")
    use_social = st.sidebar.checkbox("Social attention (StockTwits, free)", value=True)
    use_av = st.sidebar.checkbox(
        "Alpha Vantage news (25/day quota)", value=False,
        help="Off by default here: four books multiply the daily quota fast.")

    # ---- Main: per-book cards (include + capital + pins + logic) ----
    st.markdown("#### Books")
    cols = st.columns(4)
    include: dict[str, bool] = {}
    capital: dict[str, float] = {}
    pins: dict[str, list[str]] = {}
    for col, m in zip(cols, _MANDATES):
        with col:
            include[m] = st.checkbox(_DISPLAY[m], value=True, key=f"ms_inc_{m}")
            cap_default = float(total_capital) / 4.0
            capital[m] = st.number_input(
                "Capital $", min_value=0.0, value=round(cap_default, 0), step=1000.0,
                key=f"ms_cap_{m}", disabled=equal_split,
                label_visibility="collapsed",
            )
            pin_raw = st.text_input(
                "📌 pins", value="", key=f"ms_pin_{m}",
                placeholder="📌 pin tickers", label_visibility="collapsed",
            )
            pins[m] = [t.strip().upper() for t in pin_raw.replace(",", " ").split()
                       if t.strip()]
            with st.expander("🔍 Logic under this book", expanded=False):
                st.markdown(_logic_summary(m))

    checked = [m for m in _MANDATES if include[m]]
    if equal_split and checked:
        per = float(total_capital) / len(checked)
        for m in checked:
            capital[m] = per

    st.caption(
        f"{len(checked)} book(s) selected · {n_positions} positions each · "
        + ("equal split " + (f"${per:,.0f} each" if checked else "")
           if equal_split else "custom capital per book"))

    build = st.button("🏗 Build comparison", type="primary",
                      use_container_width=True, disabled=not checked)

    if build:
        symbols = _candidate_symbols(source, limit)
        all_pins = list(dict.fromkeys(p for m in checked for p in pins[m]))
        symbols = list(dict.fromkeys(all_pins + symbols))
        results = {}
        with st.spinner(f"Building {len(checked)} books over {len(symbols)} names ..."):
            price_cache.refresh_prices(symbols)          # warm the cache once
            # Score each name ONCE and reuse across all books in this run. The
            # four mandates differ only in how they blend the same per-strategy
            # scores, so this turns 4 scoring passes (incl. yfinance fundamentals
            # + stage analysis) into 1. Cleared in finally so a later run on
            # fresh prices never reuses a stale score.
            from ml.strategy_registry import enable_score_cache, clear_score_cache
            enable_score_cache()
            try:
                for m in checked:
                    cfg = PortfolioConfig(
                        account_size=float(capital[m]), max_positions=int(n_positions),
                        risk_per_trade=float(risk_pct), mandate=m,
                        use_av_news=bool(use_av), use_social=bool(use_social),
                        min_dollar_vol=float(min_liq_m) * 1_000_000,
                        force_include=pins[m],
                    )
                    results[m] = build_portfolio(symbols, cfg)
                    try:
                        from ml.portfolio_journal import record_proposal
                        record_proposal(results[m], float(capital[m]),
                                        note=f"multi-compare · {m}")
                    except Exception:
                        pass
            finally:
                clear_score_cache()
            # Feed the point-in-time training warehouse once with the locked
            # pool. Token-free; this is the corpus the learned router trains on.
            try:
                from ml.data_collector import collect_snapshot
                collect_snapshot(symbols)
            except Exception:
                pass
        st.session_state.multi_results = results
        st.session_state.multi_as_of = datetime.now().strftime("%Y-%m-%d %H:%M")
        st.session_state.multi_pool = len(symbols)
        st.session_state.multi_n = int(n_positions)

    results = st.session_state.get("multi_results")
    if not results:
        st.info("Pick your books and click **Build comparison**.")
        render_disclaimer()
        return

    n_positions = st.session_state.get("multi_n", n_positions)
    regime = next(iter(results.values())).regime
    color = _REGIME_COLOR.get(regime, "#57606a")
    st.markdown(
        f"<div style='background:{color};color:#fff;padding:10px 16px;"
        f"border-radius:8px'><b>Regime: {regime.upper()}</b> &nbsp;·&nbsp; "
        f"{len(results)} books &nbsp;·&nbsp; pool {st.session_state.get('multi_pool', 0)} "
        f"names &nbsp;·&nbsp; as of {st.session_state.get('multi_as_of', '')}</div>",
        unsafe_allow_html=True,
    )
    st.caption("Same locked pool fed to every book, so the differences below are "
               "strategy choices, not different inputs.")

    # ---- Side-by-side books ----
    book_cols = st.columns(len(results))
    for col, (m, plan) in zip(book_cols, results.items()):
        with col:
            filled = len(plan.holdings)
            st.markdown(f"**{_DISPLAY[m]}**")
            st.caption(f"${plan.invested:,.0f} invested · {plan.cash:,.0f} cash")
            # Honest fill count: show short books as short, do not pad with filler.
            if filled >= n_positions:
                st.success(f"{filled} of {n_positions} filled")
            else:
                st.warning(f"{filled} of {n_positions} filled · "
                           f"{n_positions - filled} slots unfilled (gates not met)")
            if plan.holdings:
                st.dataframe(_book_table(plan), use_container_width=True,
                             hide_index=True, height=min(38 * filled + 38, 560))
            else:
                st.caption("No names cleared the gates. In a weak tape for this "
                           "mandate, holding cash is the honest result.")

    # ---- Consensus / provenance ----
    holders: dict[str, list[str]] = defaultdict(list)
    convs: dict[str, list[float]] = defaultdict(list)
    for m, plan in results.items():
        for h in plan.holdings:
            holders[h.ticker].append(m)
            convs[h.ticker].append(h.composite)
    union = sorted(holders, key=lambda t: (-len(holders[t]), t))

    st.markdown("---")
    st.markdown("### 🔗 Consensus across books")
    st.caption("A name held by several books is a cross-strategy agreement signal. "
               "This count rides on top of the Weinstein stage, it does not replace it.")
    if union:
        cons = pd.DataFrame([{
            "Ticker": t,
            "# books": len(holders[t]),
            "Books": ", ".join(_DISPLAY[b].split(" ", 1)[1] for b in holders[t]),
            "Avg conviction": round(sum(convs[t]) / len(convs[t]), 2),
        } for t in union])
        st.dataframe(cons, use_container_width=True, hide_index=True)
        full = [t for t in union if len(holders[t]) == len(results)]
        if full:
            st.success(f"Held by ALL {len(results)} books (strongest consensus): "
                       + ", ".join(full))
    else:
        st.info("No holdings across any book in this run.")

    # ---- Handoff to Trader ----
    st.markdown("---")
    c1, c2 = st.columns([2, 3])
    with c1:
        if st.button(f"📈 Send {len(union)} names to Trader",
                     type="primary", use_container_width=True, disabled=not union):
            save_watchlist(_UNIVERSE_NAME, union)
            # Provenance metadata carried for the Trader tab to surface later.
            st.session_state.multi_provenance = {
                t: {"count": len(holders[t]), "books": holders[t]} for t in union}
            route_to_trader(_UNIVERSE_NAME)
            st.session_state.app_mode = "trader"
            st.rerun()
    with c2:
        st.caption(f"Saves the deduped union as the **{_UNIVERSE_NAME}** scan list "
                   "and opens Trader to observe every name's Weinstein stage. The "
                   "scan cache is invalidated so you never see a stale universe.")

    st.caption(f"⏳ {next(iter(results.values())).note}")
    render_disclaimer()
