"""
daily_broadcast.py - The OROS morning broadcast (JP's ask, 2026-07-06).

Turns the daily report data into a ~60-second news-style script and voices it
with ElevenLabs (data/audio_client, cached by text hash so an unchanged script
never re-bills). The centerpiece: the Pick of the Day explained in THREE
reasons, plain language, no jargon.

Skipped cleanly when ELEVENLABS_API_KEY is absent or OROS_AUDIO=0.
Educational, not financial advice. OROS never trades.

    PYTHONPATH=. python3 ml/daily_broadcast.py     # dry run: prints the script
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def build_broadcast_script(data: dict) -> str:
    """A tight news-desk script from the day's report data. ~800-1000 chars
    (ElevenLabs bills per character; a short script keeps the habit cheap)."""
    regime = (data.get("regime") or "unknown").upper()
    pnl = data.get("pnl") or {}
    rec = data.get("proposal") or {}
    acts = data.get("actions") or []
    picks = (data.get("top_picks") or []) + (data.get("etf_picks") or [])

    lines = [f"Good morning JP. This is your OROS market brief for {data.get('date', 'today')}."]

    if regime == "BULL":
        lines.append("The market is in a healthy uptrend.")
    elif regime == "BEAR":
        lines.append("The market is in a downtrend. Capital preservation comes first today.")
    else:
        lines.append(f"The market regime is {regime.lower()}.")

    n = len(pnl.get("rows") or [])
    day = pnl.get("total_day_usd", 0.0)
    up = pnl.get("total_unreal_usd", 0.0)
    lines.append(f"Your practice portfolio holds {n} positions, "
                 f"{'up' if day >= 0 else 'down'} {abs(day):,.0f} dollars today, "
                 f"{'up' if up >= 0 else 'down'} {abs(up):,.0f} overall.")

    if pnl.get("stops_hit"):
        lines.append("Attention: " + ", ".join(pnl["stops_hit"]) +
                     (" is" if len(pnl["stops_hit"]) == 1 else " are") +
                     " below the safety net. The report has the exit details.")
    elif acts:
        lines.append(f"Today's checklist has {len(acts)} item"
                     f"{'s' if len(acts) != 1 else ''}. Details in the report.")
    else:
        lines.append("Nothing needs your attention today. The book is behaving.")

    mb = data.get("my_book")
    if mb and mb.get("rows"):
        d = mb.get("day_usd", 0.0)
        macts = mb.get("actions") or []
        lines.append(f"Your real book is {'up' if d >= 0 else 'down'} "
                     f"{abs(d):,.0f} dollars today"
                     + (f", with {len(macts)} action item"
                        f"{'s' if len(macts) != 1 else ''} in the report."
                        if macts else ", and nothing needs your hand."))

    if picks:
        p = picks[0]
        kind = "fund" if p.get("is_etf") else "stock"
        lines.append(f"And now the pick of the day: {p['ticker']}. "
                     f"This {kind} earned the top spot for three reasons.")
        lines.append(f"One: it is in a confirmed uptrend, trading above its rising "
                     f"thirty-week average, and only {abs(p.get('pct_above_ma', 0)):.0f} percent "
                     f"above it, so it has not run away yet.")
        lines.append(f"Two: the move is backed by real buying, volume at "
                     f"{p.get('volume_surge', 1):.1f} times normal, and it is "
                     f"outperforming the S and P five hundred.")
        lines.append(f"Three: the trade plan is defined before any money moves. "
                     f"Entry only above {p.get('buy_stop', 0):,.2f}, safety net at "
                     f"{p.get('stop_loss', 0):,.2f}, target {p.get('target', 0):,.2f}.")
        lines.append("The full chart evidence and the research memo are attached.")
    else:
        lines.append("No pick of the day: nothing passed every quality gate, "
                     "and in this system, no setup means no trade.")

    lines.append("Remember: this is educational paper trading. OROS proposes and "
                 "journals only. It never trades. Have a good session.")
    return " ".join(lines)


def generate_broadcast(data: dict) -> tuple[Path | None, str]:
    """Script -> ElevenLabs mp3. Returns (path or None, status line)."""
    if os.environ.get("OROS_AUDIO", "1") == "0":
        return None, "Audio brief: disabled (OROS_AUDIO=0)."
    try:
        from data.audio_client import is_available, generate_audio
        if not is_available():
            return None, "Audio brief: skipped (no ELEVENLABS_API_KEY in this environment)."
        script = build_broadcast_script(data)
        path = generate_audio(script)
        if path is None:
            return None, "Audio brief: generation failed (see logs)."
        # A dated, human-readable copy next to the reports (cache keeps the original).
        from datetime import datetime
        out = Path(__file__).resolve().parent.parent / "data" / "journal" / "reports" \
            / f"oros_brief_{datetime.now():%Y-%m-%d}.mp3"
        out.write_bytes(Path(path).read_bytes())
        logger.info("audio brief written: %s (%d chars of script)", out.name, len(script))
        return out, "Audio brief attached: your 60-second OROS morning broadcast."
    except Exception as e:  # noqa: BLE001
        logger.warning("audio brief failed: %s", e)
        return None, f"Audio brief: unavailable today ({type(e).__name__})."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from ml.daily_report import build_report_data
    d = build_report_data()
    s = build_broadcast_script(d)
    print(f"--- script ({len(s)} chars) ---\n{s}")
