"""
app.py — Streamlit UI for Ai-Agent Stock Analyzer.

Two modes:
    🔬 Research Mode — deep agentic memo per ticker (~30-90s / ticker)
                       SEC EDGAR + RAG + Claude tool-use + citation validator
    📈 Trader Mode   — Weinstein Stage Analysis (~0.5s / ticker)
                       30-week MA, RS vs SPY, volume, bull probability
                       Scans the full watchlist for Stage 2/4 signals
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st

from agents.classifier import classify, explain_classification
from agents.orchestrator import run_agent
from ui.components.disclaimer import render_disclaimer
# demo_qr imports removed (2026-08-06 security review)
from data.tickers import UNIVERSE, StockCategory, tickers_by_category, category_counts


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
from pathlib import Path as _Path

_ICON = _Path(__file__).parent / "assets" / "oros_icon.png"
st.set_page_config(
    page_title="OROS Market Desk",
    page_icon=str(_ICON) if _ICON.exists() else "⛰️",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "analyses" not in st.session_state:
    st.session_state.analyses = {}
if "selected_ticker" not in st.session_state:
    st.session_state.selected_ticker = None
if "app_mode" not in st.session_state:
    # Scanner-first (JP, 2026-07-28): the app opens on the Stage Scan —
    # OROS is a computational scanner first, with investment steering.
    st.session_state.app_mode = "trader"
# Migrate any old 'assistant' mode state to research
if st.session_state.app_mode == "assistant":
    st.session_state.app_mode = "research"


# ---------------------------------------------------------------------------
# Top banner: Mode toggle
# ---------------------------------------------------------------------------
# Brand mark in the banner (JP, 2026-07-28): the gold mountain-and-sun,
# base64-embedded so the header needs no static file server. Falls back
# to the emoji if the asset is missing.
try:
    import base64 as _b64
    _logo_b64 = _b64.b64encode(_ICON.read_bytes()).decode()
    _logo_tag = ("<img src='data:image/png;base64," + _logo_b64 +
                 "' style='height:34px;vertical-align:middle;"
                 "margin-right:10px'/>")
except Exception:  # noqa: BLE001
    _logo_tag = "⛰️ "
st.markdown(
    "<div style='background:linear-gradient(90deg,#1a1a2e,#16213e);"
    "padding:14px 20px;border-radius:8px;margin-bottom:12px'>"
    "<div style='display:flex;justify-content:space-between;align-items:center'>"
    "<div><span style='color:#e6edf3;font-size:22px;font-weight:700'>"
    + _logo_tag + "OROS Market Desk</span>"
    "<span style='color:#8b949e;margin-left:12px;font-size:13px'>"
    "Capstone · agentic computational scanner with investment steering"
    "</span></div></div></div>",
    unsafe_allow_html=True,
)

mode_col1, mode_col2, mode_col3, mode_col4 = st.columns(4)
with mode_col1:
    trader_active = st.session_state.app_mode == "trader"
    label = "📈 Stage Scan  ✓" if trader_active else "📈 Stage Scan"
    if st.button(label, key="mode_trader", use_container_width=True,
                 type="primary" if trader_active else "secondary"):
        st.session_state.app_mode = "trader"
        st.rerun()
with mode_col2:
    portfolio_active = st.session_state.app_mode == "portfolio"
    label = "💼 Portfolio  ✓" if portfolio_active else "💼 Portfolio"
    if st.button(label, key="mode_portfolio", use_container_width=True,
                 type="primary" if portfolio_active else "secondary"):
        st.session_state.app_mode = "portfolio"
        st.rerun()
with mode_col3:
    research_active = st.session_state.app_mode == "research"
    label = "🔬 Research  ✓" if research_active else "🔬 Research"
    if st.button(label, key="mode_research", use_container_width=True,
                 type="primary" if research_active else "secondary"):
        st.session_state.app_mode = "research"
        st.rerun()
with mode_col4:
    yt_active = st.session_state.app_mode == "youtube"
    label = "📺 YouTube News  ✓" if yt_active else "📺 YouTube News"
    if st.button(label, key="mode_youtube", use_container_width=True,
                 type="primary" if yt_active else "secondary"):
        st.session_state.app_mode = "youtube"
        st.rerun()

st.markdown("")

# One-time launch prompt: offer to enable the morning-run schedule if it is
# not already loaded. Managed thereafter in Portfolio → Morning run & automation.
from ui.components.cron_control import maybe_prompt_cron_on_launch
maybe_prompt_cron_on_launch()


# ═══════════════════════════════════════════════════════════════════════════
# YOUTUBE NEWS MODE — customize, render, publish (no email)
# ═══════════════════════════════════════════════════════════════════════════
if st.session_state.app_mode == "youtube":
    from ui.components.youtube_mode import render_youtube_mode
    render_youtube_mode()
    st.stop()


# ═══════════════════════════════════════════════════════════════════════════
# TRADER MODE
# ═══════════════════════════════════════════════════════════════════════════
if st.session_state.app_mode == "trader":
    from ui.components.trader_mode import render_trader_mode
    render_trader_mode()
    st.stop()


# ═══════════════════════════════════════════════════════════════════════════
# PORTFOLIO MODE — one regime-aware, multi-strategy constructed portfolio
# ═══════════════════════════════════════════════════════════════════════════
if st.session_state.app_mode == "portfolio":
    from ui.components.portfolio_mode import render_portfolio_mode
    render_portfolio_mode()
    st.stop()


# ═══════════════════════════════════════════════════════════════════════════
# COMPARE MODE removed — multi-mandate comparison now lives inside Portfolio
# (check several mandates and build them together). Any old 'compare' state
# falls through to Portfolio.
# ═══════════════════════════════════════════════════════════════════════════
if st.session_state.app_mode == "compare":
    st.session_state.app_mode = "portfolio"
    from ui.components.portfolio_mode import render_portfolio_mode
    render_portfolio_mode()
    st.stop()


# ═══════════════════════════════════════════════════════════════════════════
# RESEARCH DOSSIER (Phase 2 — five-section Weinstein decision view)
#
# When the user clicks "Open Research dossier" from the Trader tab, the
# button sets st.session_state.research_open_pending = True. We pick
# that up here and flip the active view to the dossier. The user can
# still toggle back to the classic memo flow with the radio above.
# ═══════════════════════════════════════════════════════════════════════════
if st.session_state.get("research_open_pending"):
    st.session_state.research_view = "dossier"
    st.session_state.research_open_pending = False
    # Sync the dossier ticker into the legacy selected_ticker so the
    # text box pre-populates if the user toggles back to classic.
    if st.session_state.get("research_ticker"):
        st.session_state.selected_ticker = st.session_state.research_ticker

if "research_view" not in st.session_state:
    st.session_state.research_view = "dossier"   # Phase 2 default

# Research is now a SINGLE unified view. The dossier hosts the sector
# specialist memo inline (Section 4a) with audio + numbered sources, so
# the separate "classic memo" tab/toggle was removed — no extra tab.
st.session_state.research_view = "dossier"

if st.session_state.research_view == "dossier":
    from ui.components.research_dossier import render_research_dossier

    # Header + small ticker input above the dossier
    st.markdown("## 🔬 Research — Weinstein Decision Dossier")
    st.caption(
        "Five-section dossier: stage header, Chapter 4 resistance, "
        "Chapter 3 calendar, two voices (sector specialist + Chartist "
        "Strategist), decision panel with Setup Confidence Score."
    )

    dc1, dc2 = st.columns([5, 1])
    with dc1:
        dossier_ticker_input = st.text_input(
            "Ticker for dossier",
            value=(st.session_state.get("research_ticker")
                   or st.session_state.get("selected_ticker") or ""),
            placeholder="Enter a ticker (e.g. ORA, NVDA, AAPL)",
            label_visibility="collapsed",
            key="dossier_ticker_input",
        )
    with dc2:
        if st.button("📖 Open dossier", type="primary",
                     use_container_width=True, key="dossier_open_btn"):
            if dossier_ticker_input.strip():
                st.session_state.research_ticker = dossier_ticker_input.strip().upper()
                st.session_state.selected_ticker = st.session_state.research_ticker
                st.rerun()

    active_ticker = st.session_state.get("research_ticker") or dossier_ticker_input.strip()
    if active_ticker:
        render_research_dossier(active_ticker)
    else:
        st.info(
            "Enter a ticker above and click Open dossier, or click an "
            "Open Research dossier button from the Trader tab to land "
            "here pre-populated."
        )

    # Bottom disclaimer
    from ui.components.disclaimer import render_disclaimer
    st.divider()
    render_disclaimer(compact=True)
    st.stop()


# ═══════════════════════════════════════════════════════════════════════════
# RESEARCH MODE (legacy memo flow — kept for users who toggle back)
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🔬 Research Mode")
    st.caption(f"Universe: {len(UNIVERSE)} tickers across {len(category_counts())} specialists")


# Demo QR removed (2026-08-06 security review): it auto-advertised an
# unauthenticated public tunnel URL. Re-enable deliberately for demo days.


with st.sidebar:
    st.divider()
    st.subheader("Browse Universe")

    category_filter = st.selectbox(
        "Filter by category",
        options=["All"] + sorted([c.value for c in StockCategory if c.value in category_counts()]),
        key="cat_filter",
    )

    st.caption("Click any ticker to analyze:")

    if category_filter == "All":
        tickers_to_show = list(UNIVERSE.keys())
    else:
        tickers_to_show = tickers_by_category(StockCategory[category_filter])

    for ticker in sorted(tickers_to_show):
        meta = UNIVERSE[ticker]
        emoji = {
            "REIT": "🏢", "INFRASTRUCTURE_EPC": "🏗️", "BANK": "🏦",
            "TECH": "💻", "ENERGY": "⚡", "CONSUMER": "🛒",
            "HEALTHCARE": "🏥", "GENERIC": "📊",
        }.get(meta.category.value, "📊")

        label = f"{emoji} **{ticker}** — {meta.company_name[:22]}"
        if st.button(label, key=f"wl_{ticker}", use_container_width=True):
            st.session_state.selected_ticker = ticker
            st.rerun()

    st.divider()
    if st.button("🗑️ Clear cached analyses", use_container_width=True):
        st.session_state.analyses = {}
        st.session_state.selected_ticker = None
        st.rerun()


# ---------------------------------------------------------------------------
# Main area — Search bar
# ---------------------------------------------------------------------------
# (Demo QR banner removed — 2026-08-06 security review)

st.title("🔬 Research Mode — deep memo via Claude + SEC")
st.caption(
    "Enter any ticker — the classifier routes to the right specialist agent, "
    "which uses SEC EDGAR, semantic 10-K search, and Claude tool-use to generate a custom memo."
)

search_col, btn_col = st.columns([5, 1])
with search_col:
    search_input = st.text_input(
        "Ticker",
        value=st.session_state.selected_ticker or "",
        placeholder="Enter any ticker symbol (e.g. O, PLD, JPM, MSFT, AAPL...)",
        label_visibility="collapsed",
    )
with btn_col:
    run_clicked = st.button("▶️ Run Agents", type="primary", use_container_width=True)

# Resolve ticker AND force a re-run when the button is pressed
# (so clicking Run Agents on an already-cached ticker actually regenerates)
ticker = None
if run_clicked and search_input.strip():
    ticker = search_input.strip().upper()
    st.session_state.selected_ticker = ticker
    # If cached, clear it so the agents run fresh
    st.session_state.analyses.pop(ticker, None)
elif st.session_state.selected_ticker:
    ticker = st.session_state.selected_ticker


# ---------------------------------------------------------------------------
# Landing state
# ---------------------------------------------------------------------------
if not ticker:
    st.info(
        "👆 Type any ticker and click **▶️ Run Agents** for a deep memo with SEC "
        "financials, 10-K analysis, and inline citations. The memo comes with "
        "a **🔊 Listen** button and a **💬 Chat** panel below so you can ask "
        "follow-ups. For fast Weinstein stage scans across your whole universe, "
        "switch to **📈 Trader Mode** above."
    )

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Universe", f"{len(UNIVERSE)}")
    with col2:
        st.metric("Specialists", f"{len(category_counts())}")
    with col3:
        st.metric("Data Sources", "6")
    with col4:
        st.metric("Tool Functions", "12+")

    st.markdown("### How it works")
    st.markdown(
        """
        1. **Classify** — The ticker is routed to a specialist agent (REIT, Infra, Bank, Tech, Energy, Consumer, Healthcare, Generic)
        2. **Pull SEC EDGAR data** — XBRL structured financials (income statement, balance sheet, cash flow)
        3. **Semantic search 10-K** — ChromaDB + sentence-transformers retrieval over filing text
        4. **Compute live bull probability** — price momentum + growth + sentiment + leverage
        5. **Generate adaptive memo** — sections tailored to sector (REIT focuses on FFO/dividends, banks on NIM/capital, etc.)
        """
    )
    st.stop()


# ---------------------------------------------------------------------------
# Pre-run: show classification
# ---------------------------------------------------------------------------
classification = explain_classification(ticker)
col_a, col_b = st.columns([3, 1])
with col_a:
    st.markdown(f"## {ticker} — {classification.get('company_name', ticker)}")
with col_b:
    emoji = {
        "REIT": "🏢", "INFRASTRUCTURE_EPC": "🏗️", "BANK": "🏦",
        "TECH": "💻", "ENERGY": "⚡", "CONSUMER": "🛒",
        "HEALTHCARE": "🏥", "GENERIC": "📊",
    }.get(classification["category"], "📊")
    st.markdown(
        f"<div style='text-align:right;padding:8px 16px;"
        f"background:#1a1a2e;color:white;border-radius:8px;font-weight:600'>"
        f"{emoji} {classification['category']}</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Run or display cached
# ---------------------------------------------------------------------------
cache_key = ticker

if cache_key in st.session_state.analyses:
    st.caption(
        f"✓ Analysis cached. Type the ticker again and click **▶️ Run Agents** "
        f"at the top to regenerate."
    )

# If not cached, run the agent with streaming trace
if cache_key not in st.session_state.analyses:
    trace_container = st.container()
    memo_container = st.container()

    with trace_container:
        st.markdown("### 🤖 Agents Working")
        trace_expander = st.expander("Live tool calls — watch the specialists collaborate",
                                      expanded=True)

    tool_calls = []
    memo_text = ""
    error_msg = None

    with trace_expander:
        status_placeholder = st.empty()
        step_idx = 0

        try:
            for event in run_agent(ticker):
                etype = event.get("type")

                if etype == "classified":
                    status_placeholder.info(
                        f"🎯 **Classified:** {event['category']} → {event['agent_name']}"
                    )

                elif etype == "tool_call":
                    step_idx += 1
                    tool_name = event["tool"]
                    input_str = ", ".join(f"{k}={v!r}" for k, v in event["input"].items())
                    st.markdown(
                        f"**{step_idx}.** 🔧 `{tool_name}({input_str})`"
                    )
                    tool_calls.append({
                        "tool": tool_name,
                        "input": event["input"],
                    })

                elif etype == "tool_result":
                    st.caption(f"    ← {event['result_length']} chars returned")

                elif etype == "text_delta":
                    memo_text += event["text"]

                elif etype == "done":
                    memo_text = event["memo"]
                    status_placeholder.success(
                        f"✅ **Done** in {event['steps']} steps. {len(tool_calls)} tools called."
                    )

                elif etype == "error":
                    error_msg = event["message"]
                    status_placeholder.error(f"❌ Error: {error_msg}")
                    break
        except Exception as exc:
            error_msg = str(exc)
            status_placeholder.error(f"❌ Exception: {exc}")

    if memo_text and not error_msg:
        st.session_state.analyses[cache_key] = {
            "ticker": ticker,
            "company": classification.get("company_name", ticker),
            "category": classification["category"],
            "memo": memo_text,
            "tool_calls": tool_calls,
            "generated_at": datetime.utcnow(),
        }


# ---------------------------------------------------------------------------
# Display cached memo
# ---------------------------------------------------------------------------
if cache_key in st.session_state.analyses:
    analysis = st.session_state.analyses[cache_key]

    st.divider()
    st.markdown("### 📄 Research Memo and Analysis")
    st.markdown(analysis["memo"])

    # 🔊 Listen to the memo (ElevenLabs) — pulls only the actionable
    # sections (Exec Summary + Technical Setup + Analyst Take) for a
    # tight ~60-90s spoken brief instead of the full ~2000-word memo.
    from ui.components.chat_panel import render_audio_button, render_chat_panel
    from data.audio_client import extract_audio_summary
    render_audio_button(
        text=extract_audio_summary(analysis["memo"]),
        key=f"memo_{analysis['ticker']}",
        button_label="🔊 Listen to summary (Setup + Take)",
    )

    st.divider()

    # Tool calls summary
    with st.expander(f"🔧 Tool Calls ({len(analysis['tool_calls'])})"):
        for i, tc in enumerate(analysis["tool_calls"], 1):
            st.markdown(f"**{i}. `{tc['tool']}`** — `{tc['input']}`")

    # Export + send-to-Trader actions
    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        st.download_button(
            "📥 Download Memo",
            data=analysis["memo"],
            file_name=f"{analysis['ticker']}_memo_{analysis['generated_at'].strftime('%Y%m%d_%H%M')}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with col2:
        # Add to Trader scan list (defaults to "Top Picks")
        from data.watchlists import (
            DEFAULT_NAME as _WL_DEFAULT,
            add_ticker as _add_ticker,
            load_watchlist as _load_wl,
        )
        already_in = analysis["ticker"] in _load_wl(_WL_DEFAULT)
        if already_in:
            st.caption(f"✓ Already in **{_WL_DEFAULT}**")
        else:
            if st.button(f"➕ Add {analysis['ticker']} to Top Picks",
                         use_container_width=True, type="secondary"):
                _add_ticker(_WL_DEFAULT, analysis["ticker"])
                # Invalidate any cached scan so next Trader visit re-scans
                for k in list(st.session_state.keys()):
                    if k.startswith("scan_"):
                        st.session_state.pop(k, None)
                st.success(
                    f"Added **{analysis['ticker']}** to your **{_WL_DEFAULT}** "
                    f"scan list. Switch to 📈 Trader Mode and click Scan Now "
                    f"to see its Weinstein stage."
                )
                st.rerun()
    with col3:
        st.caption(
            f"Generated: {analysis['generated_at'].strftime('%Y-%m-%d %H:%M UTC')} · "
            f"Specialist: {analysis['category']}"
        )

    # 💬 Chat panel — context-aware (knows the memo + Trader scan)
    st.divider()
    render_chat_panel(
        panel_id=f"research_{analysis['ticker']}",
        title="💬 Ask follow-ups about this memo",
        caption=(
            "I know this memo inline, and I can pull Weinstein stage data for any "
            "ticker on demand. Ask 'is this a good time to buy?' or 'what's the "
            "trade setup?' or 'compare this to XYZ'. I'll answer with audio too."
        ),
        current_ticker=analysis["ticker"],
        current_memo=analysis["memo"],
        placeholder=f"e.g. 'is {analysis['ticker']} a buy right now?'",
        enable_audio=True,
    )

    # ⚠️ Full disclaimer — collapsible, immediately under the memo
    st.divider()
    render_disclaimer(compact=False)


# ---------------------------------------------------------------------------
# Global compact disclaimer footer — always visible, even before any analysis
# ---------------------------------------------------------------------------
render_disclaimer(compact=True)
