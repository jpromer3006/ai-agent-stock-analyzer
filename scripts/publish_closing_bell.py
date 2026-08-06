"""
publish_closing_bell.py — render tonight's Closing Bell and publish it to
the channel WITHOUT re-sending any email (JP, 2026-08-06: the watchdog's
evening fallback emailed the report but skipped the video on a launchd
PATH miss; this backfills the episode).

    PYTHONPATH=. python3 scripts/publish_closing_bell.py            # unlisted
    OROS_YT_PRIVACY=public PYTHONPATH=. python3 scripts/publish_closing_bell.py

Run it from Terminal (your shell PATH has ffmpeg). Uses the journaled data
from today's runs — no rescan, no email, upload only.
"""

from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("publish_closing_bell")


def main() -> int:
    os.environ.setdefault("OROS_YT_PRIVACY", "unlisted")
    from ml.daily_report import build_report_data
    from ml.daily_video import generate_evening_brief
    from ml.publish_youtube import episode_title, publish_episode

    log.info("building report data from today's journals (no rescan) ...")
    data = build_report_data()
    log.info("rendering the Closing Bell ...")
    path, note = generate_evening_brief(data)
    if path is None:
        log.error("render failed: %s", note)
        return 1
    log.info("rendered: %s", path)
    title = episode_title(data).replace("OROS Market Desk", "OROS Closing Bell")
    url, pnote = publish_episode(str(path), title)
    if url:
        log.info("PUBLISHED (%s): %s", os.environ["OROS_YT_PRIVACY"], url)
        return 0
    log.error("upload failed: %s — MP4 is saved at %s", pnote, path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
