"""
research_dossier.py — The five-section Research dossier.

Replaces the legacy memo-focused Research view. For a single ticker,
renders the full Weinstein decision dossier:

    Section 1   Stage header (carried from the Trader scan)
    Section 2   Chapter 4 resistance panel + 10-year chart
    Section 3   Chapter 3 calendar heatmap
    Section 4a  Sector specialist (fundamentals voice, optional/on-demand)
    Section 4b  Chartist Strategist (technical/timing voice, always-on)
    Section 5   Decision panel anchored by the Setup Confidence Score

The sector specialist section runs on-demand only (LLM tool-use is
expensive). The other sections are fast deterministic computations on
data that is already cached or live-fetched in under a second.
"""

from __future__ import annotations

from datetime import datetime

import plotly.graph_objects as go
import streamlit as st

from ml.stage_analyzer import analyze_stage, StageResult
from ml.resistance_analyzer import analyze_resistance, ResistanceResult
from agents.specialists.chartist_strategist import analyze_chartist
from ui.components.calendar_heatmap import render_calendar_heatmap


# ---------------------------------------------------------------------------
# Stage color constants (same as trader_mode for visual consistency)
# ---------------------------------------------------------------------------
STAGE_COLOR = {1: "#6c757d", 2: "#00c853", 3: "#ffc107", 4: "#ff1744"}
STAGE_ICON = {1: "◯", 2: "✓", 3: "⚠", 4: "✗"}


# ---------------------------------------------------------------------------
# Section 1: stage header
# ---------------------------------------------------------------------------

def _stage_maturity_label(stage_result: StageResult) -> str:
    """Human-readable maturity tag for the stage card. For Stage 2 this
    turns a bare "Stage 2" into "Stage 2 · Late (64 wks)" so a mature run
    paired with an AVOID action reads as a coherent story instead of a
    contradiction. Returns "" when there is nothing useful to add."""
    if stage_result.stage != 2:
        return ""
    phase = {
        "fresh": "Fresh",
        "watching": "Watching",
        "extended": "Late",
    }.get(stage_result.setup_type, "")
    weeks = stage_result.weeks_held
    if phase and weeks > 0:
        return f"{phase} · {weeks} wks in"
    if phase:
        return phase
    if weeks > 0:
        return f"{weeks} wks in"
    return ""


def _render_section_1_stage_header(ticker: str, stage_result: StageResult) -> None:
    color = STAGE_COLOR.get(stage_result.stage, "#6c757d")
    icon = STAGE_ICON.get(stage_result.stage, "?")
    maturity = _stage_maturity_label(stage_result)
    maturity_html = (
        f"<div style='font-size:12px;opacity:0.85;margin-top:4px;"
        f"font-weight:600'>{maturity}</div>" if maturity else ""
    )
    c1, c2, c3 = st.columns([2, 2, 3])
    with c1:
        st.markdown(
            f"<div style='background:{color};color:white;padding:18px;"
            f"border-radius:8px;text-align:center'>"
            f"<div style='font-size:12px;opacity:0.9;letter-spacing:1px'>STAGE</div>"
            f"<div style='font-size:40px;font-weight:700'>{stage_result.stage} {icon}</div>"
            f"<div style='font-size:15px'>{stage_result.stage_name}</div>"
            f"{maturity_html}"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f"<div style='padding:18px;border:1px solid #30363d;"
            f"border-radius:8px;text-align:center'>"
            f"<div style='font-size:12px;color:#8b949e'>ENTRY TIMING</div>"
            f"<div style='font-size:32px;font-weight:700;color:{color}'>"
            f"{stage_result.action}</div>"
            f"<div style='font-size:13px;color:#8b949e'>Weinstein entry call · "
            f"as of {stage_result.as_of_date}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f"<div style='padding:18px;border:1px solid #30363d;"
            f"border-radius:8px;text-align:center'>"
            f"<div style='font-size:12px;color:#8b949e'>BULL PROBABILITY</div>"
            f"<div style='font-size:32px;font-weight:700;color:{color}'>"
            f"{stage_result.bull_probability:.0%}</div>"
            f"<div style='font-size:13px;color:#8b949e'>"
            f"confidence {stage_result.confidence:.0%}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    # Compact metrics strip
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Price", f"${stage_result.last_close:,.2f}")
    m2.metric("30W MA", f"${stage_result.ma_30w:,.2f}")
    m3.metric("vs 30W MA", f"{stage_result.pct_above_ma:+.1%}")
    m4.metric("Mansfield RS", f"{stage_result.mansfield_rs:+.1f}")


# ---------------------------------------------------------------------------
# Section 2: Chapter 4 resistance panel + 10y chart
# ---------------------------------------------------------------------------

def _render_section_2_resistance(ticker: str, resistance: ResistanceResult) -> None:
    if resistance.error:
        st.warning(f"Resistance picture unavailable: {resistance.error}")
        return

    grade_color = {
        "A+": "#3fb950", "A": "#56d364",
        "B": "#ffc107", "C": "#ff7b1a", "n/a": "#6c757d",
    }.get(resistance.grade, "#6c757d")

    # Header card
    st.markdown(
        f"<div style='background:#0d2818;border:2px solid {grade_color};"
        f"border-radius:10px;padding:16px;margin-bottom:10px'>"
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center'>"
        f"<div><div style='font-size:11px;color:#8b949e;letter-spacing:1px'>"
        f"CHAPTER 4 — RESISTANCE GRADE</div>"
        f"<div style='font-size:36px;font-weight:700;color:{grade_color};"
        f"margin-top:4px'>{resistance.grade}</div></div>"
        f"<div style='text-align:right'>"
        f"<div style='font-size:11px;color:#8b949e'>10Y HIGH</div>"
        f"<div style='font-size:20px;font-weight:600;color:white'>"
        f"${resistance.ten_year_high or 0:.2f}</div></div></div>"
        f"<div style='color:#c9d1d9;margin-top:10px;font-size:14px;"
        f"line-height:1.5'>{resistance.grade_narrative}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # 10-year resistance chart
    try:
        import yfinance as yf
        weekly = yf.Ticker(ticker).history(period="10y", interval="1wk", auto_adjust=True)
        if weekly is not None and not weekly.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=weekly.index, y=weekly["Close"], mode="lines",
                name="Weekly close", line=dict(color="#4a90e2", width=1.5),
            ))
            # Draw the 10y high as a horizontal line
            if resistance.ten_year_high:
                fig.add_hline(
                    y=resistance.ten_year_high,
                    line_dash="dash", line_color="#8b949e",
                    annotation_text=f"10y high ${resistance.ten_year_high:.2f}",
                    annotation_position="top right",
                )
            # Draw each resistance zone as a faint horizontal band
            for z in resistance.zones[:8]:
                fig.add_hline(
                    y=z.price,
                    line_color=grade_color,
                    line_width=1,
                    opacity=min(0.8, 0.3 + z.strength / 50),
                    annotation_text=f"R ${z.price:.0f} ({z.touches}x)",
                    annotation_position="right",
                    annotation_font_size=10,
                )
            # Virgin territory tint above current price
            if resistance.virgin_territory and resistance.ten_year_high:
                fig.add_hrect(
                    y0=resistance.ten_year_high,
                    y1=resistance.ten_year_high * 1.2,
                    fillcolor="#3fb950", opacity=0.10, line_width=0,
                    annotation_text="VIRGIN TERRITORY",
                    annotation_position="top left",
                    annotation_font_color="#3fb950",
                )
            fig.update_layout(
                title=f"{ticker} — 10-year weekly chart with overhead supply zones",
                height=420,
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#e6edf3",
                margin=dict(l=10, r=10, t=50, b=10),
                showlegend=False,
            )
            fig.update_xaxes(showgrid=False, fixedrange=True)
            fig.update_yaxes(showgrid=True, gridcolor="#30363d",
                             title_text="Price ($)", fixedrange=True)
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False, "scrollZoom": False})
    except Exception as exc:
        st.caption(f"10y chart unavailable: {exc}")

    # Zone summary table
    if resistance.zones:
        with st.expander(
            f"📊 Show all {len(resistance.zones)} resistance zones", expanded=False
        ):
            rows = []
            for z in resistance.zones:
                rows.append({
                    "Price": f"${z.price:.2f}",
                    "Touches": z.touches,
                    "Age (yrs)": f"{z.age_years:.1f}",
                    "Strength": f"{z.strength:.2f}",
                })
            import pandas as pd
            st.dataframe(pd.DataFrame(rows), hide_index=True,
                          use_container_width=True)


# ---------------------------------------------------------------------------
# Section 4b: Chartist Strategist + Setup Confidence
# ---------------------------------------------------------------------------

def _render_section_4b_chartist(chartist_output) -> None:
    o = chartist_output
    if o.error:
        st.warning(f"Chartist Strategist unavailable: {o.error}")
        return

    # Setup Confidence Score is the headline
    band_color = o.confidence_color or "#ffc107"
    cap_note = ""
    if o.caps_applied:
        cap_labels = {
            "extended": "Late Stage 2 (Ch. 5)",
            "spy_stage4": "Bear Market (Ch. 8)",
        }
        capped = ", ".join(cap_labels.get(c, c) for c in o.caps_applied)
        cap_note = (
            f"<div style='font-size:11px;color:#ffc107;margin-top:4px'>"
            f"⚠ Capped by: {capped}</div>"
        )

    st.markdown(
        f"<div style='background:#0d2138;border:2px solid {band_color};"
        f"border-radius:10px;padding:18px;margin-bottom:10px'>"
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center'>"
        f"<div><div style='font-size:11px;color:#8b949e;letter-spacing:1.5px'>"
        f"SETUP CONFIDENCE SCORE</div>"
        f"<div style='font-size:48px;font-weight:700;color:{band_color};"
        f"margin-top:2px'>{o.confidence_score:.0f}%</div>"
        f"<div style='font-size:16px;color:{band_color};font-weight:600'>"
        f"{o.confidence_band}</div>"
        f"{cap_note}"
        f"</div>"
        f"<div style='text-align:right;color:#8b949e;font-size:11px'>"
        f"WEIGHTED ACROSS 6 SIGNALS<br>"
        f"WITH CHAPTER 5 & 8 CAPS"
        f"</div></div>"
        f"<div style='color:#c9d1d9;margin-top:14px;font-size:14px;"
        f"line-height:1.6'>{o.confidence_rationale}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # Component breakdown — small pills
    if o.component_scores:
        pretty = {
            "ceiling":      "Ceiling",
            "weather":      "Market Weather",
            "gates":        "Vol + RS",
            "fundamentals": "Fundamentals",
            "sector_rs":    "Sector RS",
            "calendar":     "Calendar",
        }
        chip_html = []
        for k, v in o.component_scores.items():
            if v >= 70:
                c = "#3fb950"
            elif v >= 50:
                c = "#ffc107"
            else:
                c = "#f85149"
            chip_html.append(
                f"<span style='background:{c};color:white;padding:4px 10px;"
                f"border-radius:12px;margin-right:6px;font-size:11px;"
                f"font-weight:600'>{pretty.get(k, k)} {v:.0f}</span>"
            )
        st.markdown(
            "<div style='margin-bottom:14px'>" + "".join(chip_html) + "</div>",
            unsafe_allow_html=True,
        )

    # The five plain-language fields
    st.markdown("**📖 The Story**")
    st.markdown(o.the_story)
    st.markdown("")

    f_cols = st.columns(2)
    with f_cols[0]:
        st.markdown("**📍 Where In Cycle**")
        st.markdown(o.where_in_cycle)
        if o.calendar_watch:
            st.markdown("**📅 Calendar Watch**")
            st.markdown(o.calendar_watch)
    with f_cols[1]:
        st.markdown("**🌤 Market Weather**")
        st.markdown(o.market_weather)
        st.markdown("**🧱 Ceiling Watch**")
        st.markdown(o.ceiling_watch)

    # ── Sentiment Analyst sub-card (Phase 3, 17th specialist) ────────
    # Renders only if the Sentiment Analyst ran successfully. Includes
    # a divergence callout when sentiment contradicts the technical
    # stage (e.g., Stage 4 stock with Bullish sentiment).
    if o.sentiment_score is not None:
        st.markdown("")
        sentiment_color = o.sentiment_color or "#ffc107"
        divergence_chip = ""
        if o.sentiment_divergence:
            divergence_chip = (
                "<span style='background:#3d2a1a;color:#ffc107;padding:2px 8px;"
                "border-radius:10px;font-size:11px;margin-left:8px;"
                "font-weight:600'>⚠ DIVERGES FROM TECHNICAL STAGE</span>"
            )
        st.markdown(
            f"<div style='background:#1a2030;border:1px solid {sentiment_color};"
            f"border-radius:8px;padding:14px;margin-top:8px'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center'>"
            f"<div style='font-size:11px;color:#8b949e;letter-spacing:1px'>"
            f"🗞 MARKET SENTIMENT (17th specialist)</div>"
            f"<div style='font-size:22px;font-weight:700;color:{sentiment_color}'>"
            f"{o.sentiment_score:.0f}/100 · {o.sentiment_band}{divergence_chip}"
            f"</div></div>"
            f"<div style='color:#c9d1d9;margin-top:8px;font-size:13px;"
            f"line-height:1.5'>{o.sentiment_narrative}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Section 4a: Sector specialist (on-demand)
# ---------------------------------------------------------------------------

def _render_section_4a_specialist(ticker: str) -> None:
    """Sector specialist — the fundamentals voice. Runs the FULL agentic
    memo in place (the same flow the legacy 'classic memo' tab used) so
    the dossier is the single home for research.

    On-demand only (Claude tool-use is ~30-90s). The trace streams into a
    collapsed expander; the memo renders with numbered sources, an
    ElevenLabs narration button, and a concise tool-call provenance trail.

    NOTE on the prior bug: this section used to call ``run_agent(ticker)``
    and store the return value directly. ``run_agent`` is a *generator*
    that yields trace events — calling it never executes the loop, so the
    memo never rendered. We now consume the generator and pull the final
    ``done`` event's memo, exactly like the classic flow did.
    """
    state_key = f"specialist_memo_{ticker}"
    cached = st.session_state.get(state_key)

    # ── Run button (shown until a memo is cached) ───────────────────────
    if not cached:
        st.info(
            f"💼 Generates the fundamentals memo for {ticker} via SEC EDGAR "
            f"XBRL, RAG over the 10-K, FRED macro, and the Weinstein book "
            f"lookup. ~30-90s of Claude tool-use. Runs only when you click."
        )
        if not st.button(
            f"🤖 Run sector specialist on {ticker}",
            key=f"run_specialist_{ticker}",
            type="primary",
        ):
            return

        from agents.orchestrator import run_agent

        tool_calls: list[dict] = []
        memo_text = ""
        error_msg = None

        # Trace collapsed by default — concise, but expandable to watch the
        # specialists work and see exactly which data sources were hit.
        with st.expander("🤖 Agents working — live tool calls", expanded=False):
            status = st.empty()
            step = 0
            try:
                for ev in run_agent(ticker):
                    et = ev.get("type")
                    if et == "classified":
                        status.info(f"🎯 {ev['category']} → {ev['agent_name']}")
                    elif et == "tool_call":
                        step += 1
                        args = ", ".join(f"{k}={v!r}" for k, v in ev["input"].items())
                        st.markdown(f"**{step}.** 🔧 `{ev['tool']}({args})`")
                        tool_calls.append({"tool": ev["tool"], "input": ev["input"]})
                    elif et == "tool_result":
                        st.caption(f"    ← {ev['result_length']} chars returned")
                    elif et == "done":
                        memo_text = ev["memo"]
                        status.success(
                            f"✅ Done in {ev['steps']} steps · {len(tool_calls)} tools called"
                        )
                    elif et == "error":
                        error_msg = ev["message"]
                        status.error(f"❌ {error_msg}")
                        break
            except Exception as exc:
                error_msg = str(exc)
                status.error(f"❌ {exc}")

        if error_msg or not memo_text:
            st.error(
                f"Specialist did not produce a memo: {error_msg or 'no output'}"
            )
            return

        # Stacked inline [Source: X] citations → numbered footnotes + one
        # "## Sources" list (idempotent). The concise way to keep exactly
        # how the agents show their sources without crowding the prose.
        from agents.orchestrator import _to_footnotes
        memo_text = _to_footnotes(memo_text)

        st.session_state[state_key] = {"memo": memo_text, "tool_calls": tool_calls}
        st.rerun()

    # ── Render cached memo + audio + provenance ─────────────────────────
    memo_text = cached["memo"]
    tool_calls = cached.get("tool_calls", [])

    st.markdown(memo_text)

    # 🔊 ElevenLabs narration of the actionable layer only (Exec Summary +
    # Technical Setup + Analyst Take) — ~80% cheaper than the full memo.
    # Audio is optional: never block the memo if TTS is unavailable.
    try:
        from ui.components.chat_panel import render_audio_button
        from data.audio_client import extract_audio_summary
        render_audio_button(
            text=extract_audio_summary(memo_text),
            key=f"dossier_memo_{ticker}",
            button_label="🔊 Listen (Setup + Take)",
        )
    except Exception:
        pass

    # Concise provenance: numbered Sources are already inline above; this
    # collapsed trail shows WHICH data tools the agent actually hit.
    if tool_calls:
        with st.expander(
            f"🔧 Data sources — {len(tool_calls)} tool calls", expanded=False
        ):
            for i, tc in enumerate(tool_calls, 1):
                st.markdown(f"**{i}. `{tc['tool']}`** — `{tc['input']}`")

    c1, c2, c3 = st.columns(3)
    with c1:
        # PDF is the primary deliverable. Falls back gracefully if the
        # optional PDF libs are unavailable in the environment.
        from ui.components.memo_pdf import memo_to_pdf_bytes
        pdf_bytes = memo_to_pdf_bytes(memo_text, ticker)
        if pdf_bytes:
            st.download_button(
                "📄 Download PDF",
                data=pdf_bytes,
                file_name=f"{ticker}_research_memo.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"dl_pdf_{ticker}",
            )
        else:
            st.button(
                "📄 PDF unavailable",
                disabled=True,
                use_container_width=True,
                key=f"dl_pdf_disabled_{ticker}",
                help="Install the 'markdown' and 'xhtml2pdf' packages to enable PDF export.",
            )
    with c2:
        st.download_button(
            "📥 Markdown",
            data=memo_text,
            file_name=f"{ticker}_memo.md",
            mime="text/markdown",
            use_container_width=True,
            key=f"dl_memo_{ticker}",
        )
    with c3:
        if st.button(
            "♻️ Regenerate memo",
            key=f"rerun_specialist_{ticker}",
            use_container_width=True,
        ):
            del st.session_state[state_key]
            st.rerun()


# ---------------------------------------------------------------------------
# Section 5: Decision panel
# ---------------------------------------------------------------------------

def _render_section_5_decision(ticker: str, stage_result: StageResult,
                                chartist_output) -> None:
    """Final decision summary that pulls the score, the action, the
    trade setup, and the headline guidance into one block."""
    band_color = chartist_output.confidence_color or "#ffc107"
    action_color = STAGE_COLOR.get(stage_result.stage, "#6c757d")

    ts = stage_result.trade_setup
    has_trade = ts is not None and ts.applicable

    # Headline row
    c1, c2 = st.columns([2, 3])
    with c1:
        st.markdown(
            f"<div style='background:#161b22;border:2px solid {band_color};"
            f"border-radius:10px;padding:18px;text-align:center'>"
            f"<div style='font-size:11px;color:#8b949e;letter-spacing:1.5px'>"
            f"DOSSIER VERDICT</div>"
            f"<div style='font-size:32px;font-weight:700;color:{band_color};"
            f"margin-top:4px'>{chartist_output.confidence_band}</div>"
            f"<div style='font-size:13px;color:#c9d1d9;margin-top:4px'>"
            f"Confidence {chartist_output.confidence_score:.0f}%</div>"
            f"<div style='font-size:11px;color:#8b949e;margin-top:8px'>"
            f"Weinstein action label: "
            f"<span style='color:{action_color};font-weight:700'>"
            f"{stage_result.action}</span></div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with c2:
        if has_trade:
            st.markdown(
                f"<div style='background:#0e1117;border:1px solid {action_color};"
                f"border-radius:10px;padding:18px;height:100%'>"
                f"<div style='font-size:11px;color:#8b949e;letter-spacing:1.5px'>"
                f"TRADE SETUP</div>"
                f"<div style='color:#c9d1d9;margin-top:8px;line-height:1.6'>"
                f"<b>Entry</b>: ${ts.entry_price:.2f} ({ts.entry_type})<br>"
                f"<b>Stop</b>: ${ts.stop_loss:.2f} (30-week MA invalidator)<br>"
                f"<b>Target 1</b>: ${ts.target_1:.2f}<br>"
                f"<b>R/R</b>: {ts.risk_reward_ratio:.2f}:1"
                f"</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.info(
                "No actionable Weinstein trade setup for this stage. "
                "Setups apply to Stage 2 (long) and Stage 4 (short) only."
            )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_research_dossier(ticker: str) -> None:
    """Top-level dossier renderer. Call this from app.py when the user
    selects a ticker in Research mode or clicks "Open Research dossier"
    from the Trader tab.

    The five sections appear in order with reasonable spinners so the
    user sees progress.
    """
    ticker = ticker.upper().strip()
    if not ticker:
        st.warning("Enter a ticker to generate a dossier.")
        return

    st.markdown(
        f"## 🔬 Research Dossier — **{ticker}**"
    )
    st.caption(
        "Five-section Weinstein decision dossier. Sections 1, 2, 3, and 4b "
        "render in under a second. Section 4a (sector specialist) runs "
        "only on demand."
    )

    # Run analyses
    with st.spinner(f"Running Weinstein stage analysis on {ticker}..."):
        stage_result = analyze_stage(ticker)
    if stage_result.error:
        st.error(f"Stage analysis failed: {stage_result.error}")
        return

    with st.spinner("Reading the 10-year resistance picture..."):
        resistance = analyze_resistance(ticker)

    with st.spinner("Synthesizing the Chartist Strategist..."):
        chartist_output = analyze_chartist(ticker, stage_result=stage_result)

    # ── Section 1: Stage header ─────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 1️⃣ Stage Header")
    _render_section_1_stage_header(ticker, stage_result)

    # ── Section 2: Chapter 4 resistance ─────────────────────────────────
    st.markdown("---")
    st.markdown("### 2️⃣ Chapter 4 — Overhead Resistance")
    _render_section_2_resistance(ticker, resistance)

    # ── Section 3: Chapter 3 calendar ───────────────────────────────────
    st.markdown("---")
    st.markdown("### 3️⃣ Chapter 3 — Calendar Context")
    render_calendar_heatmap(section_header=False)

    # ── Section 4: Two analysts side by side ────────────────────────────
    st.markdown("---")
    st.markdown("### 4️⃣ Research")
    st.caption(
        "Two research agents read the same stock from different angles: 4a "
        "covers the business (SEC filings, FRED macro), 4b covers the chart "
        "(stage, timing, relative strength)."
    )
    voice_cols = st.columns([1, 1])
    with voice_cols[0]:
        st.markdown("#### 4a · 💼 Sector Specialist (Fundamentals)")
        _render_section_4a_specialist(ticker)
    with voice_cols[1]:
        st.markdown("#### 4b · 📈 Chartist Strategist (Technicals & Timing)")
        _render_section_4b_chartist(chartist_output)

    # ── Section 5: Decision panel ───────────────────────────────────────
    st.markdown("---")
    st.markdown("### 5️⃣ Decision Panel")
    _render_section_5_decision(ticker, stage_result, chartist_output)
