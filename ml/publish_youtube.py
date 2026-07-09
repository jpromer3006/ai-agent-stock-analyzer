"""
publish_youtube.py - Publish the OROS Market Desk episode to YouTube (unlisted).

Uses plain HTTPS (requests) with an OAuth refresh token - no Google SDK
dependency. One-time setup: scripts/yt_authorize.py mints the refresh token
(see docs/YOUTUBE_SETUP.md); then these live in .env and GitHub Secrets:

    YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN

Videos are uploaded UNLISTED by design (link-shareable, not public) pending
any decision to go public - see the advising/publisher discussion. OROS_YT=0
disables. Skips cleanly when unconfigured.

    PYTHONPATH=. python3 ml/publish_youtube.py path/to/video.mp4 "Title"
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
               "?uploadType=resumable&part=snippet,status")

_DESCRIPTION = """The OROS Market Desk - an autonomous AI market analysis briefing.

Generated end-to-end by software: market scan, portfolio construction,
chart analysis, script, and voice. No human edits.

All figures are SIMULATED paper trading. OROS proposes and journals only -
it never trades. Nothing in this video is financial advice. Educational
project - Fordham University, MS in AI in Business.
"""


def _load_env_fallback() -> None:
    if os.environ.get("YT_REFRESH_TOKEN"):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass


def _access_token() -> str | None:
    _load_env_fallback()
    cid = os.environ.get("YT_CLIENT_ID", "").strip()
    sec = os.environ.get("YT_CLIENT_SECRET", "").strip()
    ref = os.environ.get("YT_REFRESH_TOKEN", "").strip()
    if not (cid and sec and ref):
        return None
    r = requests.post(_TOKEN_URL, data={
        "client_id": cid, "client_secret": sec,
        "refresh_token": ref, "grant_type": "refresh_token"}, timeout=30)
    if r.status_code != 200:
        logger.warning("YouTube token refresh failed: %s %s", r.status_code, r.text[:200])
        return None
    return r.json().get("access_token")


def publish_episode(video_path: Path | str,
                    title: str | None = None) -> tuple[str | None, str]:
    """Upload an episode. Returns (watch_url or None, status line)."""
    if os.environ.get("OROS_YT", "1") == "0":
        return None, "YouTube: disabled (OROS_YT=0)."
    video_path = Path(video_path)
    if not video_path.exists():
        return None, "YouTube: no video file to publish."
    token = _access_token()
    if token is None:
        return None, ("YouTube: skipped (YT_CLIENT_ID / YT_CLIENT_SECRET / "
                      "YT_REFRESH_TOKEN not configured).")
    title = title or f"OROS Market Desk — {datetime.now():%B %d, %Y}"
    meta = {
        "snippet": {"title": title, "description": _DESCRIPTION,
                    "categoryId": "25",
                    "tags": ["OROS", "market analysis", "AI", "paper trading",
                             "educational"]},
        "status": {"privacyStatus": "unlisted",
                   "selfDeclaredMadeForKids": False},
    }
    try:
        size = video_path.stat().st_size
        r = requests.post(_UPLOAD_URL, json=meta, timeout=60, headers={
            "Authorization": f"Bearer {token}",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4"})
        if r.status_code not in (200, 201) or "Location" not in r.headers:
            logger.warning("YouTube session init failed: %s %s",
                           r.status_code, r.text[:300])
            return None, f"YouTube: upload init failed ({r.status_code})."
        put = requests.put(r.headers["Location"], data=video_path.read_bytes(),
                           timeout=300, headers={
                               "Authorization": f"Bearer {token}",
                               "Content-Type": "video/mp4"})
        if put.status_code not in (200, 201):
            logger.warning("YouTube upload failed: %s %s",
                           put.status_code, put.text[:300])
            return None, f"YouTube: upload failed ({put.status_code})."
        vid = put.json().get("id")
        url = f"https://youtu.be/{vid}"
        logger.info("episode published (unlisted): %s", url)
        return url, f"Watch today's episode (unlisted): {url}"
    except Exception as e:  # noqa: BLE001
        logger.warning("YouTube publish failed: %s", e)
        return None, f"YouTube: unavailable today ({type(e).__name__})."


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        from datetime import datetime as _dt
        path = (Path(__file__).resolve().parent.parent / "data" / "journal"
                / "reports" / f"oros_brief_{_dt.now():%Y-%m-%d}.mp4")
    url, note = publish_episode(path, sys.argv[2] if len(sys.argv) > 2 else None)
    print(url, "|", note)
