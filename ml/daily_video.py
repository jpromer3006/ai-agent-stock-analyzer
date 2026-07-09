"""
daily_video.py - The OROS video brief (JP's ask, 2026-07-09).

A narrated MP4 walkthrough of the day's report: the summary, then each
Weinstein Top Pick chart with the trade plan explained in plain language
(what the buy-stop, stop, and target actually mean), then the capstone
scoreline. Scenes are the same matplotlib figures as the PDF; the voice is
ElevenLabs (cached by script hash); ffmpeg stitches scenes into one video.

Skips cleanly when ffmpeg or the ElevenLabs key is absent (OROS_VIDEO=0
disables). When the video is produced, it REPLACES the audio-only brief in
the email. Educational, not financial advice; OROS never trades.

    PYTHONPATH=. python3 ml/daily_video.py     # build from today's data
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_REPORT_DIR = _ROOT / "data" / "journal" / "reports"
_SIZE = "1280x720"


# ---------------------------------------------------------------------------
# Scene cards (matplotlib figures -> PNG)
# ---------------------------------------------------------------------------
def _card(plt, title: str, lines: list[tuple[str, dict]]):
    fig = plt.figure(figsize=(12.8, 7.2))
    fig.patch.set_facecolor("#0d1117")
    fig.text(0.06, 0.88, title, fontsize=26, fontweight="bold", color="#e6edf3")
    y = 0.76
    for text, style in lines:
        fig.text(0.06, y, text, fontsize=style.get("size", 15),
                 color=style.get("color", "#c9d1d9"),
                 fontweight=style.get("weight", "normal"), family="monospace")
        y -= style.get("dy", 0.075)
    fig.text(0.06, 0.05, "OROS proposes and journals only — it never trades. "
             "Educational, not financial advice.", fontsize=9, color="#8b949e")
    return fig


def _summary_scene(plt, data: dict):
    pnl = data.get("pnl") or {}
    acts = data.get("actions") or []
    mb = data.get("my_book") or {}
    lines = [
        (f"{data.get('date', '')}", {"size": 15, "color": "#8b949e"}),
        (f"Market regime: {(data.get('regime') or '?').upper()}",
         {"size": 19, "weight": "bold",
          "color": "#3fb950" if data.get("regime") == "bull" else "#f0883e"}),
        (f"Paper book today: ${pnl.get('total_day_usd', 0):+,.0f}", {}),
    ]
    if mb.get("rows"):
        lines.append((f"Your real book today: ${mb.get('day_usd', 0):+,.0f} "
                      f"(equity ${mb.get('equity', 0):,.0f})", {}))
    lines.append((f"Today's actions: {len(acts) or 'none — let the winners run'}",
                  {"color": "#3fb950" if not acts else "#f0883e"}))
    return _card(plt, "OROS Daily Brief", lines)


def _capstone_scene(plt, cs: dict):
    lv = cs.get("live") or {}
    lines = [
        (f"Since {cs['start_date']} — three books, same $100,000",
         {"size": 14, "color": "#8b949e"}),
        (f"First Portfolio (frozen):  ${cs['value']:,.0f}  ({cs['return_pct']:+.2f}%)",
         {"weight": "bold",
          "color": "#3fb950" if cs["return_pct"] >= 0 else "#f85149"}),
    ]
    if lv:
        lines.append((f"Live OROS book (chained):  ${lv['value']:,.0f}  "
                      f"({lv['return_pct']:+.2f}%)",
                      {"weight": "bold",
                       "color": "#3fb950" if lv["return_pct"] >= 0 else "#f85149"}))
    if cs.get("spy_value"):
        lines.append((f"SPY benchmark:             ${cs['spy_value']:,.0f}  "
                      f"({cs['spy_return_pct']:+.2f}%)", {"color": "#8b949e"}))
    lines.append(("Frozen book: buy-and-hold since 07/06. Live book: chained",
                  {"size": 12, "color": "#8b949e", "dy": 0.05}))
    lines.append(("daily returns, append-only. Honest by construction.",
                  {"size": 12, "color": "#8b949e"}))
    return _card(plt, "Capstone Comparison (to 07/29)", lines)


# ---------------------------------------------------------------------------
# Narration scripts
# ---------------------------------------------------------------------------
def _summary_script(data: dict) -> str:
    pnl = data.get("pnl") or {}
    acts = data.get("actions") or []
    mb = data.get("my_book") or {}
    s = [f"Good morning JP. Here is your OROS video brief for {data.get('date', 'today')}."]
    regime = (data.get("regime") or "").upper()
    s.append("The market is in a healthy uptrend." if regime == "BULL"
             else f"The market regime is {regime.lower() or 'uncertain'}.")
    d = pnl.get("total_day_usd", 0.0)
    s.append(f"The paper book is {'up' if d >= 0 else 'down'} {abs(d):,.0f} dollars today.")
    if mb.get("rows"):
        rd = mb.get("day_usd", 0.0)
        s.append(f"Your real book is {'up' if rd >= 0 else 'down'} {abs(rd):,.0f} dollars.")
    s.append(f"There {'is nothing on the checklist today.' if not acts else 'are ' + str(len(acts)) + ' checklist items in the report.'}")
    return " ".join(s)


def _pick_script(p: dict) -> str:
    kind = "fund" if p.get("is_etf") else "stock"
    return (
        f"Now the chart for {p['ticker']}, today's top {kind} pick, and how to read "
        f"the plan. The blue line is the weekly price; the gold line is the thirty "
        f"week average, and it is rising, which is what a healthy trend looks like. "
        f"The green dashed line at {p['buy_stop']:,.2f} is the buy trigger. This is "
        f"not a price to chase; you only buy if the market itself pushes up through "
        f"that line, proving real demand. If it never gets there, you never buy, and "
        f"that is the plan working, not failing. "
        f"The red dashed line at {p['stop_loss']:,.2f} is the exit if you own it: it "
        f"matters on a weekly close below, not a scary afternoon dip. "
        f"The purple line at {p['target']:,.2f} is the objective. It is not an "
        f"automatic sell; its job was before the trade, proving the potential reward "
        f"is {p['risk_reward']:.1f} times the risk. As price approaches it, take "
        f"some profit and let the rising stop protect the rest. "
        f"Sized correctly, at {p['shares_1pct']} shares, being wrong costs about one "
        f"percent of the account. Buy on proof, exit by rule, size to survive."
    )


def _capstone_script(cs: dict) -> str:
    lv = cs.get("live") or {}
    s = [f"Finally, the capstone scoreboard, three books, the same one hundred "
         f"thousand dollars, since July sixth. The frozen first portfolio stands at "
         f"{cs['value']:,.0f} dollars, {'up' if cs['return_pct'] >= 0 else 'down'} "
         f"{abs(cs['return_pct']):.2f} percent."]
    if lv:
        s.append(f"The live, evolving book stands at {lv['value']:,.0f}.")
    if cs.get("spy_value"):
        s.append(f"And the S and P index fund, {cs['spy_value']:,.0f}.")
    s.append("Same start, same dollars, nothing restated. That is the honest race. "
             "This has been your OROS brief.")
    return " ".join(s)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def _scene_to_clip(png: Path, mp3: Path, out: Path) -> bool:
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(png), "-i", str(mp3),
           "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
           "-vf", f"scale={_SIZE.replace('x', ':')}:force_original_aspect_ratio=decrease,"
                  f"pad={_SIZE.replace('x', ':')}:(ow-iw)/2:(oh-ih)/2:color=0x0d1117",
           "-c:a", "aac", "-b:a", "128k", "-shortest", str(out)]
    r = subprocess.run(cmd, capture_output=True, timeout=180)
    return r.returncode == 0 and out.exists()


def generate_video_brief(data: dict) -> tuple[Path | None, str]:
    """Build the narrated MP4. Returns (path or None, status line)."""
    if os.environ.get("OROS_VIDEO", "1") == "0":
        return None, "Video brief: disabled (OROS_VIDEO=0)."
    if not shutil.which("ffmpeg"):
        return None, "Video brief: skipped (no ffmpeg in this environment)."
    try:
        from data.audio_client import is_available, generate_audio
        if not is_available():
            return None, "Video brief: skipped (no ELEVENLABS_API_KEY)."
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from ml.daily_report import pick_chart_fig

        scenes: list[tuple[object, str]] = [(_summary_scene(plt, data),
                                             _summary_script(data))]
        picks = (data.get("top_picks") or []) + (data.get("etf_picks") or [])
        for p in picks[:2]:
            fig = pick_chart_fig(plt, p)
            if fig is not None:
                scenes.append((fig, _pick_script(p)))
        cs = data.get("capstone")
        if cs and cs.get("rows"):
            scenes.append((_capstone_scene(plt, cs), _capstone_script(cs)))

        tmp = Path(tempfile.mkdtemp(prefix="oros_video_"))
        clips = []
        for i, (fig, script) in enumerate(scenes):
            png = tmp / f"scene{i}.png"
            fig.savefig(png, dpi=100, facecolor=fig.get_facecolor())
            plt.close(fig)
            mp3 = generate_audio(script)
            if mp3 is None:
                logger.warning("video: narration failed for scene %d", i)
                continue
            clip = tmp / f"clip{i}.mp4"
            if _scene_to_clip(png, Path(mp3), clip):
                clips.append(clip)
        if not clips:
            return None, "Video brief: no scenes could be rendered."

        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{c}'\n" for c in clips))
        out = _REPORT_DIR / f"oros_brief_{datetime.now():%Y-%m-%d}.mp4"
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(lst), "-c", "copy", str(out)],
                           capture_output=True, timeout=180)
        if r.returncode != 0 or not out.exists():
            return None, "Video brief: final assembly failed."
        logger.info("video brief written: %s (%d scenes, %.1f MB)",
                    out.name, len(clips), out.stat().st_size / 1e6)
        return out, (f"Video brief attached: a narrated walkthrough of today's "
                     f"charts ({len(clips)} scenes).")
    except Exception as e:  # noqa: BLE001
        logger.warning("video brief failed: %s", e)
        return None, f"Video brief: unavailable today ({type(e).__name__})."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from ml.daily_report import build_report_data
    p, note = generate_video_brief(build_report_data())
    print(p, "|", note)
