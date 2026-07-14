"""
evening_report.py - The Closing Bell run (JP, 2026-07-14).

The morning show proposes; this grades. Called by scripts/oros_run.py when
the edition resolves to "evening" (the 16:50 ET cron slot). It:

  1. Refreshes prices for only what grading needs (picks, book, indexes,
     frozen-book tickers) - fast, no full universe refresh.
  2. Grades the morning's Top Picks off the day's bar (picks_ledger.grade).
  3. Computes who won the day: live book vs frozen book vs SPY, and updates
     the running season record (append-only day_tally.json).
  4. Updates the capstone live chain with the day's return (so the morning
     and evening runs never double-append: chain is idempotent per date).
  5. Renders the short Closing Bell video, publishes to YouTube, emails it.

Educational, not financial advice. OROS proposes and journals only.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)


def _day_pct_from_rows(rows: list[dict], account: float) -> float | None:
    try:
        if not rows or not account:
            return None
        return 100.0 * sum(float(r.get("day_usd") or 0) for r in rows) / account
    except Exception:  # noqa: BLE001
        return None


def _spy_day_pct() -> float | None:
    try:
        from data import price_cache
        c = price_cache.get_history("SPY")["Close"].dropna()
        return 100.0 * (float(c.iloc[-1]) / float(c.iloc[-2]) - 1)
    except Exception:  # noqa: BLE001
        return None


def send_evening_report(to: str | None = None) -> None:
    from data import price_cache
    from ml import picks_ledger
    from ml.capstone_tracker import baseline_status, update_live_chain
    from ml.daily_report import _holdings_pnl, _latest_proposal
    from ml.daily_video import generate_evening_brief
    from ml.notifier import send_email

    to = (to or os.environ.get("OROS_REPORT_TO", "").strip()
          or "JPR20@fordham.edu")

    # 1. Targeted refresh: picks + live book + frozen book + indexes.
    need = {"SPY", "QQQ", "DIA", "IWM"}
    rec = _latest_proposal() or {}
    need |= {h["ticker"] for h in rec.get("holdings", [])}
    for v in picks_ledger.grade():                 # pre-read for tickers
        need.add(v["ticker"])
    cs0 = baseline_status()
    if cs0:
        need |= {r["ticker"] for r in cs0.get("rows", [])}
    try:
        price_cache.refresh_prices(sorted(need))
    except Exception as e:  # noqa: BLE001
        logger.warning("evening refresh failed (grading off cache): %s", e)

    # 2. Grade the morning picks off the fresh bars.
    verdicts = picks_ledger.grade()

    # 3. Who won the day + running record.
    acct = float(rec.get("account") or 100_000.0)
    live_pct = None
    if rec:
        pnl = _holdings_pnl(rec)
        live_pct = _day_pct_from_rows(pnl.get("rows") or [], acct)
    cs = baseline_status()
    frozen_pct = _day_pct_from_rows((cs or {}).get("rows") or [],
                                    float((cs or {}).get("account") or acct))
    spy_pct = _spy_day_pct()
    tally = picks_ledger.record_day_tally(live_pct, frozen_pct, spy_pct)

    # 4. Capstone live chain (idempotent per date - safe alongside morning).
    if live_pct is not None:
        try:
            update_live_chain(live_pct / 100.0)
        except Exception as e:  # noqa: BLE001
            logger.warning("live chain update failed: %s", e)

    # 5. Video -> YouTube -> email.
    video, note = generate_evening_brief(verdicts, tally)
    logger.info("%s", note)

    hr = next((v["ticker"] for v in verdicts if v["verdict"] == "home_run"),
              None)
    graded = [v for v in verdicts if v["verdict"] != "ungraded"]
    if hr:
        hook = f"HOME RUN confirmed: {hr}"
    elif graded:
        hook = ", ".join(f"{v['ticker']} {v['verdict'].replace('_', ' ')}"
                         for v in graded[:2])
    else:
        hook = "no pick to grade"
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        now = datetime.now()
    title = f"OROS Closing Bell — {now:%b %d, %Y} · {hook}"

    watch = ""
    if video is not None and str(video).endswith(".mp4"):
        try:
            from ml.publish_youtube import publish_episode
            url, yt_note = publish_episode(video, title)
            logger.info("%s", yt_note)
            watch = f"\nWatch: {url}" if url else ""
        except Exception as e:  # noqa: BLE001
            logger.warning("closing bell publish failed: %s", e)

    vf = tally.get("vs_frozen") or {}
    body_lines = [f"The OROS Closing Bell — {now:%b %d, %Y %H:%M %Z}", ""]
    for v in verdicts:
        pct = (f" ({v['vs_trigger_pct']:+.1f}% vs trigger)"
               if v.get("vs_trigger_pct") is not None else "")
        body_lines.append(f"  {v['ticker']}: {v['verdict'].replace('_', ' ')}{pct}")
    if not verdicts:
        body_lines.append("  No pick was on the tape this morning.")
    t = tally.get("today") or {}
    body_lines += ["",
                   f"Day: live {t.get('live_pct') if t.get('live_pct') is None else format(t['live_pct'], '+.2f')}%"
                   f" | frozen {t.get('frozen_pct') if t.get('frozen_pct') is None else format(t['frozen_pct'], '+.2f')}%"
                   f" | SPY {t.get('spy_pct') if t.get('spy_pct') is None else format(t['spy_pct'], '+.2f')}%",
                   f"Season record vs frozen: {vf.get('wins', 0)}-{vf.get('losses', 0)}",
                   watch,
                   "",
                   "Simulated paper trading. OROS proposes and journals only;"
                   " it never trades. Educational, not financial advice."]
    send_email(title, "\n".join(body_lines), to=to,
               attachments=[video] if video else None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    send_evening_report()
