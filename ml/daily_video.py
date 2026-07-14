"""
daily_video.py - The OROS Market Desk: a narrated video morning note.

Newscast format (JP, 2026-07-12) — a three-voice newsroom telling one story:
can a system that reflects on its own record hold its own, with no human
intervention? Rundown:
  1. Cold open       - tonight's headlines + the no-human tagline (anchor)
  2. The Market Open - regime, fear gauge, four Weinstein index CHARTS
  3. Top Story       - the pick, but the HOME-RUN label only when the strict
                       bar clears (fresh setup, R/R>=3, 2x volume, RS>0);
                       includes the agent-pipeline story (analyst voice)
  4. Business Desk   - the book: rotation WITH per-name reasons, throttle
  5. Movers          - best and worst in the paper book
  6. Sports Desk     - The Race: standings language, day N of 17 (sports voice)
  7. Market Analysis - stage breadth of the OROS 500 (the market's seasons),
                       plus the VIX as an outside gauge
  8. The Takeaway    - one closing lesson EARNED by the day's events
  9. Sign-off        - disclosure, agents-and-sources credits

Narration is built from SEGMENTS so evergreen teaching audio is cached by
(voice, text) hash forever; only the day's numbers hit ElevenLabs. One deep
lesson rotates daily (_lesson_key). Sidecar files ride with the MP4:
{stem}.srt (captions) and {stem}.meta.json (chapters, headline facts) for
the YouTube publisher's description and transcript.

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
_BLUE = "#58a6ff"
_GOLD = "#e0a800"
_GRID = "#30363d"

_INDEX_NAMES = {"SPY": "S&P 500", "QQQ": "NASDAQ 100",
                "DIA": "Dow Jones", "IWM": "Russell 2000"}

_FOOTER = ("OROS Market Desk · simulated paper portfolio · proposes and "
           "journals only, never trades · educational, not financial advice")

_CREDITS_LINE = ("Agents: scanner · classifier · portfolio · risk · desk     "
                 "Sources: SEC EDGAR XBRL · FRED · daily market prices")

# ---------------------------------------------------------------------------
# The cast (JP, 2026-07-12): a three-voice newsroom. ElevenLabs premade
# voices by default, overridable via .env. audio_client caches per
# (voice, text), so evergreen lines are paid once per voice, ever.
# ---------------------------------------------------------------------------
_VOICES = {
    "anchor":  os.environ.get("OROS_VOICE_ANCHOR",
                              "pNInz6obpgDQGcFmaJgB"),   # Adam — the desk
    "analyst": os.environ.get("OROS_VOICE_ANALYST",
                              "21m00Tcm4TlvDq8ikWAM"),   # Rachel — top story
    "sports":  os.environ.get("OROS_VOICE_SPORTS",
                              "TxGEqnHWrfWFTfGW9XjX"),   # Josh — the race
}

_TAGLINE = ("Every position, every chart, and every word of this broadcast "
            "was produced by software with no human intervention. The "
            "question this channel exists to answer: can a system that "
            "reflects on its own record hold its own?")

# Season arc: the capstone race ends 2026-07-29 (Fordham finale).
_RACE_START = "2026-07-06"
_RACE_END = "2026-07-29"


def _race_day() -> tuple[int, int]:
    """(current trading day number, total trading days) of the race."""
    import numpy as np
    try:
        start = datetime.strptime(_RACE_START, "%Y-%m-%d").date()
        end = datetime.strptime(_RACE_END, "%Y-%m-%d").date()
        today = min(datetime.now().date(), end)
        total = int(np.busday_count(start, end) + 1)
        cur = max(1, min(int(np.busday_count(start, today) + 1), total))
        return cur, total
    except Exception:  # noqa: BLE001
        return 1, 17


# ---------------------------------------------------------------------------
# The Takeaway: one closing lesson EARNED by the day's events, never a
# random fortune cookie. Rule-mapped, all evergreen (cached once).
# ---------------------------------------------------------------------------
_TAKEAWAYS = {
    "no_pick": ("Tonight's takeaway. You don't get paid for activity; you "
                "get paid for being right. And sometimes being right means "
                "sitting still."),
    "throttled": ("Tonight's takeaway. The system trades smaller today "
                  "because its own record says it should. Ego has no line "
                  "item in a portfolio."),
    "stop_hit": ("Tonight's takeaway. A small loss taken on rule is a "
                 "receipt for insurance. The disaster is the loss you "
                 "negotiate with."),
    "home_run": ("Tonight's takeaway. Nobody knows whether today's setup "
                 "works. The plan, the trigger, the stop, the size, makes "
                 "it safe to find out."),
    "rotation": ("Tonight's takeaway. A portfolio is not a museum. Names "
                 "that stop qualifying leave, names that start qualifying "
                 "arrive, and no position is owed loyalty."),
    "quiet": ("Tonight's takeaway. Wealth is built on the boring days. "
              "Showing up with discipline is the strategy."),
    "winter": ("Tonight's takeaway. When most of the market is in decline, "
               "cash is not surrender. It is the position that lets you "
               "play the next season."),
}


def _takeaway_key(data: dict, home_run: bool) -> str:
    pnl = data.get("pnl") or {}
    if pnl.get("stops_hit"):
        return "stop_hit"
    br = data.get("breadth") or {}
    counts, tot = br.get("counts") or {}, max(br.get("total") or 0, 1)
    if counts and (counts.get("Declining", 0) / tot) >= 0.40:
        return "winter"
    note = ((data.get("proposal") or {}).get("note") or "").lower()
    if "throttle" in note and "100" not in note:
        return "throttled"
    if home_run:
        return "home_run"
    ch = data.get("changes") or {}
    if ch.get("added") or ch.get("removed"):
        return "rotation"
    picks = (data.get("top_picks") or []) + (data.get("etf_picks") or [])
    if not picks:
        return "no_pick"
    return "quiet"


# ---------------------------------------------------------------------------
# The home-run gate (strict, so the Top Story label means something):
# every pick already survived the full Weinstein gauntlet; on top of that
# we demand a fresh setup, 3-to-1 reward on risk, a 2x volume surge, and
# positive relative strength before the desk may call it the Top Story.
# ---------------------------------------------------------------------------
def _is_home_run(p: dict) -> bool:
    try:
        return (p.get("setup") == "fresh"
                and float(p.get("risk_reward") or 0) >= 3.0
                and float(p.get("volume_surge") or 0) >= 2.0
                and float(p.get("mansfield_rs") or 0) > 0)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Rotating lessons (JP, 2026-07-12): one scene gets the deep, evergreen
# explanation each day; the others get a single orienting sentence. Every
# evergreen string below is BYTE-STABLE across days, so ElevenLabs audio is
# generated once and served from cache forever. Only sentences carrying the
# day's numbers regenerate.
# ---------------------------------------------------------------------------
_ORIENT = {
    "market": ("On screen, each index panel shows the weekly closing price in "
               "blue against its thirty week average in gold — the health of "
               "the tape at a glance."),
    "book": ("On screen, the book's positions, how much capital is deployed, "
             "and today's rotation in and out."),
    "movers": ("On screen, the day's gainers and laggards inside the paper "
               "book."),
    "race": ("On screen, three equity lines grown from the same starting "
             "dollar: the frozen first book, the live book, and the index "
             "fund."),
    "signoff": ("On screen, the forward scoreboard that decides how large the "
                "system is allowed to trade."),
}

_LESSONS = {
    "market": ("Today's lesson is this market screen. The gold line is the "
               "thirty week moving average, the single most useful trend "
               "filter in stage analysis. When price holds above a rising "
               "gold line, the tide is coming in, and long trades get the "
               "benefit of the doubt. When price sinks below a falling gold "
               "line, the tide is going out, and the first job becomes "
               "protecting capital, not finding heroes."),
    "book": ("Today's lesson is position management. Cash is a position. The "
             "system sizes deployment by rule, and when its own forward "
             "record weakens, a throttle automatically cuts size. Rotation "
             "is not indecision; it is the portfolio breathing. Names that "
             "stop qualifying leave, and names that start qualifying "
             "arrive."),
    "movers": ("Today's lesson is noise versus signal. Daily moves are "
               "weather; the weekly trend is climate. The system never exits "
               "on a scary afternoon. It acts only on weekly closes, because "
               "that is where the real information lives."),
    "race": ("Today's lesson is benchmarking. Any strategy must beat the "
             "boring alternative: the same money parked in an index fund. "
             "That is why the S and P line shares the chart. If the system "
             "cannot outrun it, the index deserves the money, and publishing "
             "the race every day keeps the system honest."),
    "signoff": ("Today's lesson is the scoreboard. Expectancy is the average "
                "profit or loss per graded trade. A system that publishes a "
                "negative record and cuts its own size is doing the one "
                "thing most traders never do: letting evidence, not "
                "confidence, set the risk."),
}


def _lesson_key() -> str:
    """Deterministic daily rotation through the lesson topics."""
    keys = sorted(_LESSONS)
    return keys[datetime.now().timetuple().tm_yday % len(keys)]


def _teach(key: str) -> str:
    """Full lesson on this scene's rotation day, one orienting line otherwise."""
    return _LESSONS[key] if _lesson_key() == key else _ORIENT[key]


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


def _coldopen_scene(plt, data: dict, home_run: dict | None):
    """The rundown card: three headlines and the no-human tagline."""
    day, total = _race_day()
    regime = (data.get("regime") or "?").upper()
    lines = [
        (f"01  THE MARKET OPEN      regime {regime}"
         + (f", VIX {data['vix'].get('level', '')}" if data.get("vix") else ""),
         {"color": _UP if regime == "BULL" else _WARN, "size": 15}),
        ((f"02  TOP STORY           {home_run['ticker']} — a home-run setup"
          if home_run else
          "02  TOP STORY           no setup cleared the bar — discipline day"),
         {"color": _GOLD if home_run else _MUT, "size": 15}),
        (f"03  THE RACE            day {day} of {total} — three books, "
         f"same $100,000", {"color": _BLUE, "size": 15}),
        ("", {"size": 8, "dy": 0.03}),
        ("No human intervention: scanned, built, risk-checked,",
         {"color": _MUT, "size": 12, "dy": 0.05}),
        ("written and voiced entirely by the OROS agents.",
         {"color": _MUT, "size": 12}),
    ]
    return _card(plt, "Tonight on the Market Desk", lines,
                 kicker=data.get("date", ""))


def _coldopen_script(data: dict, home_run: dict | None) -> list[str]:
    day, total = _race_day()
    regime = (data.get("regime") or "mixed").upper()
    dyn = [f"Good morning. From the OROS Market Desk, here is the rundown "
           f"for {data.get('date', 'today')}."]
    dyn.append(f"The tape reads {regime.lower()}.")
    if home_run:
        dyn.append(f"Our top story: {home_run['ticker']} cleared every gate "
                   f"and the home-run bar. The full trade plan is ahead.")
    else:
        dyn.append("No setup cleared our home-run bar today, and you will "
                   "hear why that is a story in itself.")
    dyn.append(f"And at the sports desk, day {day} of {total} in the race: "
               f"the live book, the frozen book, and the index, all running "
               f"the same one hundred thousand simulated dollars.")
    return [" ".join(dyn), _TAGLINE]


def _analysis_scene(plt, data: dict):
    """Market Analysis: stage breadth of the OROS 500, drawn as four
    season boxes with counts, percentages, and the shift vs prior."""
    br = data.get("breadth")
    if not br or not br.get("counts"):
        raise ValueError("no stage breadth journaled yet")
    tot = max(int(br.get("total") or 0), 1)
    prior = br.get("prior")
    n500 = (data.get("oros500") or {}).get("count") or tot

    spec = [("Advancing", "Stage 2 · summer", _UP),
            ("Basing", "Stage 1 · spring", _BLUE),
            ("Topping", "Stage 3 · autumn", _WARN),
            ("Declining", "Stage 4 · winter", _DN)]
    fig = plt.figure(figsize=(12.8, 7.2))
    fig.patch.set_facecolor(_BG)
    fig.text(0.06, 0.93, _esc(f"{br.get('date', '')} · {n500} names graded "
             f"overnight"), fontsize=13, color=_MUT, family="monospace")
    fig.text(0.06, 0.84, "Market Analysis — Inside the OROS 500",
             fontsize=26, fontweight="bold", color=_FG)
    for i, (key, sub, color) in enumerate(spec):
        x = 0.06 + (i % 2) * 0.47
        y = 0.46 - (i // 2) * 0.30
        n = int((br["counts"] or {}).get(key, 0))
        pct = 100 * n / tot
        delta = ""
        if prior and prior.get("counts") and prior.get("total"):
            d = pct - 100 * prior["counts"].get(key, 0) / max(prior["total"], 1)
            arrow = "▲" if d >= 0.5 else ("▼" if d <= -0.5 else "·")
            delta = f"   {arrow} {d:+.0f} pts vs {prior['date']}"
        fig.text(x, y + 0.14, f"{key.upper()}", fontsize=16,
                 fontweight="bold", color=color, family="monospace")
        fig.text(x, y + 0.045, f"{pct:.0f}%", fontsize=34,
                 fontweight="bold", color=color, family="monospace")
        fig.text(x + 0.15, y + 0.055, f"{n} of {tot}", fontsize=12,
                 color=_FG, family="monospace")
        fig.text(x, y - 0.005, _esc(sub + delta), fontsize=10, color=_MUT,
                 family="monospace")
    inv = br.get("invalidated") or 0
    v = (data.get("vix") or {}).get("level")
    tail = f"Invalidations on watch: {inv}"
    if v:
        tail += f"     Outside gauge: VIX {v}"
    fig.text(0.06, 0.095, _esc(tail), fontsize=12, color=_WARN if inv else _MUT,
             family="monospace")
    fig.text(0.06, 0.045, _FOOTER, fontsize=9, color=_MUT)
    return fig


_ANALYSIS_EXPLAINER = (
    "Why this screen matters: stages are the market's seasons. Basing is "
    "spring, when ownership quietly changes hands. Advancing is summer, the "
    "only season this system buys. Topping is autumn, when smart money "
    "distributes. Declining is winter, when the first job is staying warm. "
    "The more of the market in summer, the more the tide is doing the "
    "lifting."
)


def _analysis_script(data: dict) -> list[str]:
    br = data.get("breadth") or {}
    counts, tot = br.get("counts") or {}, max(br.get("total") or 0, 1)
    n500 = (data.get("oros500") or {}).get("count") or tot
    s = [f"Now, market analysis. Overnight the scanner graded the OROS "
         f"five hundred — {n500} of the most liquid names on the tape — "
         f"and sorted every chart into its Weinstein stage."]
    adv = 100 * counts.get("Advancing", 0) / tot
    base = 100 * counts.get("Basing", 0) / tot
    dec = 100 * counts.get("Declining", 0) / tot
    s.append(f"{adv:.0f} percent are advancing in Stage 2. {base:.0f} percent "
             f"are basing. {dec:.0f} percent are in decline.")
    prior = br.get("prior")
    if prior and prior.get("counts") and prior.get("total"):
        d = adv - 100 * prior["counts"].get("Advancing", 0) / max(prior["total"], 1)
        if abs(d) >= 1:
            s.append(f"The advancing share is {'up' if d > 0 else 'down'} "
                     f"{abs(d):.0f} points since {prior['date']} — the tide "
                     f"is {'coming in' if d > 0 else 'going out'}.")
    v = (data.get("vix") or {}).get("level")
    if v:
        s.append(f"Outside our shop, the fear gauge reads {v}.")
    return [" ".join(s), _ANALYSIS_EXPLAINER]


def _takeaway_scene(plt, key: str):
    text = _TAKEAWAYS[key]
    body = text.replace("Tonight's takeaway. ", "")
    import textwrap
    lines = [("“" if i == 0 else "") + ln
             for i, ln in enumerate(textwrap.wrap(body, 52))]
    lines[-1] += "”"
    styled = [(ln, {"size": 17, "color": _FG, "dy": 0.085}) for ln in lines]
    return _card(plt, "The Takeaway", styled)


def _market_text_scene(plt, data: dict):
    """Text-tape fallback when index price history is unavailable."""
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


def _style_dark_ax(ax):
    ax.set_facecolor(_BG)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.tick_params(colors=_MUT, labelsize=7)
    ax.grid(alpha=0.15, color=_GRID)


def _market_chart_scene(plt, data: dict):
    """The Market Open as four Weinstein index panels: weekly close (blue)
    vs the 30-week MA (gold), with the day's tape numbers on each panel.
    Narration cites the S&P and NASDAQ; now the viewer sees them too."""
    from data import price_cache
    from ml.daily_report import _weekly

    idx_rows = {r["ticker"]: r for r in (data.get("indexes") or [])}
    tickers = [t for t in ("SPY", "QQQ", "DIA", "IWM") if t in idx_rows]
    if len(tickers) < 2:
        raise ValueError("not enough index rows for the chart scene")

    fig, axes = plt.subplots(2, 2, figsize=(12.8, 7.2))
    fig.patch.set_facecolor(_BG)
    fig.subplots_adjust(left=0.06, right=0.97, top=0.80, bottom=0.10,
                        hspace=0.45, wspace=0.18)
    regime = (data.get("regime") or "?").upper()
    head = f"Regime: {regime}"
    if data.get("vix"):
        head += f"   ·   VIX {data['vix'].get('level', '')}"
    fig.text(0.06, 0.945, _esc(data.get("date", "")), fontsize=13, color=_MUT,
             family="monospace")
    fig.text(0.06, 0.875, "The Market Open", fontsize=26, fontweight="bold",
             color=_FG)
    fig.text(0.60, 0.885, _esc(head), fontsize=15, fontweight="bold",
             color=_UP if regime == "BULL" else _WARN, family="monospace")

    drawn = 0
    for ax, t in zip(axes.flat, tickers):
        r = idx_rows[t]
        df = price_cache.get_history(t)
        if df is None or len(df) < 160 or "Close" not in df.columns:
            ax.set_visible(False)
            continue
        wk = _weekly(df).tail(80)
        ma30 = _weekly(df)["Close"].rolling(30).mean().reindex(wk.index)
        _style_dark_ax(ax)
        ax.plot(wk.index, wk["Close"], color=_BLUE, linewidth=1.5)
        ax.plot(ma30.index, ma30, color=_GOLD, linewidth=1.8)
        c = _UP if r["day_pct"] >= 0 else _DN
        ax.set_title(f"{_INDEX_NAMES.get(t, t)} ({t})   {r['close']:,.2f}   "
                     f"day {r['day_pct']:+.2f}%",
                     fontsize=11, fontweight="bold", color=c, loc="left",
                     family="monospace")
        ax.text(0.02, 0.06,
                f"{r['vs_ma_pct']:+.1f}% vs 30-wk MA ({r['ma_trend']})",
                transform=ax.transAxes, fontsize=8.5, color=_GOLD,
                family="monospace")
        drawn += 1
    if drawn < 2:
        plt.close(fig)
        raise ValueError("index history too thin for the chart scene")
    fig.text(0.06, 0.028, "blue = weekly close · gold = 30-week moving average"
             "     " + _FOOTER, fontsize=8, color=_MUT)
    return fig


def _market_scene(plt, data: dict):
    try:
        return _market_chart_scene(plt, data)
    except Exception as e:  # noqa: BLE001
        logger.warning("market chart scene fell back to text: %s", e)
        return _market_text_scene(plt, data)


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


def _capstone_chart_scene(plt, cs: dict):
    """The Race drawn as it is narrated: three equity lines from the same
    starting dollar — frozen First Portfolio, live chained book, and SPY."""
    import json
    import pandas as pd
    from data import price_cache
    from ml.capstone_tracker import _CHAIN

    acct = float(cs.get("account") or 100_000.0)
    start = pd.Timestamp(cs["start_date"])

    def _closes(ticker):
        df = price_cache.get_history(ticker)
        if df is None or "Close" not in df.columns:
            return None
        c = df["Close"].dropna()
        c.index = pd.to_datetime(c.index)
        try:
            c.index = c.index.tz_localize(None)
        except TypeError:
            pass
        return c[c.index.normalize() >= start]

    spy = _closes("SPY")
    if spy is None or len(spy) < 2:
        raise ValueError("not enough SPY history since capstone start")
    spy_curve = acct * spy / float(spy.iloc[0])

    # Frozen book: cash + shares x daily close for each journaled holding.
    frozen = pd.Series(float(cs.get("cash") or 0.0), index=spy.index)
    priced = 0
    for r in cs.get("rows") or []:
        c = _closes(r["ticker"])
        if c is None or not len(c):
            continue
        frozen = frozen + float(r["shares"]) * c.reindex(spy.index).ffill().bfill()
        priced += 1
    if priced == 0:
        raise ValueError("no frozen-book holdings could be priced")

    # Live book: cumulative chain of the evolving book's daily returns.
    live = None
    if _CHAIN.exists():
        try:
            ch = json.loads(_CHAIN.read_text())
            v, vals = acct, []
            for dr in ch.get("day_returns") or []:
                v *= 1.0 + float(dr)
                vals.append(v)
            if vals:
                live = pd.Series(vals, index=pd.to_datetime(ch["dates"]))
        except Exception as e:  # noqa: BLE001
            logger.warning("live chain unreadable for race chart: %s", e)

    fig, ax = plt.subplots(figsize=(12.8, 7.2))
    fig.patch.set_facecolor(_BG)
    fig.subplots_adjust(left=0.08, right=0.82, top=0.78, bottom=0.12)
    fig.text(0.06, 0.94, f"Same \\$100,000, three books, since {cs['start_date']}"
             f" · finish line {cs.get('end_date', '')}",
             fontsize=13, color=_MUT, family="monospace")
    fig.text(0.06, 0.85, "The Race — to July 29", fontsize=26,
             fontweight="bold", color=_FG)
    _style_dark_ax(ax)
    ax.axhline(acct, color=_GRID, linewidth=1.0, linestyle=":")

    series = [(frozen, _UP, "First Portfolio (frozen)"),
              (spy_curve, _MUT, "SPY index fund")]
    if live is not None:
        series.insert(1, (live, _BLUE, "Live OROS book"))
    for s, color, label in series:
        ax.plot(s.index, s, color=color, linewidth=2.2, marker="o",
                markersize=3.5, label=label)
        end_pct = 100 * (float(s.iloc[-1]) / acct - 1)
        ax.annotate(f" {label}\n ${float(s.iloc[-1]):,.0f} ({end_pct:+.2f}%)",
                    xy=(s.index[-1], float(s.iloc[-1])),
                    xycoords="data", xytext=(8, 0), textcoords="offset points",
                    fontsize=9.5, color=color, fontweight="bold",
                    family="monospace", va="center",
                    annotation_clip=False)
    ax.set_ylabel("Simulated value ($)", color=_MUT, fontsize=9)
    import matplotlib.dates as mdates
    ax.xaxis.set_major_locator(mdates.DayLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.tick_params(axis="x", labelsize=8)
    fig.text(0.06, 0.035, "dotted line = the starting \\$100,000 · append-only "
             "measurement, nothing restated     " + _FOOTER,
             fontsize=8, color=_MUT)
    return fig


def _capstone_text_scene(plt, cs: dict):
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


def _capstone_scene(plt, cs: dict):
    try:
        return _capstone_chart_scene(plt, cs)
    except Exception as e:  # noqa: BLE001
        logger.warning("race chart scene fell back to text: %s", e)
        return _capstone_text_scene(plt, cs)


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
    lines.append((_CREDITS_LINE, {"color": _MUT, "size": 10, "dy": 0.09}))
    return _card(plt, "Disclosure — The Honest Scoreboard", lines)


# ---------------------------------------------------------------------------
# Narration (anchor voice, plain language, disclosure included)
#
# Every script builder returns a LIST of segments. Each segment becomes its
# own ElevenLabs call, cached by text hash, and the MP3s are concatenated.
# Convention: dynamic segments (today's numbers) first, then one evergreen
# segment from _teach() — the deep lesson on that scene's rotation day, a
# single orienting sentence otherwise. Evergreen text is byte-stable, so it
# is paid for exactly once.
# ---------------------------------------------------------------------------
def _market_script(data: dict) -> list[str]:
    s = ["First, the market open."]
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
    return [" ".join(s), _teach("market")]


def _change_reason(ticker: str) -> str:
    """One plain-language reason a name entered or left the book, straight
    from the stage analyzer's journaled read (cheap: cached analysis)."""
    try:
        from data import price_cache
        from ml.stage_analyzer import analyze_stage
        r = analyze_stage(ticker, df=price_cache.get_history(ticker),
                          use_cache=True)
        if getattr(r, "is_invalidated", False):
            return "closed below its 30-week average, our invalidation rule"
        name = (getattr(r, "stage_name", "") or "").lower()
        if name == "topping":
            return "slipped into Stage 3 distribution"
        if name == "declining":
            return "broke down into a Stage 4 decline"
        if name == "basing":
            return "fell back into a base"
        if name == "advancing":
            return "still advancing, but outranked by stronger names"
    except Exception:  # noqa: BLE001
        pass
    return "no longer clears the entry gates"


def _book_script(data: dict) -> list[str]:
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
        s.append("Rotated out: " + "; ".join(
            f"{t}, which {_change_reason(t)}" for t in dropped[:4]) + ".")
    if not added and not dropped:
        s.append("No rotation today; the book stands pat.")
    return [" ".join(s), _teach("book")]


def _movers_script(data: dict) -> list[str]:
    rows = sorted((data.get("pnl") or {}).get("rows") or [],
                  key=lambda r: r["day_usd"], reverse=True)
    if not rows:
        return []
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
    return [" ".join(s), _teach("movers")]


_PICK_LESSON = (
    "How to read the chart: the blue line is the weekly price; the gold line "
    "is the thirty week average, rising, the signature of a healthy trend. "
    "The green dashed line is the entry trigger, and this is the part most "
    "people get backwards: you do not buy it here. You buy only if the market "
    "pushes up through that line, proving demand. If it never gets there, you "
    "never buy, and that is the plan protecting you. The red line is the exit "
    "once owned, taken on a weekly close below, never an afternoon scare. The "
    "purple line is the objective that justified the trade; as price "
    "approaches it, professionals take partial profits and let the rising "
    "stop guard the rest. Buy on proof. Exit by rule. Size to survive."
)


_PIPELINE_STORY = (
    "How a pick reaches this desk: the scanner grades every chart in the "
    "OROS five hundred overnight. The router classifies each survivor and "
    "hands it to a sector specialist, which pulls the company's actual "
    "financial filings from SEC EDGAR. A sentiment analyst reads the recent "
    "headlines. Then the risk manager, who holds veto power, checks the "
    "position size against the account's risk budget. Only a name that "
    "survives every desk gets named the top story."
)


def _pick_script(p: dict, home_run: bool = False) -> list[str]:
    kind = "fund" if p.get("is_etf") else "stock"
    label = ("Our top story, and the system calls this one a home-run "
             "setup — a label its own rules only allow when everything "
             "lines up" if home_run else "Now the chart of the day")
    dyn = (
        f"{label}: {p['ticker']}, the top {kind} to clear every quality "
        f"gate. The entry trigger sits at {p['buy_stop']:,.2f}, the exit at "
        f"{p['stop_loss']:,.2f}, and the objective at {p['target']:,.2f} — "
        f"{p['risk_reward']:.1f} dollars of potential reward per dollar "
        f"risked. At {p['shares_1pct']} shares, a failed trade costs about "
        f"one percent of the account."
    )
    segs = [dyn, _PICK_LESSON]
    if home_run:
        segs.append(_PIPELINE_STORY)
    return segs


def _no_pick_script() -> list[str]:
    return [("No pick of the day: nothing in the scan cleared every gate, and "
             "in this discipline, no setup means no trade. Forcing a pick on a "
             "weak tape is how systems bleed. We wait.")]


def _capstone_script(cs: dict) -> list[str]:
    """The sports desk: standings language, play-by-play voice."""
    day, total = _race_day()
    lv = cs.get("live") or {}
    standings = [("the frozen first portfolio, the green line",
                  float(cs["value"]), float(cs["return_pct"]))]
    if lv:
        standings.append(("the live self-rebuilding book, in blue",
                          float(lv["value"]), float(lv["return_pct"])))
    if cs.get("spy_value"):
        standings.append(("the index fund, in gray",
                          float(cs["spy_value"]), float(cs["spy_return_pct"])))
    standings.sort(key=lambda r: -r[1])
    lead_name, lead_v, _ = standings[0]
    s = [f"Over to the sports desk. Day {day} of {total} in the race: three "
         f"books, the same one hundred thousand simulated dollars, since "
         f"July sixth."]
    s.append(f"In first place, {lead_name}, at {lead_v:,.0f}.")
    for name, v, _pct in standings[1:]:
        gap = (lead_v - v) / 1000.0
        s.append(f"Then {name}, {gap:,.1f} thousand back at {v:,.0f}.")
    if day >= total:
        s.append("That's the final. The full season review is coming.")
    else:
        s.append(f"{total - day} trading day{'s' if total - day != 1 else ''} "
                 f"to the finish line.")
    s.append("Every line starts the same day with the same dollar. Nothing "
             "is restated.")
    return [" ".join(s), _teach("race")]


_CREDITS_SCRIPT = (
    "A word on how this desk works. Every segment you just watched was "
    "researched and assembled end to end by the OROS specialist agents: a "
    "scanner that grades every chart, a classifier that routes each name to "
    "a sector specialist, a portfolio builder, and a risk manager with veto "
    "power. The numbers come from primary sources: SEC EDGAR company "
    "filings, Federal Reserve economic data, and daily market prices."
)

_DISCLOSURE_SCRIPT = (
    "All figures are simulated paper trading. OROS proposes and journals "
    "only; it never trades, and nothing here is financial advice. From the "
    "OROS Market Desk, have a disciplined session."
)


def _signoff_script(data: dict) -> list[str]:
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
    segs = [" ".join(s)]
    if _lesson_key() == "signoff":
        segs.append(_LESSONS["signoff"])
    segs.append(_CREDITS_SCRIPT)
    segs.append(_DISCLOSURE_SCRIPT)
    return segs


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def _mp3_duration(path: Path) -> float:
    """Duration in seconds via ffprobe (ships with ffmpeg)."""
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, timeout=30)
        return float(r.stdout.decode().strip())
    except Exception:  # noqa: BLE001
        return 0.0


def _narration_audio(segments: list[str], generate_audio, tmp: Path,
                     idx: int, voice: str = "anchor",
                     ) -> tuple[Path | None, list[tuple[str, float]]]:
    """One MP3 per segment (each cached by (voice, text) hash by
    audio_client), then concatenate. Evergreen segments cost one API call
    per voice, ever; only the day's-numbers segments regenerate.
    Returns (combined mp3, [(segment text, seconds)]) — the timings feed
    the SRT captions."""
    voice_id = _VOICES.get(voice) or _VOICES["anchor"]
    timed: list[tuple[str, float, Path]] = []
    for s in segments:
        if not s or not s.strip():
            continue
        m = generate_audio(s, voice_id=voice_id)
        if m:
            timed.append((s, _mp3_duration(Path(m)), Path(m)))
    if not timed:
        return None, []
    if len(timed) == 1:
        return timed[0][2], [(timed[0][0], timed[0][1])]
    lst = tmp / f"aud{idx}.txt"
    lst.write_text("".join(f"file '{t[2]}'\n" for t in timed))
    out = tmp / f"aud{idx}.mp3"
    r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(out)],
                       capture_output=True, timeout=60)
    if r.returncode != 0 or not out.exists():
        # Stream-copy can fail on mismatched encoder params; re-encode.
        r = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(lst), "-acodec", "libmp3lame",
                            "-b:a", "128k", str(out)],
                           capture_output=True, timeout=120)
        if r.returncode != 0 or not out.exists():
            logger.warning("narration concat failed for scene %d; using "
                           "first segment only", idx)
            return timed[0][2], [(timed[0][0], timed[0][1])]
    return out, [(t[0], t[1]) for t in timed]


def _srt_time(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(cues: list[tuple[float, float, str]], path: Path) -> None:
    """cues = [(start_s, end_s, text)] — long texts split by sentence with
    time allotted proportionally to length."""
    import re
    out, n = [], 1
    for start, end, text in cues:
        sents = [s.strip() for s in re.split(r"(?<=[.!?]) +", text) if s.strip()]
        if not sents:
            continue
        total_chars = sum(len(s) for s in sents)
        t = start
        for s in sents:
            dur = (end - start) * (len(s) / max(total_chars, 1))
            out.append(f"{n}\n{_srt_time(t)} --> {_srt_time(min(t + dur, end))}\n{s}\n")
            n += 1
            t += dur
    path.write_text("\n".join(out), encoding="utf-8")


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

        # ---- The rundown (key, chapter title, fig, segments, voice) ------
        picks = (data.get("top_picks") or []) + (data.get("etf_picks") or [])
        home_run = next((p for p in picks if _is_home_run(p)), None)
        story_pick = home_run or (picks[0] if picks else None)

        scenes: list[tuple[str, str, object, list[str], str]] = []
        scenes.append(("coldopen", "Tonight on the Market Desk",
                       _coldopen_scene(plt, data, home_run),
                       _coldopen_script(data, home_run), "anchor"))
        scenes.append(("market", "The Market Open",
                       _market_scene(plt, data), _market_script(data),
                       "anchor"))
        if story_pick is not None:
            fig = pick_chart_fig(plt, story_pick)
            if fig is not None:
                title = ("Top Story — home-run setup" if home_run
                         else "Chart of the Day")
                scenes.append(("topstory", title, fig,
                               _pick_script(story_pick,
                                            home_run=home_run is not None),
                               "analyst"))
        else:
            scenes.append(("topstory", "Top Story — no setup today",
                           _card(plt, "Top Story: No Setup Today",
                                 [("Nothing cleared every gate.",
                                   {"weight": "bold", "size": 17, "color": _WARN}),
                                  ("No setup means no trade.", {"color": _MUT})]),
                           _no_pick_script(), "analyst"))
        if data.get("proposal"):
            scenes.append(("book", "Business Desk — the book",
                           _book_scene(plt, data), _book_script(data),
                           "anchor"))
        mv = _movers_scene(plt, data)
        if mv is not None:
            scenes.append(("movers", "Movers in the Book", mv,
                           _movers_script(data), "anchor"))
        cs = data.get("capstone")
        if cs and cs.get("rows"):
            scenes.append(("race", "Sports Desk — The Race",
                           _capstone_scene(plt, cs), _capstone_script(cs),
                           "sports"))
        try:
            scenes.append(("analysis", "Market Analysis — the OROS 500",
                           _analysis_scene(plt, data),
                           _analysis_script(data), "anchor"))
        except Exception as e:  # noqa: BLE001
            logger.info("market analysis segment skipped: %s", e)
        tk = _takeaway_key(data, home_run is not None)
        scenes.append(("takeaway", "The Takeaway", _takeaway_scene(plt, tk),
                       [_TAKEAWAYS[tk]], "anchor"))
        scenes.append(("signoff", "Disclosure & Credits",
                       _signoff_scene(plt, data), _signoff_script(data),
                       "anchor"))

        tmp = Path(tempfile.mkdtemp(prefix="oros_video_"))
        clips, chapters, cues, t = [], [], [], 0.0
        for i, (key, title, fig, segments, voice) in enumerate(scenes):
            segments = [s for s in (segments or []) if s and s.strip()]
            if not segments:
                plt.close(fig)
                continue
            png = tmp / f"scene{i}.png"
            fig.savefig(png, dpi=100, facecolor=fig.get_facecolor())
            plt.close(fig)
            mp3, timed = _narration_audio(segments, generate_audio, tmp, i,
                                          voice=voice)
            if mp3 is None:
                logger.warning("video: narration failed for scene %s", key)
                continue
            clip = tmp / f"clip{i}.mp4"
            if _scene_to_clip(png, Path(mp3), clip):
                clips.append(clip)
                dur = _mp3_duration(clip) or sum(d for _, d in timed)
                chapters.append({"title": title, "start_s": round(t, 2)})
                seg_t = t
                for text, d in timed:
                    cues.append((seg_t, seg_t + d, text))
                    seg_t += d
                t += dur
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

        # Sidecar metadata: chapters + captions for the YouTube publisher.
        try:
            _write_srt(cues, out.with_suffix(".srt"))
            day, total = _race_day()
            meta = {"date": data.get("date"), "regime": data.get("regime"),
                    "lesson": _lesson_key(), "race_day": day,
                    "race_total": total,
                    "home_run": (home_run or {}).get("ticker"),
                    "takeaway": _TAKEAWAYS[tk],
                    "breadth": data.get("breadth"),
                    "oros500": data.get("oros500"),
                    "chapters": chapters,
                    "duration_s": round(t, 1)}
            import json as _json
            out.with_suffix(".meta.json").write_text(_json.dumps(meta, indent=1))
        except Exception as e:  # noqa: BLE001
            logger.warning("video sidecar metadata failed: %s", e)

        logger.info("video brief written: %s (%d scenes, %.1f MB, %.0fs)",
                    out.name, len(clips), out.stat().st_size / 1e6, t)
        return out, (f"Market Desk video attached: {len(clips)} scenes — "
                     f"cold open, market open, top story"
                     f"{' (HOME RUN: ' + home_run['ticker'] + ')' if home_run else ''}, "
                     "business desk, the race, market analysis, takeaway, "
                     f"disclosure. Lesson of the day: {_lesson_key()}. "
                     "(Personal holdings excluded by design.)")
    except Exception as e:  # noqa: BLE001
        logger.warning("video brief failed: %s", e)
        return None, f"Video brief: unavailable today ({type(e).__name__})."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from ml.daily_report import build_report_data
    p, note = generate_video_brief(build_report_data())
    print(p, "|", note)
