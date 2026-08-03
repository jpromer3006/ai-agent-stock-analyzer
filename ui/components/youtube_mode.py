"""
youtube_mode.py - The YouTube News tab (JP, 2026-07-18, advisor feedback).

Customize the Market Desk broadcast before it airs: edit any scene's
narration, set the desk anchor's headshot, render the video locally, and
publish straight to the OROS Market Desk channel WITHOUT sending any email.

Segments are separated by a blank line; each block becomes its own
ElevenLabs call (evergreen blocks stay cached, so keep the teaching text
byte-identical unless you mean to re-record it).
"""

from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
_ASSETS = _ROOT / "ui" / "assets"
_ANCHOR = _ASSETS / "anchor.png"
_ENDCARD = _ASSETS / "endcard_logo.png"

_SCENE_LABELS = {
    "coldopen": "01 · Cold open — Tonight on the Market Desk",
    "market": "02 · The Market Open",
    "topstory": "03 · Top Pick — the chart and the strategy",
    "analysis": "04 · OROS Scan — the trade card (stock + ETF)",
    "book": "05 · The Book — Today's Rotation (IN/OUT)",
    "holdings": "06 · The Portfolio — Actual Holdings",
    "race": "07 · The Race — live vs frozen vs SPY",
    "takeaway": "08 · The Takeaway",
    "signoff": "09 · Disclosure & Credits",
}


def render_youtube_mode() -> None:
    st.subheader("📺 YouTube News Desk")
    st.caption("Edit the script, set the anchor, render, publish. "
               "No emails are sent from this tab — upload only.")

    # ----- The end card (logo / sponsor slot) -----------------------------
    col_img, col_up = st.columns([1, 2])
    with col_img:
        if _ENDCARD.exists():
            st.image(str(_ENDCARD), caption="Current end card", width=240)
        else:
            st.info("No end-card logo yet.")
    with col_up:
        st.markdown("**End-card logo — image only (PNG/JPG)**")
        up = st.file_uploader("The closing logo shown for a few seconds with "
                              "the disclaimer. This is NOT for research "
                              "reports — those upload inside the Script "
                              "assistant after you load the rundown below.",
                              type=["png", "jpg", "jpeg"], key="yt_endcard")
        if up is not None:
            _ASSETS.mkdir(parents=True, exist_ok=True)
            _ENDCARD.write_bytes(up.getvalue())
            st.success("End card saved — the last few seconds of every "
                       "render from now on. Swap it any time (sponsor slot).")
            st.rerun()

    st.divider()

    # ----- Load the day's rundown ----------------------------------------
    if st.button("📋 Load today's rundown (auto-written script)",
                 use_container_width=True):
        with st.spinner("Building today's data + scripts (uses cached "
                        "journals — no rescan)..."):
            from ml.daily_report import build_report_data
            from ml.daily_video import default_scripts
            data = build_report_data()
            st.session_state.yt_data = data
            st.session_state.yt_scripts = default_scripts(data)
            st.session_state.pop("yt_video", None)

    data = st.session_state.get("yt_data")
    scripts = st.session_state.get("yt_scripts")
    if not data or not scripts:
        st.info("Load today's rundown above — then the editable scene "
                "scripts, the 🤖 Script assistant (Claude), and the render "
                "and upload controls appear here.")
        st.stop()

    st.caption("Blank line = new narration segment (cached separately). "
               "Evergreen teaching blocks are paid for once — edit them "
               "only when you mean to re-record.")
    # Apply any assistant draft BEFORE the text areas exist this run —
    # Streamlit forbids touching a widget's state after it renders.
    _pending = st.session_state.pop("yt_pending_draft", None)
    if _pending:
        for _k, _v in _pending.items():
            st.session_state[f"yt_scene_{_k}"] = _v
        st.success(f"Assistant rewrote {len(_pending)} scene(s) — review "
                   "below, then render. Disclosure stays enforced.")

    edited: dict[str, list[str]] = {}
    for key, segs in scripts.items():
        label = _SCENE_LABELS.get(key, key)
        txt = st.text_area(label, value="\n\n".join(segs),
                           height=140, key=f"yt_scene_{key}")
        edited[key] = [b.strip() for b in txt.split("\n\n") if b.strip()]

    # ----- Script assistant (Claude) --------------------------------------
    with st.expander("🤖 Script assistant — draft scenes from a report "
                     "(⬅ upload research PDFs HERE)", expanded=True):
        st.caption("Upload a research report and/or give an instruction; "
                   "Claude rewrites the chosen scenes in the desk's voice. "
                   "The closing disclosure is enforced in code — no edit or "
                   "assistant output can remove it from the broadcast.")
        rep = st.file_uploader("Research report (PDF / MD / TXT) — optional",
                               type=["pdf", "md", "txt"], key="yt_report")
        instr = st.text_area(
            "Instruction",
            value="Rewrite the selected scenes: tighter, more engaging, "
                  "plain spoken English, grounded in the report's facts.",
            height=80, key="yt_instr")
        targets = st.multiselect(
            "Scenes to rewrite", list(scripts.keys()),
            default=[k for k in ("topstory",) if k in scripts],
            format_func=lambda k: _SCENE_LABELS.get(k, k), key="yt_targets")
        if st.button("✍️ Draft with Claude", key="yt_draft") and targets:
            report_text = ""
            if rep is not None:
                try:
                    if rep.name.lower().endswith(".pdf"):
                        import io
                        from pypdf import PdfReader
                        report_text = "\n".join(
                            (p.extract_text() or "")
                            for p in PdfReader(io.BytesIO(rep.getvalue())).pages)
                    else:
                        report_text = rep.getvalue().decode("utf-8", "ignore")
                except Exception as e:
                    st.warning(f"Could not read the report ({e}); "
                               "drafting from instruction only.")
            current = {k: "\n\n".join(edited.get(k) or scripts[k])
                       for k in targets}
            with st.spinner("Claude is writing in the desk's voice..."):
                try:
                    from ui.components.oros_chat import draft_scripts
                    out = draft_scripts(current, instr, report_text)
                    # Park the draft; it is applied at the TOP of the next
                    # run, before the text areas render (Streamlit forbids
                    # writing widget state after instantiation).
                    st.session_state["yt_pending_draft"] = {
                        k: v for k, v in out.items() if k in targets}
                    st.rerun()
                except Exception as e:
                    st.error(f"Assistant unavailable: {type(e).__name__}: {e}")

    st.divider()
    c1, c2 = st.columns(2)

    with c1:
        if st.button("🎬 Render video with this script",
                     type="primary", use_container_width=True):
            with st.spinner("Rendering scenes, narration, and assembly "
                            "(edited segments hit ElevenLabs; unchanged "
                            "ones come from cache)..."):
                from ml.daily_video import generate_video_brief
                path, note = generate_video_brief(
                    data, overrides=edited,
                    anchor=_ANCHOR if _ANCHOR.exists() else None)
            if path is None:
                st.error(note)
            else:
                st.session_state.yt_video = str(path)
                st.success(note)

    video = st.session_state.get("yt_video")
    if video and Path(video).exists():
        st.video(video)
        with c2:
            privacy = st.selectbox("Upload visibility",
                                   ["unlisted", "public"], index=0,
                                   help="Test uploads go unlisted; flip to "
                                        "public for the real episode.")
            if st.button("⬆️ Upload to OROS Market Desk (no email)",
                         use_container_width=True):
                with st.spinner("Publishing to YouTube..."):
                    os.environ["OROS_YT_PRIVACY"] = privacy
                    from ml.publish_youtube import publish_episode, episode_title
                    url, note = publish_episode(video, episode_title(data))
                if url:
                    st.success(f"Published ({privacy}): {url}")
                    st.markdown(f"[Watch it here]({url})")
                else:
                    st.error(note)

    # One-click pipeline (JP, 2026-07-30): render THEN publish in a single
    # press — the full desk workflow, live on stage. The two-step buttons
    # above remain for preview-first workflows.
    st.divider()
    st.markdown("**🚀 One click — render & publish to the channel**")
    _oc1, _oc2 = st.columns([1, 2])
    with _oc1:
        one_privacy = st.selectbox(
            "Visibility", ["unlisted", "public"], index=0,
            key="yt_oneclick_privacy",
            help="unlisted = private link to share · public = on the channel")
    with _oc2:
        st.caption("Renders this script (edited scenes get fresh voices, "
                   "unchanged ones come from cache — typically 2-5 min), "
                   "then uploads straight to OROS Market Desk. The closing "
                   "disclosure is enforced in code either way.")
    if st.button("🚀 Render & publish in one click", type="primary",
                 use_container_width=True, key="yt_oneclick"):
        with st.spinner("Rendering — scenes, three voices, assembly..."):
            from ml.daily_video import generate_video_brief
            path, note = generate_video_brief(
                data, overrides=edited,
                anchor=_ANCHOR if _ANCHOR.exists() else None)
        if path is None:
            st.error(note)
        else:
            st.session_state.yt_video = str(path)
            with st.spinner("Uploading to OROS Market Desk..."):
                os.environ["OROS_YT_PRIVACY"] = one_privacy
                from ml.publish_youtube import publish_episode, episode_title
                url, pnote = publish_episode(str(path), episode_title(data))
            if url:
                st.success(f"Live on the channel ({one_privacy}): {url}")
                st.markdown(f"[▶ Watch it here]({url})")
                st.video(str(path))
            else:
                st.error(f"Rendered but upload failed: {pnote} — the MP4 is "
                         "saved; use the two-step Upload button above to retry.")

    st.caption("Simulated paper trading · OROS proposes and journals only · "
               "educational, not financial advice")
