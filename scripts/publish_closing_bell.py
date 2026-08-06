"""
publish_closing_bell.py — render tonight's Closing Bell and publish it to
the channel WITHOUT re-sending any email (JP, 2026-08-06: the watchdog's
evening fallback emailed the report but skipped the video on a launchd
PATH miss; this backfills the episode).

Mirrors ml/evening_report.send_evening_report steps 2-6 exactly — same
grading, same journals (all idempotent per date), same scenes, same title —
and stops before the email.

    PYTHONPATH=. python3 scripts/publish_closing_bell.py            # public (the channel default)
    OROS_YT_PRIVACY=unlisted PYTHONPATH=. python3 scripts/publish_closing_bell.py

Run from Terminal (your shell PATH has ffmpeg). No email, upload only.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("publish_closing_bell")


def main() -> int:
    from ml import picks_ledger
    from ml.capstone_tracker import baseline_status
    from ml.daily_report import (_holdings_pnl, _latest_proposal,
                                 build_report_data)
    from ml.daily_video import generate_evening_brief
    from ml.evening_report import (_day_pct_from_rows, _evening_scan,
                                   _index_day_moves, _reflection,
                                   _spy_day_pct)
    from ml.publish_youtube import publish_episode

    rec = _latest_proposal() or {}
    acct = float(rec.get("account") or 100_000.0)
    verdicts = picks_ledger.grade()
    pnl = _holdings_pnl(rec) if rec else {}
    live_pct = _day_pct_from_rows(pnl.get("rows") or [], acct) if rec else None
    cs = baseline_status()
    frozen_pct = _day_pct_from_rows((cs or {}).get("rows") or [],
                                    float((cs or {}).get("account") or acct))
    tally = picks_ledger.record_day_tally(live_pct, frozen_pct,
                                          _spy_day_pct())
    shift = _evening_scan()
    refl = _reflection(rec, (pnl.get("rows") or []) if rec else [], shift)
    data = None
    try:
        data = build_report_data()
    except Exception as e:  # noqa: BLE001
        log.warning("report data unavailable: %s", e)

    log.info("rendering the Closing Bell ...")
    video, note = generate_evening_brief(verdicts, tally, shift=shift,
                                         reflection=refl,
                                         idx_moves=_index_day_moves(),
                                         data=data)
    log.info("%s", note)
    if video is None or not str(video).endswith(".mp4"):
        log.error("no video to publish — is ffmpeg on PATH here?")
        return 1

    hr = next((v["ticker"] for v in verdicts if v["verdict"] == "home_run"),
              None)
    graded = [v for v in verdicts if v["verdict"] != "ungraded"]
    hook = (f"HOME RUN confirmed: {hr}" if hr
            else ", ".join(f"{v['ticker']} {v['verdict'].replace('_', ' ')}"
                           for v in graded[:2]) if graded
            else "no pick to grade")
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001
        now = datetime.now()
    title = f"OROS Closing Bell — {now:%b %d, %Y} · {hook}"

    url, yt_note = publish_episode(str(video), title)
    if url:
        log.info("PUBLISHED: %s", url)
        return 0
    log.error("upload failed: %s — MP4 saved at %s", yt_note, video)
    return 1


if __name__ == "__main__":
    sys.exit(main())
