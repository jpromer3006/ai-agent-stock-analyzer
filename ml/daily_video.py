"""
daily_video.py - The OROS Market Desk: a narrated video morning note.

Professional analyst-desk structure (JP, 2026-07-09):
  1. Market open   - regime, index tape, fear gauge
  2. The book      - rotation (adds/drops), deployment, cash, throttle
  3. Day's movers  - best and worst in the paper book
  4. Pick of the day - the Weinstein chart, trade plan taught plainly
  5. The race      - frozen First Portfolio vs live book vs SPY (capstone)
  6. Sign-off      - honest disclosure: scoreboard status and sizing level

The personal (real-money) book is deliberately EXCLUDED from the video so it
can be shared. Scenes are matplotlib figures; voice is ElevenLabs (cached by
script hash); ffmpeg assembles. Skips cleanly without ffmpeg or the API key
(OROS_VIDEO=0 disables); the email then falls back to the audio-only brief.

Educational, not financial advice. OROS proposes and journals only.

    PYTHONPATH=. python3 ml/daily_video.py
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

_BG = "#0d1117"
_FG = "#e6edf3"
_MUT = "#8b949e"
_UP = "#3fb950"
_DN = "#f85149"
_WARN = "#f0883e"


# ---------------------------------------------------------------------------
# Scene cards
# ---------------------------------------------------------------------------
def _esc(s: str) -> str:
    """Escape '$' so matplotlib doesn't parse dollar amounts as mathtext."""
    return str(s).replace("$", r"\$")


def _card(plt, title: str, lines: list[tuple[str, dict]], kicker: str = ""):
    fig = plt.figure(figsize=(12.8, 7.2))
    fig.patch.set_facecolor(_BG)
    if kicker:
        fig.text(0.06, 0.93, _esc(kicker), fontsize=13, color=_MUT, family="monospace")
    fig.text(0.06, 0.84, _esc(title), fontsize=26, fontweight="bold", color=_FG)
    y = 0.72
    for text, style in lines:
        text = _esc(text)
        fig.text(0.06, y, text, fontsize=style.get("size", 15),
                 color=style.get("color", "#c9d1d9"),
                 fontweight=style.get("weight", "normal"), family="monospace")
        y -= style.get("dy", 0.068)
    fig.text(0.06, 0.045, "OROS Market Desk · simulated paper portfolio · "
             "proposes and journals only, never trades · educational, not "
             "financial advice", fontsize=9, color=_MUT)
    return fig


def _market_scene(plt, data: dict):
    lines = [(f"Regime: {(data.get('regime') or '?').upper()}"
              + (f"   ·   VIX {data['vix'].get('level', '')}" if data.get("vix") else ""),
              {"weight": "bold", "size": 18,
               "color": _UP if data.get("regime") == "bull" else _WARN})]
    for r in (data.get("indexes") or [])[:4]:
        c = _UP if r["day_pct"] >= 0 else _DN
        lines.append((f"{r['ticker']:5} {r['close']:>10,.2f}   day {r['day_pct']:+.2f}%   "
                      f"{r['vs_ma_pct']:+.1f}% vs 30-wk avg ({r['ma_trend']})",
                      {"color": c, "size": 14}))
    return _card(plt, "The Market Open", lines, kicker=data.get("date", ""))


def _book_scene(plt, data: dict):
    rec = data.get("proposal") or {}
    ch = data.get("changes") or {}
    acct = float(rec.get("account") or 1.0)
    inv, cash = float(rec.get("invested") or 0), float(rec.get("cash") or 0)
    lines = [
        (f"Positions: {len(rec.get('holdings') or [])}    "
         f"Deployed: ${inv:,.0f} ({inv/acct:.0%})    "
         f"Cash: ${cash:,.0f} ({cash/acct:.0%})", {"weight": "bold", "size": 15}),
    ]
    added, dropped = ch.get("added") or [], ch.get("removed") or []
    lines.append((f"IN : {', '.join(added) if added else 'no new names today'}",
                  {"color": _UP, "size": 14}))
    lines.append((f"OUT: {', '.join(dropped) if dropped else 'no names dropped'}",
                  {"color": _DN, "size": 14}))
    note = (rec.get("note") or "")
    thr = next((s.strip() for s in note.split(".") if "throttle" in s.lower()), "")
    if thr:
        lines.append((thr, {"color": _WARN, "size": 12, "dy": 0.06}))
    heat = next((s.strip() for s in note.split(".") if "wallet" in s.lower()), "")
    if heat:
        lines.append((heat, {"color": _MUT, "size": 12}))
    return _card(plt, "The Book — Today's Rotation", lines)


def _movers_scene(plt, data: dict):
    rows = sorted((data.get("pnl") or {}).get("rows") or [],
                  key=lambda r: r["day_usd"], reverse=True)
    if not rows:
        return None
    tot = (data.get("pnl") or {}).get("total_day_usd", 0.0)
    lines = [(f"Book today: ${tot:+,.0f}",
              {"weight": "bold", "size": 17, "color": _UP if tot >= 0 else _DN})]
    for r in rows[:3]:
        lines.append((f"▲ {r['ticker']:6} {r['day_pct']:+6.2f}%   ${r['day_usd']:+,.0f}",
                      {"color": _UP, "size": 14}))
    for r in rows[-3:][::-1]:
        if r["day_usd"] < 0:
            lines.append((f"▼ {r['ticker']:6} {r['day_pct']:+6.2f}%   ${r['day_usd']:+,.0f}",
                          {"color": _DN, "size": 14}))
    return _card(plt, "Movers in the Book", lines)


def _capstone_scene(plt, cs: dict):
    lv = cs.get("live") or {}
    lines = [
        (f"Same $100,000, three books, since {cs['start_date']}",
         {"size": 13, "color": _MUT}),
        (f"First Portfolio (frozen) ${cs['value']:,.0f}  ({cs['return_pct']:+.2f}%)",
         {"weight": "bold", "color": _UP if cs["return_pct"] >= 0 else _DN}),
    ]
    if lv:
        lines.append((f"Live OROS book          ${lv['value']:,.0f}  "
                      f"({lv['return_pct']:+.2f}%)",
                      {"weight": "bold", "color": _UP if lv["return_pct"] >= 0 else _DN}))
    if cs.get("spy_value"):
        lines.append((f"SPY index fund          ${cs['spy_value']:,.0f}  "
                      f"({cs['spy_return_pct']:+.2f}%)", {"color": _MUT}))
    lines.append(("Append-only measurement. Nothing restated.",
                  {"size": 12, "color": _MUT, "dy": 0.055}))
    return _card(plt, "The Race — to July 29", lines)


def _signoff_scene(plt, data: dict):
    board = data.get("scoreboard") or {}
    lines = []
    best = None
    for m, v in board.items():
        if v.get("expectancy_pct") is not None:
            if best is None or v["expectancy_pct"] > best[1]:
                best = (m, v["expectancy_pct"])
    for m, v in list(board.items())[:4]:
        e = v.get("expectancy_pct")
        lines.append((f"{m:<16} expectancy {'—' if e is None else f'{e:+.2f}%'}   "
                      f"({v.get('positions_scored') or 0} graded)",
                      {"size": 13, "color": _UP if (e or -1) > 0 else _MUT}))
    rec = data.get("proposal") or {}
    thr = next((s.strip() for s in (rec.get("note") or "").split(".")
                if "throttle" in s.lower()), "")
    if thr:
        lines.append((thr, {"color": _WARN, "size": 13, "dy": 0.06}))
    return _card(plt, "Disclosure — The Honest Scoreboard", lines)


# ---------------------------------------------------------------------------
# Narration (anchor voice, plain language, disclosure included)
# ---------------------------------------------------------------------------
def _market_script(data: dict) -> str:
    s = [f"Good morning, and welcome to the OROS Market Desk briefing for "
         f"{data.get('date', 'today')}."]
    regime = (data.get("regime") or "").upper()
    idx = {r["ticker"]: r for r in (data.get("indexes") or [])}
    spy, qqq = idx.get("SPY"), idx.get("QQQ")
    if spy:
        s.append(f"The S and P five hundred "
                 f"{'gained' if spy['day_pct'] >= 0 else 'slipped'} "
                 f"{abs(spy['day_pct']):.1f} percent"
                 + (f", while the NASDAQ {'added' if qqq['day_pct'] >= 0 else 'lost'} "
                    f"{abs(qqq['day_pct']):.1f}." if qqq else "."))
    s.append("The primary trend reads healthy: the index holds above its rising "
             "thirty week average." if regime == "BULL" else
             f"The primary trend reads {regime.lower() or 'mixed'}; capital "
             "preservation leads the playbook.")
    v = (data.get("vix") or {}).get("level")
    if v:
        s.append(f"The fear gauge sits at {v}.")
    return " ".join(s)


def _book_script(data: dict) -> str:
    rec = data.get("proposal") or {}
    ch = data.get("changes") or {}
    acct = float(rec.get("account") or 1.0)
    inv = float(rec.get("invested") or 0)
    added, dropped = ch.get("added") or [], ch.get("removed") or []
    s = [f"Turning to the model book. The system holds "
         f"{len(rec.get('holdings') or [])} positions with "
         f"{inv/acct:.0%} of capital deployed and the rest in cash, a split the "
         f"risk rules choose, not a leftover."]
    if added:
        s.append(f"New to the book: {', '.join(added[:6])} — each passed the full "
                 "entry gauntlet overnight.")
    if dropped:
        s.append(f"Rotated out: {', '.join(dropped[:6])} — names that no longer "
                 "clear the gates or lost their strategy sponsorship.")
    if not added and not dropped:
        s.append("No rotation today; the book stands pat.")
    return " ".join(s)


def _movers_script(data: dict) -> str:
    rows = sorted((data.get("pnl") or {}).get("rows") or [],
                  key=lambda r: r["day_usd"], reverse=True)
    if not rows:
        return ""
    tot = (data.get("pnl") or {}).get("total_day_usd", 0.0)
    s = [f"On the day, the book is {'up' if tot >= 0 else 'down'} "
         f"{abs(tot):,.0f} dollars."]
    up, dn = rows[0], rows[-1]
    if up["day_usd"] > 0:
        s.append(f"{up['ticker']} leads, {up['day_pct']:+.1f} percent.")
    if dn["day_usd"] < 0:
        s.append(f"{dn['ticker']} lags at {dn['day_pct']:+.1f}.")
    acts = data.get("actions") or []
    s.append("The checklist is clear: nothing requires action today."
             if not acts else
             f"The checklist carries {len(acts)} item"
             f"{'s' if len(acts) != 1 else ''}; details are in the written report.")
    return " ".join(s)


def _pick_script(p: dict) -> str:
    kind = "fund" if p.get("is_etf") else "stock"
    return (
        f"Now the chart of the day: {p['ticker']}, the top {kind} to clear every "
        f"quality gate. The blue line is the weekly price; the gold line is the "
        f"thirty week average, rising, the signature of a healthy trend. "
        f"The green dashed line at {p['buy_stop']:,.2f} is the entry trigger, and "
        f"this is the part most people get backwards: you do not buy it here. You "
        f"buy only if the market pushes up through that line, proving demand. If it "
        f"never gets there, you never buy, and that is the plan protecting you. "
        f"The red line at {p['stop_loss']:,.2f} is the exit once owned, taken on a "
        f"weekly close below, never an afternoon scare. The purple line at "
        f"{p['target']:,.2f} is the objective that justified the trade, "
        f"{p['risk_reward']:.1f} dollars of potential reward per dollar risked; as "
        f"price approaches it, professionals take partial profits and let the "
        f"rising stop guard the rest. At {p['shares_1pct']} shares, a failed trade "
        f"costs about one percent of the account. Buy on proof. Exit by rule. "
        f"Size to survive."
    )


def _no_pick_script() -> str:
    return ("No pick of the day: nothing in the scan cleared every gate, and in "
            "this discipline, no setup means no trade. Forcing a pick on a weak "
            "tape is how systems bleed. We wait.")


def _capstone_script(cs: dict) -> str:
    lv = cs.get("live") or {}
    s = [f"The race the analysts asked for: three books, the same one hundred "
         f"thousand simulated dollars, since July sixth. The frozen first "
         f"portfolio: {cs['value']:,.0f}, "
         f"{'up' if cs['return_pct'] >= 0 else 'down'} "
         f"{abs(cs['return_pct']):.2f} percent."]
    if lv:
        s.append(f"The live, self-rebuilding book: {lv['value']:,.0f}.")
    if cs.get("spy_value"):
        s.append(f"The S and P index fund: {cs['spy_value']:,.0f}.")
    s.append("Every line starts the same day with the same dollar. Nothing is "
             "restated.")
    return " ".join(s)


def _signoff_script(data: dict) -> str:
    board = data.get("scoreboard") or {}
    exps = [v.get("expectancy_pct") for v in board.values()
            if v.get("expectancy_pct") is not None]
    s = ["Before we close, the disclosure segment."]
    if exps and max(exps) <= 0:
        s.append("The system's forward track record remains negative across "
                 "strategies, and it is published anyway. Position sizes stay "
                 "automatically reduced until the record earns them back.")
    elif exps:
        s.append("The forward record shows early positive expectancy; sizing "
                 "follows the record, not the mood.")
    s.append("All figures are simulated paper trading. OROS proposes and journals "
             "only; it never trades, and nothing here is financial advice. "
             "From the OROS Market Desk, have a disciplined session.")
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
    """Build the Market Desk MP4. Returns (path or None, status line)."""
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

        scenes: list[tuple[object, str]] = []
        scenes.append((_market_scene(plt, data), _market_script(data)))
        if data.get("proposal"):
            scenes.append((_book_scene(plt, data), _book_script(data)))
        mv = _movers_scene(plt, data)
        if mv is not None:
            scenes.append((mv, _movers_script(data)))
        picks = (data.get("top_picks") or []) + (data.get("etf_picks") or [])
        if picks:
            for p in picks[:2]:
                fig = pick_chart_fig(plt, p)
                if fig is not None:
                    scenes.append((fig, _pick_script(p)))
        else:
            scenes.append((_card(plt, "Pick of the Day",
                                 [("None. Nothing cleared every gate today.",
                                   {"weight": "bold", "size": 17, "color": _WARN}),
                                  ("No setup means no trade.", {"color": _MUT})]),
                           _no_pick_script()))
        cs = data.get("capstone")
        if cs and cs.get("rows"):
            scenes.append((_capstone_scene(plt, cs), _capstone_script(cs)))
        scenes.append((_signoff_scene(plt, data), _signoff_script(data)))

        tmp = Path(tempfile.mkdtemp(prefix="oros_video_"))
        clips = []
        for i, (fig, script) in enumerate(scenes):
            if not script:
                plt.close(fig)
                continue
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
        return out, (f"Market Desk video attached: {len(clips)} scenes — market, "
                     "rotation, movers, chart of the day, the race, disclosure. "
                     "(Personal holdings excluded by design.)")
    except Exception as e:  # noqa: BLE001
        logger.warning("video brief failed: %s", e)
        return None, f"Video brief: unavailable today ({type(e).__name__})."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from ml.daily_report import build_report_data
    p, note = generate_video_brief(build_report_data())
    print(p, "|", note)
