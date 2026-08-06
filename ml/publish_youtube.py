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

_GLOSSARY = """GLOSSARY
30-week moving average: the average weekly closing price over 30 weeks — the trend's spine.
Stage 1-4 (Weinstein): Basing (spring), Advancing (summer), Topping (autumn), Declining (winter).
Buy-stop: a conditional entry ABOVE the market — you buy only if price proves demand by rising through it.
Stop-loss: the pre-planned exit, taken on a weekly close below, never an afternoon scare.
Expectancy: average profit or loss per graded trade — the system's honest report card.
Throttle: OROS cuts its own position sizes when its forward record weakens."""

_SOURCES = """SOURCES & AGENTS
Data: SEC EDGAR XBRL company filings, FRED (Federal Reserve) economic data, daily market prices.
Agents: universe scanner, stage classifier, router, sector specialists, sentiment analyst, portfolio builder, risk manager (veto power), and this news desk.
Methodologies are referenced for attribution only; no author endorses or is affiliated with OROS.
No human intervention: the system scans, builds, risk-checks, writes, and voices this briefing on its own."""


def _fmt_ts(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}" if s >= 3600 \
        else f"{s // 60}:{s % 60:02d}"


def build_description(meta: dict | None) -> str:
    """Rich, per-episode description: hook, chapters, the day's numbers,
    sources, glossary, disclaimer. Falls back to the static text."""
    if not meta:
        return _DESCRIPTION
    parts = []
    day = meta.get("race_day")
    hook = ["An autonomous AI runs three simulated $100,000 books and "
            "publishes its record daily, win or lose."]
    if day:
        hook.append(f"Race day {day} — live book vs frozen book "
                    f"vs the S&P 500, updated every trading day.")
    if meta.get("home_run"):
        hr = meta["home_run"]
        try:
            from data.universe import company_name
            n = company_name(hr)
            hr = f"{n} ({hr})" if n else hr
        except Exception:  # noqa: BLE001
            pass
        hook.append(f"Today's TOP STORY: {hr} cleared the "
                    "strict home-run bar (fresh breakout, 3-to-1 reward on "
                    "risk, 2x volume).")
    parts.append(" ".join(hook))
    ch = meta.get("chapters") or []
    if ch:
        # YouTube chapters require the first stamp to be 0:00.
        lines = ["CHAPTERS"]
        if ch[0].get("start_s", 0) > 0:
            ch = [{"title": ch[0]["title"], "start_s": 0}] + ch[1:]
        for c in ch:
            lines.append(f"{_fmt_ts(float(c.get('start_s') or 0))} {c['title']}")
        parts.append("\n".join(lines))
    br = meta.get("breadth") or {}
    if br.get("counts") and br.get("total"):
        tot = max(br["total"], 1)
        c = br["counts"]
        parts.append("MARKET ANALYSIS — the OROS 500 by stage\n"
                     + "  ".join(f"{k}: {v} ({100 * v / tot:.0f}%)"
                                 for k, v in c.items())
                     + f"\nInvalidations on watch: {br.get('invalidated', 0)}")
    # Full PDF report link (JP, 2026-07-18): appears only when the public
    # reports mirror is configured (OROS_REPORT_URL_BASE env/variable).
    base = os.environ.get("OROS_REPORT_URL_BASE", "").strip().rstrip("/")
    if base:
        from datetime import datetime as _dt2
        parts.append("FULL REPORT (PDF)\n"
                     f"{base}/oros_report_{_dt2.now():%Y-%m-%d}.pdf")
    if meta.get("takeaway"):
        parts.append(meta["takeaway"].replace("Tonight's takeaway. ",
                                              "THE TAKEAWAY\n"))
    parts.append(_SOURCES)
    parts.append(_GLOSSARY)
    parts.append(_DESCRIPTION.strip())
    return "\n\n".join(parts)[:4900]     # YouTube caps descriptions at 5000


def upload_captions(video_id: str, srt_path: Path, token: str) -> bool:
    """Best-effort captions.insert so the Transcript panel populates.
    Requires the youtube.force-ssl scope; failures are logged, never fatal."""
    try:
        srt = srt_path.read_bytes()
        import json as _json
        boundary = "oros_caption_boundary"
        snippet = _json.dumps({"snippet": {"videoId": video_id,
                                           "language": "en",
                                           "name": "OROS Market Desk",
                                           "isDraft": False}})
        body = (f"--{boundary}\r\nContent-Type: application/json; "
                f"charset=UTF-8\r\n\r\n{snippet}\r\n"
                f"--{boundary}\r\nContent-Type: application/octet-stream"
                f"\r\n\r\n").encode() + srt + f"\r\n--{boundary}--".encode()
        r = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/captions"
            "?uploadType=multipart&part=snippet",
            data=body, timeout=120, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}"})
        ok = r.status_code in (200, 201)
        if not ok:
            logger.warning("caption upload failed: %s %s",
                           r.status_code, r.text[:200])
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning("caption upload failed: %s", e)
        return False


def _load_env_fallback() -> None:
    if os.environ.get("YT_REFRESH_TOKEN"):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass


def _access_token() -> str | None:
    """Returns an access token, None when YT_* are unconfigured, or ""
    when credentials exist but the refresh was REJECTED — callers must
    surface that last case loudly (root cause of the silent 2026-07-14
    no-upload day: a stale refresh token failed here and the report
    email hid it behind the 'not configured' filter)."""
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
        return ""
    return r.json().get("access_token")


def episode_title(data: dict) -> str:
    """A headline that carries the date AND the day's finding, so the channel
    reads like a dated broadcast archive at a glance."""
    from datetime import datetime as _dt
    try:
        from zoneinfo import ZoneInfo
        now = _dt.now(ZoneInfo("America/New_York"))
    except Exception:
        now = _dt.now()
    regime = (data.get("regime") or "").upper() or "MARKET"
    picks = (data.get("top_picks") or []) + (data.get("etf_picks") or [])
    home_run = None
    try:
        from ml.daily_video import _is_home_run
        home_run = next((p for p in picks if _is_home_run(p)), None)
    except Exception:
        pass
    def _named(t: str) -> str:
        try:
            from data.universe import company_name
            n = company_name(t)
            return f"{n} ({t})" if n else t
        except Exception:  # noqa: BLE001
            return t

    if home_run:
        hook = f"HOME RUN setup: {_named(home_run['ticker'])}"
    elif picks:
        hook = f"Pick: {', '.join(_named(p['ticker']) for p in picks[:2])}"
    else:
        hook = "No setup today"
    # YouTube caps titles at 100 chars; keep the date + regime, trim the hook.
    title = f"OROS Market Desk — {now:%b %d, %Y} · {regime} · {hook}"
    return title if len(title) <= 100 else title[:97] + "..."


def _add_to_playlist(video_id: str, playlist_title: str, token: str) -> bool:
    """Best-effort: find the channel playlist by title and append the video.
    (Morning Desk / Closing Bell playlists created by JP, 2026-07-14.)
    Needs the youtube or youtube.force-ssl scope; failures never block."""
    try:
        hdr = {"Authorization": f"Bearer {token}"}
        r = requests.get("https://www.googleapis.com/youtube/v3/playlists",
                         params={"part": "snippet", "mine": "true",
                                 "maxResults": 50}, headers=hdr, timeout=30)
        if r.status_code != 200:
            logger.warning("playlist lookup failed: %s %s",
                           r.status_code, r.text[:150])
            return False
        pid = next((it["id"] for it in r.json().get("items", [])
                    if it["snippet"]["title"].strip().lower()
                    == playlist_title.strip().lower()), None)
        if not pid:
            logger.warning("playlist %r not found on channel", playlist_title)
            return False
        r = requests.post(
            "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet",
            json={"snippet": {"playlistId": pid, "resourceId": {
                "kind": "youtube#video", "videoId": video_id}}},
            headers=hdr, timeout=30)
        ok = r.status_code in (200, 201)
        if ok:
            logger.info("added %s to playlist %r", video_id, playlist_title)
        else:
            logger.warning("playlist insert failed: %s %s",
                           r.status_code, r.text[:150])
        return ok
    except Exception as e:  # noqa: BLE001
        logger.warning("playlist add failed: %s", e)
        return False


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
    if token == "":
        return None, ("YouTube: TOKEN REFRESH FAILED — the refresh token was "
                      "rejected. Re-run scripts/yt_authorize.py and update "
                      "the YT_REFRESH_TOKEN GitHub Actions secret.")
    title = title or f"OROS Market Desk — {datetime.now():%B %d, %Y}"
    # Sidecar metadata from the video builder → rich description + captions.
    ep_meta = None
    meta_path = video_path.with_suffix(".meta.json")
    if meta_path.exists():
        try:
            import json as _json
            ep_meta = _json.loads(meta_path.read_text())
        except Exception:  # noqa: BLE001
            pass
    # Public by default since 2026-07-14 (channel went public). Override with
    # OROS_YT_PRIVACY=unlisted for dry runs.
    privacy = os.environ.get("OROS_YT_PRIVACY", "public").strip() or "public"
    meta = {
        "snippet": {"title": title, "description": build_description(ep_meta),
                    "categoryId": "25",
                    "tags": ["OROS", "market analysis", "AI", "paper trading",
                             "educational", "AI agents", "Weinstein",
                             "stage analysis"]},
        "status": {"privacyStatus": privacy,
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
        srt = video_path.with_suffix(".srt")
        if vid and srt.exists():
            if upload_captions(vid, srt, token):
                logger.info("captions uploaded for %s", vid)
        pl_note = ""
        if vid:
            playlist = ("Closing Bell"
                        if (ep_meta or {}).get("edition") == "evening"
                        else "Morning Desk")
            if not _add_to_playlist(vid, playlist, token):
                pl_note = (f" (WARNING: not added to playlist {playlist!r} — "
                           "check token scope includes youtube.force-ssl)")
        logger.info("episode published (%s): %s", privacy, url)
        return url, f"Watch today's episode ({privacy}): {url}{pl_note}"
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
