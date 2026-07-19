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
    "topstory": "03 · Top Story / Chart of the Day",
    "book": "04 · Business Desk — the book",
    "movers": "05 · Movers in the Book",
    "race": "06 · Sports Desk — The Race",
    "analysis": "07 · Market Analysis — the OROS 500",
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
        up = st.file_uploader("End-card logo (PNG/JPG) — closes every "
                              "episode with the disclaimer",
                              type=["png", "jpg", "jpeg"])
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
        st.stop()

    st.caption("Blank line = new narration segment (cached separately). "
               "Evergreen teaching blocks are paid for once — edit them "
               "only when you mean to re-record.")
    edited: dict[str, list[str]] = {}
    for key, segs in scripts.items():
        label = _SCENE_LABELS.get(key, key)
        txt = st.text_area(label, value="\n\n".join(segs),
                           height=140, key=f"yt_scene_{key}")
        edited[key] = [b.strip() for b in txt.split("\n\n") if b.strip()]

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

    st.caption("Simulated paper trading · OROS proposes and journals only · "
               "educational, not financial advice")
