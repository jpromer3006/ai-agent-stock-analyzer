#!/usr/bin/env python3
"""
yt_authorize.py - One-time YouTube OAuth (lunch-break edition).

Run this ONCE on the Mac after creating the OAuth client in Google Cloud
(docs/YOUTUBE_SETUP.md). It opens your browser for Google's consent screen,
catches the redirect locally, exchanges the code, prints the refresh token,
and appends YT_* lines to .env.

    cd "/Users/jpromero/Ai-Agent Oros"
    python3 scripts/yt_authorize.py CLIENT_ID CLIENT_SECRET
      OR (read the downloaded JSON directly):
    python3 scripts/yt_authorize.py docs/client_secret_XXXX.json
"""

from __future__ import annotations

import http.server
import json
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

PORT = 8765
REDIRECT = f"http://localhost:{PORT}"
# upload alone can't insert captions or add to playlists; force-ssl covers
# uploads, captions, and playlist management (JP, 2026-07-14).
SCOPE = ("https://www.googleapis.com/auth/youtube.upload "
         "https://www.googleapis.com/auth/youtube.force-ssl")
_code: list[str] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q:
            _code.append(q["code"][0])
            body = b"<h2>OROS is authorized. You can close this tab.</h2>"
        else:
            body = b"<h2>No code received - try again.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def main() -> int:
    # Accept either two args (id, secret) OR one arg (path to the client_secret
    # JSON Google downloads) — JP downloaded the JSON, so support it directly.
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        d = json.loads(Path(sys.argv[1]).read_text())
        k = d.get("installed") or d.get("web") or {}
        cid, sec = k.get("client_id", "").strip(), k.get("client_secret", "").strip()
        if not (cid and sec):
            print("Could not find client_id/client_secret in that JSON.")
            return 1
    elif len(sys.argv) == 3:
        cid, sec = sys.argv[1].strip(), sys.argv[2].strip()
    else:
        print(__doc__)
        return 1

    srv = http.server.HTTPServer(("localhost", PORT), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    auth_url = ("https://accounts.google.com/o/oauth2/v2/auth?" +
                urllib.parse.urlencode({
                    "client_id": cid, "redirect_uri": REDIRECT,
                    "response_type": "code", "scope": SCOPE,
                    "access_type": "offline", "prompt": "consent"}))
    print("Opening browser for Google consent...\nIf it doesn't open, visit:\n" + auth_url)
    webbrowser.open(auth_url)

    import time
    for _ in range(300):
        if _code:
            break
        time.sleep(1)
    srv.shutdown()
    if not _code:
        print("Timed out waiting for authorization.")
        return 1

    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": sec, "code": _code[0],
        "grant_type": "authorization_code", "redirect_uri": REDIRECT}, timeout=30)
    tok = r.json()
    ref = tok.get("refresh_token")
    if not ref:
        print("No refresh token returned:", json.dumps(tok)[:300])
        return 1

    env = Path(__file__).resolve().parent.parent / ".env"
    with env.open("a") as f:
        f.write(f"\n# YouTube (OROS Market Desk) - added by yt_authorize.py\n"
                f"YT_CLIENT_ID={cid}\nYT_CLIENT_SECRET={sec}\nYT_REFRESH_TOKEN={ref}\n")
    print("\nSUCCESS. Credentials appended to .env.")
    print("Now add the SAME three values as GitHub repository secrets:")
    print("  YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN")
    print("(Repo -> Settings -> Secrets and variables -> Actions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
