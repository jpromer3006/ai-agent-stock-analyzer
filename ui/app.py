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
from data.tickers import UNIVERSE, StockCategory, tickers_by_category, category_counts


# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Ai-Agent Stock Analyzer",
    page_icon="🤖",
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
    st.session_state.app_mode = "research"
# Migrate any old 'assistant' mode state to research
if st.session_state.app_mode == "assistant":
    st.session_state.app_mode = "research"


# ---------------------------------------------------------------------------
# Top banner: Mode toggle
# ---------------------------------------------------------------------------
st.markdown(
    "<div style='background:linear-gradient(90deg,#1a1a2e,#16213e);"
    "padding:14px 20px;border-radius:8px;margin-bottom:12px'>"
    "<div style='display:flex;justify-content:space-between;align-items:center'>"
    "<div><span style='color:#e6edf3;font-size:22px;font-weight:700'>"
    "🤖 Ai-Agent Stock Analyzer</span>"
    "<span style='color:#8b949e;margin-left:12px;font-size:13px'>"
    "Fordham Applied Finance Project · Lecture 9</span></div></div></div>",
    unsafe_allow_html=True,
)

mode_col1, mode_col2 = st.columns(2)
with mode_col1:
    research_active = st.session_state.app_mode == "research"
    label = "🔬 Research  ✓" if research_active else "🔬 Research"
    if st.button(label, key="mode_research", use_container_width=True,
                 type="primary" if research_active else "secondary"):
        st.session_state.app_mode = "research"
        st.rerun()
with mode_col2:
    trader_active = st.session_state.app_mode == "trader"
    label = "📈 Trader  ✓" if trader_active else "📈 Trader"
    if st.button(label, key="mode_trader", use_container_width=True,
                 type="primary" if trader_active else "secondary"):
        st.session_state.app_mode = "trader"
        st.rerun()

st.markdown("")


# ═══════════════════════════════════════════════════════════════════════════
# TRADER MODE
# ═══════════════════════════════════════════════════════════════════════════
if st.session_state.app_mode == "trader":
    from ui.components.trader_mode import render_trader_mode
    render_trader_mode()
    st.stop()


# ═══════════════════════════════════════════════════════════════════════════
# RESEARCH MODE (existing flow — unchanged)
# ═══════════════════════════════════════════════════════════════════════════

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("🔬 Research Mode")
    st.caption(f"Universe: {len(UNIVERSE)} tickers across {len(category_counts())} specialists")

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
    run_clicked = st.button("🔍 Analyze", type="primary", use_container_width=True)

# Resolve ticker
ticker = None
if run_clicked and search_input.strip():
    ticker = search_input.strip().upper()
    st.session_state.selected_ticker = ticker
elif st.session_state.selected_ticker:
    ticker = st.session_state.selected_ticker


# ---------------------------------------------------------------------------
# Landing state
# ---------------------------------------------------------------------------
if not ticker:
    st.info(
        "👆 Type any ticker and click **Analyze** for a deep memo with SEC "
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

col_run1, col_run2 = st.columns([1, 5])
with col_run1:
    if st.button("▶️ Run Agent", type="primary"):
        st.session_state.analyses.pop(cache_key, None)
        st.rerun()
with col_run2:
    if cache_key in st.session_state.analyses:
        st.caption(f"✓ Analysis cached. Click **Run Agent** to re-run.")

# If not cached, run the agent with streaming trace
if cache_key not in st.session_state.analyses:
    trace_container = st.container()
    memo_container = st.container()

    with trace_container:
        st.markdown("### 🔬 Agent Trace")
        trace_expander = st.expander("Live tool calls", expanded=True)

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
    st.markdown("### 📄 Research Memo")
    st.markdown(analysis["memo"])

    # 🔊 Listen to the memo (ElevenLabs)
    from ui.components.chat_panel import render_audio_button, render_chat_panel
    render_audio_button(
        text=analysis["memo"],
        key=f"memo_{analysis['ticker']}",
        button_label="🔊 Listen to this memo",
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
