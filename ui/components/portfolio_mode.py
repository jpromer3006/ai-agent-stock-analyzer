"""
portfolio_mode.py - Portfolio view.

Renders the ONE constructed portfolio from ml.portfolio_engine, instead of
separate scanner lists. The engine detects the market regime, asks the strategy
router for the regime-tilted blend, scores names across Weinstein, momentum, and
IPO (value/growth activate once the book index + key are live), then selects a
concentrated, risk-sized portfolio with a cash sleeve.

Additive component: does not touch Research or Trader.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from data import price_cache
from data.watchlists import (
    DEFAULT_NAME, list_watchlists, load_watchlist, watchlist_updated_at,
)
from ml.portfolio_engine import PortfolioConfig, build_portfolio
from ml.strategy_registry import display_name
from rag.strategy_router import ARCHETYPES
from ui.components.disclaimer import render_disclaimer

_REGIME_COLOR = {"bull": "#1a7f37", "neutral": "#9a6700", "bear": "#cf222e"}

_DYNAMIC_SOURCE = "Open market"
# Curated broad lists that act as universe sources, not personal scratch lists.
_UNIVERSE_LISTS = {"Top Picks", "Momentum Leaders"}


def _grouped_sources(watchlists: list[str]) -> tuple[list[str], dict[str, str]]:
    """Order the Candidate source options into Universe / Portfolio handoffs /
    My lists, and return a display-label map (raw name -> tagged label).

    Selectbox values stay the raw watchlist names so downstream loaders are
    unchanged; only the displayed label carries the group tag. The grouping is
    purely cosmetic ordering plus an emoji prefix, since a flat selectbox has no
    real section headers.
    """
    handoffs = [w for w in watchlists if w.startswith("Portfolio Holdings")]
    universe = [w for w in watchlists if w in _UNIVERSE_LISTS]
    mine = [w for w in watchlists
            if w not in _UNIVERSE_LISTS and not w.startswith("Portfolio Holdings")]

    ordered = [_DYNAMIC_SOURCE] + universe + handoffs + sorted(mine)
    labels: dict[str, str] = {_DYNAMIC_SOURCE: f"🌐 {_DYNAMIC_SOURCE}"}
    for w in universe:
        labels[w] = f"🌐 {w}"
    for w in handoffs:
        labels[w] = f"📥 {w}"
    for w in mine:
        labels[w] = f"📝 {w}"
    return ordered, labels


def _candidate_symbols(source: str, limit: int) -> list[str]:
    if source == _DYNAMIC_SOURCE:
        from data.universe import get_universe, load_liquid_ranking
        # Pool is built best-first and only ever as large as the cap asks for:
        #   1) liquid large-cap core (megacaps always evaluated)
        #   2) the nightly liquidity ranking (true liquidity order)
        #   3) top up from already-cached names (fast, no network)
        #   4) top up from the rest of the investable universe
        # Steps 3-4 are the fix for the cap silently stopping at the ranking
        # file's length (~1100): without them, sliding past that did nothing
        # because the ranking was shorter than the cap.
        try:
            from ml.momentum_screener import _SP100, _SECTOR_LEADERS
            core = list(_SP100) + list(_SECTOR_LEADERS)
        except Exception:
            core = []
        ranked = load_liquid_ranking() or []
        ordered = list(dict.fromkeys(core + ranked))

        if len(ordered) < limit:
            try:
                from data import price_cache
                have = set(ordered)
                ordered += [s for s in price_cache.cached_symbols() if s not in have]
            except Exception:
                pass
        if len(ordered) < limit:
            have = set(ordered)
            ordered += [u for u in get_universe(filtered=False).keys() if u not in have]
        return ordered[:limit]
    return load_watchlist(source)


def _risk_score(plan, account: float, max_pos: int) -> Optional[dict]:
    """Deterministic 0-100 portfolio risk score for the research team.

    Higher means riskier. It blends five transparent components, each scored
    0-100, then weighted. It also reports whether the book stayed within the
    requested position count, so a large universe pull can be flagged as risky
    while still confirming it respected the ticker budget.
    """
    holds = plan.holdings or []
    n = len(holds)
    if n == 0:
        return None

    weights = [getattr(h, "weight_pct", 0) / 100.0 for h in holds]

    # 1) Portfolio heat: total dollars at risk / account (Weinstein style).
    heat = sum(getattr(h, "risk_dollars", 0) for h in holds) / max(account, 1.0)
    heat_score = min(100.0, heat / 0.10 * 100.0)            # 10% heat -> 100

    # 2) Name concentration: largest single weight (25% one name -> 100).
    top_w = max(weights) if weights else 0.0
    conc_score = min(100.0, top_w / 0.25 * 100.0)

    # 3) Sector concentration: largest sector weight (40% -> 100).
    sec: dict[str, float] = {}
    for h, w in zip(holds, weights):
        sec[getattr(h, "sector", "?")] = sec.get(getattr(h, "sector", "?"), 0.0) + w
    top_sec = max(sec.values()) if sec else 0.0
    sector_score = min(100.0, top_sec / 0.40 * 100.0)

    # 4) Froth / earnings flags: share of flagged holdings.
    flagged = sum(1 for h in holds
                  if getattr(h, "froth", False) or getattr(h, "earnings_risk", False))
    flag_score = min(100.0, flagged / n * 100.0)

    # 5) Capital deployed: more invested, less cash cushion -> riskier.
    deployed_score = min(100.0, (plan.invested / max(account, 1.0)) * 100.0)

    comps = {
        "Portfolio heat": heat_score,
        "Name concentration": conc_score,
        "Sector concentration": sector_score,
        "Froth / earnings": flag_score,
        "Capital deployed": deployed_score,
    }
    blend = {"Portfolio heat": 0.30, "Name concentration": 0.25,
             "Sector concentration": 0.20, "Froth / earnings": 0.15,
             "Capital deployed": 0.10}
    score = sum(comps[k] * blend[k] for k in comps)
    if score < 34:
        band, color = "Low", "#3fb950"
    elif score < 67:
        band, color = "Moderate", "#ffc107"
    else:
        band, color = "High", "#f85149"
    return {"score": score, "band": band, "color": color, "components": comps,
            "within": n <= max_pos, "n": n, "max_pos": max_pos, "heat": heat}


def _render_benchmark_compare(plan, account: float, mkey: str) -> None:
    """Portfolio vs index funds: hypothetical growth of $10k for the book's
    CURRENT weights next to user-chosen benchmarks. The journaled scoreboard
    stays the honest forward record; this answers 'would an index fund have
    done as well?' over a past window."""
    import plotly.graph_objects as go
    from ml.benchmark_compare import (
        DEFAULT_BENCHMARKS, SUGGESTED_BENCHMARKS, build_comparison,
    )

    holds = plan.holdings or []
    if not holds:
        return

    with st.expander("📊 vs Index Funds (benchmark comparison)", expanded=False):
        c1, c2 = st.columns([3, 1])
        picks = c1.multiselect(
            "Benchmarks", SUGGESTED_BENCHMARKS, default=DEFAULT_BENCHMARKS,
            key=f"bench_picks_{mkey}",
            help="ETFs price intraday; mutual funds (VTSAX, FXAIX) price once "
                 "daily at NAV. Both compare fine on closes.",
        )
        extra = c1.text_input(
            "Add any ticker (ETF, index fund, or mutual fund)", "",
            key=f"bench_extra_{mkey}", placeholder="e.g. SCHD, VUG, SWPPX",
        )
        lookback_label = c2.selectbox(
            "Window", ["6 months", "1 year", "2 years"], index=1,
            key=f"bench_window_{mkey}",
        )
        lookback = {"6 months": 126, "1 year": 252, "2 years": 504}[lookback_label]

        benchmarks = list(picks) + [
            t.strip().upper() for t in extra.replace(",", " ").split() if t.strip()
        ]
        if not benchmarks:
            st.caption("Pick at least one benchmark.")
            return

        _acct = max(account, 1.0)
        holdings = [(h.ticker, getattr(h, "weight_pct", 0) / 100.0) for h in holds]
        cash_w = max(0.0, plan.cash / _acct)

        with st.spinner("Building comparison curves ..."):
            res = build_comparison(holdings, benchmarks,
                                   lookback_days=lookback, cash_weight=cash_w)

        if res.curves.empty:
            st.warning(res.note or "No comparison data available.")
            return
        if res.missing:
            st.caption("⚠️ No usable history for: " + ", ".join(sorted(set(res.missing))))

        fig = go.Figure()
        palette = {"Portfolio": "#00c853"}
        for col in res.curves.columns:
            fig.add_trace(go.Scatter(
                x=res.curves.index, y=res.curves[col], mode="lines", name=col,
                line=dict(
                    width=3 if col == "Portfolio" else 1.6,
                    color=palette.get(col),
                    dash="solid" if col == "Portfolio" else "dot",
                ),
                hovertemplate=f"<b>{col}</b><br>%{{x|%b %d, %Y}}<br>"
                              "$%{y:,.0f}<extra></extra>",
            ))
        fig.update_layout(
            title="Growth of $10,000 — this book's weights vs index funds",
            template="plotly_dark", height=420, hovermode="x unified",
            legend=dict(orientation="h", y=-0.15),
            margin=dict(l=40, r=20, t=50, b=40),
            yaxis_tickprefix="$", yaxis_tickformat=",.0f",
        )
        st.plotly_chart(fig, use_container_width=True, key=f"bench_chart_{mkey}")

        st.dataframe(res.stats, use_container_width=True, hide_index=True)
        st.caption(
            f"⚠️ {res.note} A backtest of today's weights is NOT the forward "
            "paper-trade record (see the scoreboard below) and not financial advice. "
            "If the index fund line keeps winning, that itself is a finding."
        )


def _render_plan(plan, trace, account: float, mkey: str, max_pos: int,
                 max_per_sector: int = 3) -> None:
    """Render a single constructed portfolio. ``mkey`` (the mandate name)
    suffixes all widget keys so multiple plans can render side by side in
    tabs without Streamlit key collisions."""
    # ---- Regime banner ----
    color = _REGIME_COLOR.get(plan.regime, "#57606a")
    st.markdown(
        f"<div style='background:{color};color:#fff;padding:12px 18px;border-radius:8px'>"
        f"<b>Regime: {plan.regime.upper()}</b> &nbsp;·&nbsp; mandate <b>{plan.mandate}</b> "
        f"&nbsp;·&nbsp; deploy cap <b>{plan.max_invested:.0%}</b> &nbsp;·&nbsp; "
        f"blend {', '.join(f'{display_name(k)} {v:.0%}' for k, v in plan.weights.items())}</div>",
        unsafe_allow_html=True,
    )

    # ---- Metrics (allocation, NOT performance) ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tickrs", len(plan.holdings))
    c2.metric("Invested", f"${plan.invested:,.0f}")
    c3.metric("Cash", f"${plan.cash:,.0f}")
    c4.metric("Active strategies", len(plan.active_strategies))
    _acct = max(account, 1.0)  # guard: account can be $0 before a value is entered
    st.caption(f"Allocation: {plan.invested/_acct:.0%} invested, {plan.cash/_acct:.0%} cash "
               "of your account. These are how the capital is split today, NOT a gain or "
               "loss — nothing has been traded yet.")

    # ---- Why N holdings (transparency: holdings is capped by supply, not the slider) ----
    n = len(plan.holdings)
    if n < max_pos:
        from collections import Counter
        sec_counts = Counter(getattr(h, "sector", "?") for h in plan.holdings)
        capped = [s for s, c in sec_counts.items()
                  if s not in ("UNKNOWN", "Other") and c >= max_per_sector]
        if capped:
            st.caption(
                f"📉 Stopped at {n} of {max_pos}: {len(capped)} sector(s) hit the "
                f"{max_per_sector}-per-sector cap ({', '.join(capped)}). Raise **Max per "
                f"sector**, widen the **Universe cap**, or lower **Min liquidity** to add names."
            )
        else:
            st.caption(
                f"📉 Stopped at {n} of {max_pos}: only {n} names cleared the gates "
                f"(Min liquidity floor + a strategy buy signal under this regime). Lower "
                f"**Min liquidity** or raise the **Universe cap** to widen the pool."
            )
    else:
        st.caption(f"✅ Filled all {max_pos} Tickr slots.")

    # ---- Risk score (deterministic, for the research team) ----
    r = _risk_score(plan, account, max_pos)
    if r:
        within = (f"✅ within your {max_pos}-Tickr limit ({r['n']} Tickrs)"
                  if r["within"] else
                  f"⚠️ OVER your {max_pos}-Tickr limit ({r['n']} Tickrs)")
        st.markdown(
            f"<div style='background:#161b22;border:2px solid {r['color']};"
            f"border-radius:10px;padding:14px 18px;margin:6px 0'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center'>"
            f"<div><div style='font-size:11px;color:#8b949e;letter-spacing:1px'>"
            f"PORTFOLIO RISK SCORE</div>"
            f"<div style='font-size:34px;font-weight:700;color:{r['color']}'>"
            f"{r['score']:.0f}/100 <span style='font-size:16px'>{r['band']}</span></div></div>"
            f"<div style='text-align:right;color:#c9d1d9;font-size:12px'>{within}<br>"
            f"<span style='color:#8b949e'>portfolio heat {r['heat']:.1%} of account</span>"
            f"</div></div></div>",
            unsafe_allow_html=True,
        )
        chips = []
        for k, v in r["components"].items():
            c = "#3fb950" if v < 34 else "#ffc107" if v < 67 else "#f85149"
            chips.append(
                f"<span style='background:{c};color:#fff;padding:3px 9px;border-radius:11px;"
                f"margin-right:6px;font-size:11px;font-weight:600'>{k} {v:.0f}</span>")
        st.markdown("<div style='margin:2px 0 8px'>" + "".join(chips) + "</div>",
                    unsafe_allow_html=True)
        st.caption("Risk score blends portfolio heat (30%), name concentration (25%), "
                   "sector concentration (20%), froth/earnings flags (15%), and capital "
                   "deployed (10%). Higher is riskier. Deterministic and auditable; "
                   "educational, not financial advice.")

    # ---- Agent trace (how the orchestrator built it) ----
    if trace:
        with st.expander("🧭 Agent trace — how the orchestrator built this", expanded=True):
            for s in trace:
                st.markdown(f"**{s.agent}** · _{s.role}_ — {s.summary}")

    # ---- Holdings table ----
    if plan.holdings:
        try:
            from data.universe import company_name as _cname
        except Exception:  # noqa: BLE001
            def _cname(t):  # noqa: ANN001
                return ""
        df = pd.DataFrame([{
            "Ticker": h.ticker, "Company": _cname(h.ticker) or "—",
            "Sector": h.sector, "Conviction": round(h.composite, 2),
            "News": round(h.sentiment, 2),
            "Flags": " ".join(f for f, on in
                              [("📌pin", getattr(h, "manual", False)),
                               ("⚠️earnings", h.earnings_risk), ("🔥froth", h.froth)] if on) or "-",
            "Shares": h.shares, "$ Position": round(h.dollars), "Weight %": h.weight_pct,
            "Entry": h.entry, "Stop": h.stop, "Risk $": round(h.risk_dollars),
            "Led by": display_name(max(h.strategy_scores, key=h.strategy_scores.get)) if h.strategy_scores else "n/a",
        } for h in plan.holdings])
        st.dataframe(df, use_container_width=True, hide_index=True)

        with st.expander("Per-Tickr rationale"):
            for h in plan.holdings:
                st.markdown(f"**{h.ticker}** — {h.rationale}")

        # ---- Place trades yourself (OROS never trades for you) ----
        st.markdown(
            "<a href='https://www.chase.com/' target='_blank' style='display:inline-block;"
            "background:#117ACA;color:#fff;padding:10px 18px;border-radius:8px;margin-right:10px;"
            "text-decoration:none;font-weight:600'>🏦 Open Chase</a>"
            "<a href='https://robinhood.com/login/' target='_blank' style='display:inline-block;"
            "background:#00C805;color:#000;padding:10px 18px;border-radius:8px;"
            "text-decoration:none;font-weight:600'>🟢 Open Robinhood</a>",
            unsafe_allow_html=True,
        )
        st.caption("Open your brokerage to place these trades yourself. OROS proposes "
                   "and journals only - it never places orders or moves money.")

        # ---- Portfolio vs index funds (SPY/QQQ default, user-pickable) ----
        _render_benchmark_compare(plan, account, mkey)
        if st.button("📈 Analyze this book in Trader (Weinstein stages)",
                     key=f"to_trader_{mkey}"):
            # Use the handoff helper so the selectbox key, the mirror var, and
            # the scan cache are all updated together — otherwise the Trader tab
            # silently reverts to the prior list (the "still 78" bug).
            from ui.components.trader_mode import route_to_trader
            route_to_trader(f"Portfolio Holdings {mkey}")
            st.session_state.app_mode = "trader"
            st.rerun()
    else:
        st.warning("No eligible names cleared the gates. In a weak tape the engine "
                   "holds cash by design.")

    st.caption(f"⏳ {plan.note}")


def _execute_builds(source: str, limit: int, selected_mandates: list[str],
                    claude_first: bool, account: float, max_pos: int,
                    max_per_sector: int, risk_pct: float, min_liq_m: float,
                    pin_raw: str, use_social: bool, use_av: bool) -> None:
    """Run the build pipeline for the given settings and store results in
    session state. Shared by the manual Build button and OROS Auto-build so the
    two paths can never drift apart."""
    symbols = _candidate_symbols(source, limit)
    pins = [t.strip().upper() for t in pin_raw.replace(",", " ").split() if t.strip()]
    # Pinned names must be in the candidate set so they flow through; the
    # engine also force-includes them, but adding here keeps scoring warm.
    symbols = list(dict.fromkeys(pins + symbols))

    # ---- Claude-first selection (optional) -------------------------
    # Claude chooses the names and assigns each to a mandate; the quant
    # engine below still gates, sizes, and risk-stops whatever it picks.
    picks_by_mandate: dict = {}
    if claude_first:
        from ml.claude_selector import select_and_assign
        # Ask Claude for noticeably MORE names than max_positions, because
        # the quant engine still gates some out. Otherwise a tiny shortlist
        # yields only a handful of holdings (the "15 requested, 5 placed"
        # gap). Show it a wider liquidity slice too.
        _per = max(int(max_pos) * 2, 25)
        # Honor the universe cap: Claude must see the FULL requested pool
        # (so "1000" really means 1000 candidates), not a fixed slice.
        with st.spinner(
            f"🤖 Claude selecting from {len(symbols)} candidates ..."
        ):
            picks_by_mandate, sel_err = select_and_assign(
                symbols, selected_mandates,
                max_per_mandate=_per, universe_cap=int(limit))
        if sel_err:
            st.warning(f"Claude-first selection unavailable ({sel_err}). "
                       "Falling back to the full quant universe.")
            picks_by_mandate = {}

    # Warm prices. With Claude-first, only the union of picks is needed.
    if claude_first and picks_by_mandate:
        warm = list(dict.fromkeys(
            pins + [t for v in picks_by_mandate.values() for t in v]))
    else:
        warm = symbols
    with st.spinner(f"Warming prices for {len(warm)} names ..."):
        price_cache.refresh_prices(warm)
    from ml.agent_orchestrator import run_orchestrated

    plans: dict = {}
    progress = st.progress(0.0)
    for i, m in enumerate(selected_mandates):
        # Candidate set for this mandate: Claude's shortlist when the
        # toggle is on and it returned names, otherwise the full universe.
        mand_picks = picks_by_mandate.get(m, [])
        if claude_first and mand_picks:
            cand = list(dict.fromkeys(pins + mand_picks))
        else:
            cand = symbols
        cfg = PortfolioConfig(account_size=float(account), max_positions=int(max_pos),
                              risk_per_trade=float(risk_pct), mandate=m,
                              use_av_news=bool(use_av), use_social=bool(use_social),
                              min_dollar_vol=float(min_liq_m) * 1_000_000,
                              max_per_sector=int(max_per_sector),
                              force_include=pins)
        _src = "Claude's picks" if (claude_first and mand_picks) else "the universe"
        with st.spinner(f"Constructing '{m}' from {_src} ({len(cand)} names) ..."):
            result = run_orchestrated(cand, cfg)
        plans[m] = {"plan": result.plan, "trace": result.trace}
        st.session_state.pop(f"analyst_verdicts_{m}", None)  # fresh build
        # Journal every build (tagged by mandate) for the comparison.
        try:
            from ml.portfolio_journal import record_proposal
            record_proposal(result.plan, float(account),
                            trace=result.trace, note=f"UI build · {m}")
        except Exception:
            pass
        # Each mandate's holdings get their own scan list for the Trader handoff.
        try:
            from data.watchlists import save_watchlist
            tickers = [h.ticker for h in result.plan.holdings]
            if tickers:
                save_watchlist(f"Portfolio Holdings {m}", tickers)
        except Exception:
            pass
        progress.progress((i + 1) / len(selected_mandates))
    progress.empty()

    st.session_state.portfolio_plans = plans
    st.session_state.portfolio_account = float(account)
    # Remember the geometry the book was actually built with, so rendering
    # after an auto-build doesn't read the (untouched) sidebar sliders.
    st.session_state.portfolio_buildinfo = {
        "max_pos": int(max_pos), "max_per_sector": int(max_per_sector)}

    # Combine every built book into ONE Trader scan list so the Trader
    # tab automatically analyzes exactly these names — nothing else
    # unless the user adds tickers manually in the Trader sidebar.
    combined: list[str] = []
    for _m in plans:
        combined += [h.ticker for h in plans[_m]["plan"].holdings]
    combined = list(dict.fromkeys(combined))  # union, order-preserving
    if combined:
        try:
            from data.watchlists import save_watchlist
            save_watchlist("Portfolio Holdings All", combined)
            # Point Trader at the combined book (sets the selectbox key
            # before that widget is instantiated, and clears stale scans).
            from ui.components.trader_mode import route_to_trader
            route_to_trader("Portfolio Holdings All")
        except Exception:
            pass

    st.success(
        f"Built {len(plans)} portfolio(s): {', '.join(plans)}. "
        f"Trader is now set to scan the combined {len(combined)} Tickr(s) "
        "from these books (open the Trader tab)."
    )


def _render_latest_journaled_build() -> None:
    """The tab's opening view: this morning's cloud build, straight from the
    journal — renders instantly (no scan, no network)."""
    try:
        from ml.daily_report import _latest_proposal
        rec = _latest_proposal()
    except Exception:  # noqa: BLE001
        rec = None
    if not rec or not rec.get("holdings"):
        return
    try:
        from data.universe import company_name as _cname
    except Exception:  # noqa: BLE001
        def _cname(t):  # noqa: ANN001
            return ""
    try:
        from ml.strategy_registry import display_name as _dn
    except Exception:  # noqa: BLE001
        def _dn(m):  # noqa: ANN001
            return m
    acct = float(rec.get("account") or 100_000.0)
    inv = float(rec.get("invested") or 0.0)
    st.markdown(f"#### 🌅 Latest build — {rec.get('date', '?')} "
                f"({rec.get('regime', '?').upper()} · "
                f"{_dn(rec.get('mandate', ''))})")
    c1, c2, c3 = st.columns(3)
    c1.metric("Positions", len(rec.get("holdings", [])))
    c2.metric("Deployed", f"{inv / acct:.0%}")
    c3.metric("Cash", f"{(acct - inv) / acct:.0%}")
    df = pd.DataFrame([{
        "Ticker": h.get("ticker"), "Company": _cname(h.get("ticker", "")) or "—",
        "Sector": h.get("sector"), "Shares": h.get("shares"),
        "Entry": h.get("entry"), "Stop": h.get("stop"),
        "Weight %": h.get("weight_pct"),
        "Led by": _dn(h.get("led_by")) if h.get("led_by") else "n/a",
    } for h in rec.get("holdings", [])])
    st.dataframe(df, use_container_width=True, hide_index=True)
    bench = rec.get("bench") or []
    if bench:
        with st.expander(f"🪑 The bench — {len(bench)} candidates excluded, "
                         "with reasons"):
            for b in bench:
                st.markdown(f"- **{b.get('ticker')}** — {b.get('reason')}")
    st.caption("Journaled by the autonomous morning run. Auto-build below "
               "constructs a fresh book without touching this record.")
    st.markdown("---")


def render_portfolio_mode() -> None:
    st.markdown(
        "<div style='background:#0d1117;padding:14px 20px;border-radius:8px;"
        "margin-bottom:12px'><span style='color:#e6edf3;font-size:22px;"
        "font-weight:700'>💼 Portfolio</span><span style='color:#8b949e;"
        "margin-left:12px;font-size:13px'>Regime-aware, multi-strategy construction"
        "</span></div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "One constructed portfolio, not lists. The engine reads the market regime, "
        "blends Weinstein, momentum, and the IPO track via the strategy router, then "
        "selects and risk-sizes a concentrated book. Educational, not financial advice."
    )

    # ---- Morning run automation (manual run + schedule toggle) ----
    from ui.components.cron_control import render_cron_panel
    render_cron_panel()

    # ---- Sidebar controls (hidden by default; OROS can drive itself) ----
    # Settings live behind a click-to-open expander so the default experience
    # is autonomous: press Auto-build and OROS chooses everything from the data.
    watchlists = list_watchlists() or [DEFAULT_NAME]
    with st.sidebar.expander("💼 Portfolio settings (manual — click to open)",
                             expanded=False):
        # Open market is the default so the portfolio scans the whole market,
        # not a small saved watchlist. Options are grouped (🌐 universe, 📥 portfolio
        # handoffs, 📝 personal lists) so the source list reads cleanly.
        _source_options, _source_labels = _grouped_sources(watchlists)
        source = st.selectbox(
            "Candidate source",
            _source_options, index=0,
            format_func=lambda w: _source_labels.get(w, w),
            help="🌐 universe sources scan broad lists, 📥 portfolio handoffs reuse a "
                 "previously built book, 📝 are your own saved lists. Open market "
                 "scans the whole market up to the cap.",
        )
        limit = st.slider("Universe cap (Open market only)", 100, 3000, 400, 100)
        if source == _DYNAMIC_SOURCE:
            # Open market is rescanned live each run, so its timestamp is "now".
            # Show the ACTUAL pool size, not the slider, so the cap can't overstate it.
            from datetime import datetime as _dt
            actual = len(_candidate_symbols(source, limit))
            capnote = f" (cap {limit})" if actual < limit else ""
            st.caption(
                f"{actual} candidate names{capnote} · scanned {_dt.now():%b %d, %Y %H:%M}"
            )
        else:
            n_preview = len(load_watchlist(source))
            ts = watchlist_updated_at(source)
            when = f" · chosen {ts:%b %d, %Y %H:%M}" if ts else " · date unknown"
            st.caption(f"{n_preview} candidate names{when}")
        st.markdown("**Mandate** — check one or more to build together")
        _mandate_keys = list(ARCHETYPES.keys())
        selected_mandates = [
            m for m in _mandate_keys
            if st.checkbox(m, value=(m == "swing_momentum"), key=f"mandate_{m}")
        ]
        claude_first = st.checkbox(
            "🤖 Claude picks first (LLM selects names, quant sizes them)",
            value=False,
            help="When on, Claude chooses tickers from a liquidity-ranked slice of the "
                 "universe and assigns each to a mandate; the quant engine then gates, "
                 "sizes, and risk-stops those picks. Uses a few cents on Haiku.",
        )
        account = st.number_input(
            "Account Value ($)", 0, 100_000_000, 0, 1_000,
            help="Total capital this portfolio is sized against. Start at $0 and enter "
                 "your real account value; position sizes and risk dollars scale from it. "
                 "Auto-build uses the last journaled account when this is left at $0.",
        )
        max_pos = st.slider("Max Tickrs (concentrated)", 5, 25, 12)
        max_per_sector = st.slider(
            "Max per sector", 1, 10, 3,
            help="Diversification cap: the most names allowed from any one sector. Low "
                 "keeps the book diversified but can stop it short of your Tickr limit "
                 "when winners cluster in a few sectors (the usual reason Tickrs < Max "
                 "Tickrs). Raise it to let more correlated names in.",
        )
        risk_pct = st.slider("Risk per trade (%)", 0.25, 3.0, 1.25, 0.25) / 100.0
        min_liq_m = st.slider("Min liquidity ($M daily volume)", 5, 200, 25, 5,
                              help="Higher = only large, liquid names (more blue-chip). "
                                   "Lower lets smaller momentum names in.")
        pin_raw = st.text_input(
            "📌 Pin tickers (always include)",
            value="",
            help="Comma or space separated. These names are forced into the book "
                 "regardless of the gates, with a protective 8% fallback stop if no "
                 "strategy supplies one. They lead the holdings and are flagged 📌.",
        )
        st.markdown("**News & sentiment**")
        use_social = st.checkbox("Social attention (StockTwits, free)", value=True)
        use_av = st.checkbox("Alpha Vantage news (uses 25/day quota)", value=False)

    c_auto, c_manual = st.columns(2)
    auto_build = c_auto.button("🤖 Auto-build — OROS picks the settings",
                               type="primary", use_container_width=True,
                               help="OROS derives every setting from the data: regime, "
                                    "paper-trade scoreboard, last journaled account. "
                                    "No sliders needed.")
    build = c_manual.button("🏗 Build with my settings", use_container_width=True)
    st.caption(
        "Auto-build lets OROS choose the mandate, risk, and universe from the data "
        "it gathers (regime + forward scoreboard). Manual settings live in the "
        "sidebar expander. Open market scans the whole market up to the cap; the "
        "first build is slower while the price cache warms (the nightly job "
        "pre-caches it). Brand-new IPOs (e.g. SPCX, under ~15 days listed) are "
        "handled by the separate IPO track, not this constructed portfolio."
    )

    if auto_build:
        from ml.auto_config import choose_auto_settings
        auto = choose_auto_settings()
        # If the user typed a real account value, honor it; otherwise reuse the
        # last journaled account so the run needs no human input at all.
        acct = float(account) if account > 0 else float(auto["account"])
        st.info(
            f"🤖 **Auto settings** — regime **{auto['regime'].upper()}** · mandate "
            f"**{auto['mandate']}** ({auto['mandate_reason']}) · "
            f"{auto['max_pos']} Tickrs · risk {auto['risk_pct']:.2%}/trade · "
            f"min liquidity ${auto['min_liq_m']}M · account ${acct:,.0f}"
        )
        _execute_builds(source=_DYNAMIC_SOURCE, limit=int(auto["limit"]),
                        selected_mandates=[auto["mandate"]], claude_first=False,
                        account=acct, max_pos=auto["max_pos"],
                        max_per_sector=auto["max_per_sector"],
                        risk_pct=auto["risk_pct"], min_liq_m=auto["min_liq_m"],
                        pin_raw="", use_social=True, use_av=False)
    elif build:
        if not selected_mandates:
            st.warning("Pick at least one mandate (the checkboxes in the sidebar).")
        elif account <= 0:
            st.warning("Enter your Account Value (greater than $0) so Tickrs can be "
                       "sized — or use 🤖 Auto-build.")
        else:
            _execute_builds(source=source, limit=int(limit),
                            selected_mandates=selected_mandates,
                            claude_first=claude_first, account=float(account),
                            max_pos=int(max_pos), max_per_sector=int(max_per_sector),
                            risk_pct=float(risk_pct), min_liq_m=float(min_liq_m),
                            pin_raw=pin_raw, use_social=use_social, use_av=use_av)

    plans = st.session_state.get("portfolio_plans")
    if not plans:
        # Instant first impression (JP, 2026-07-29): show this morning's
        # JOURNALED build immediately — no waiting, no click. Auto-build
        # still constructs a fresh book on demand.
        _render_latest_journaled_build()
        st.info("Click **🤖 Auto-build** and OROS will construct a fresh book "
                "from the data — or open the sidebar settings and build "
                "manually.")
        render_disclaimer()
        return

    account = st.session_state.get("portfolio_account", float(account))
    # Render with the geometry the book was BUILT with (auto-build may differ
    # from the untouched sidebar sliders).
    binfo = st.session_state.get("portfolio_buildinfo", {})
    r_max_pos = int(binfo.get("max_pos", max_pos))
    r_max_sec = int(binfo.get("max_per_sector", max_per_sector))

    # ---- Render each selected mandate's portfolio ----
    names = list(plans.keys())
    if len(names) == 1:
        _render_plan(plans[names[0]]["plan"], plans[names[0]]["trace"],
                     account, names[0], r_max_pos, r_max_sec)
    else:
        tabs = st.tabs([f"💼 {m}" for m in names])
        for tab, m in zip(tabs, names):
            with tab:
                _render_plan(plans[m]["plan"], plans[m]["trace"],
                             account, m, r_max_pos, r_max_sec)

    # ---- Opportunities & changes (rendered once, anchored to the first book) ----
    first_plan = plans[names[0]]["plan"]
    with st.expander("🚨 Opportunities & changes (IPO breakouts + what moved)", expanded=False):
        try:
            from ml.notifier import scan_opportunities, detect_changes
            from ml.portfolio_journal import list_proposals
            import json as _json
            opps = scan_opportunities(max_check=300)
            if opps["breakouts"]:
                st.markdown("**Fresh IPO breakouts (the SPCX case):**")
                st.dataframe(pd.DataFrame(opps["breakouts"]), use_container_width=True, hide_index=True)
            else:
                st.caption("No confirmed IPO breakouts right now.")
            if opps["watch"]:
                st.caption(f"Young names on watch ({len(opps['watch'])}): "
                           + ", ".join(w["ticker"] for w in opps["watch"][:15]))
            props = list_proposals()
            if len(props) >= 2:
                prev = [h["ticker"] for h in _json.loads(props[-2].read_text()).get("holdings", [])]
                ch = detect_changes([h.ticker for h in first_plan.holdings], prev)
                if ch["added"] or ch["removed"]:
                    st.markdown(f"**Since last run:** added {ch['added'] or '—'}, "
                                f"dropped {ch['removed'] or '—'}")
        except Exception as e:  # noqa: BLE001
            st.caption(f"Opportunity scan unavailable: {e}")

    # ---- Paper-trade scoreboard (swing vs fast vs ...) ----
    try:
        from ml.portfolio_journal import compare_by_mandate
        scoreboard = compare_by_mandate()
        if scoreboard:
            with st.expander("📊 Paper-trade scoreboard (mandate comparison, to August)"):
                st.caption("Every Build is journaled. Realized returns fill in as each "
                           "holding's horizon elapses (the nightly job updates them). "
                           "Counts the full book, not cherry-picked winners.")
                st.dataframe(
                    pd.DataFrame([{"Mandate": m, "proposals": v["proposals"],
                                   "scored": v["scored"], "avg_realized_%": v["avg_realized_pct"],
                                   "hit_rate_%": v["avg_hit_rate_pct"]}
                                  for m, v in scoreboard.items()]),
                    use_container_width=True, hide_index=True)
                for m, v in scoreboard.items():
                    if v.get("latest_holdings"):
                        st.caption(f"**{m}** latest book: " + ", ".join(v["latest_holdings"]))
    except Exception:
        pass

    render_disclaimer()
