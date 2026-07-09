"""
notifier.py - Change detection, opportunity scan, and alerts.

So nothing is left out, this module does three things, all token-free:
  1. detect_changes  - what changed in the portfolio vs the last journaled proposal
     (names added, names dropped), so you are told when the book moves.
  2. scan_opportunities - runs the IPO/new-listing track over young names (e.g. SPCX)
     to surface fresh breakouts the main constructed portfolio cannot see yet,
     so IPO-era opportunities are never missed.
  3. notify - a macOS notification (osascript) plus an appended alert log, so the
     unattended runner can tell you when something needs a look.

OROS never trades. Alerts are informational; you place any trade yourself.

    PYTHONPATH=. python3 ml/notifier.py
"""

from __future__ import annotations

import logging
import os
import platform
import smtplib
import subprocess
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger(__name__)
_ALERTS = Path(__file__).resolve().parent.parent / "data" / "journal" / "alerts.log"
_ALERTS.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
def detect_changes(new_holdings: list[str], prev_holdings: list[str]) -> dict:
    new_set, prev_set = set(new_holdings), set(prev_holdings)
    return {"added": sorted(new_set - prev_set),
            "removed": sorted(prev_set - new_set),
            "unchanged": sorted(new_set & prev_set)}


def scan_opportunities(symbols: list[str] | None = None, max_check: int = 400,
                       include: tuple[str, ...] = ("SPCX",)) -> dict:
    """Find young names (5-150 days) that are breaking out or basing - the SPCX case.
    `include` names are always refreshed and checked, even if not in the scan set, so
    a brand-new IPO you care about is never missed. Returns {breakouts, watch}. Token-free."""
    from data import price_cache
    from ml.new_listing_scanner import assess_new_listing
    syms = list(symbols or sorted(price_cache.cached_symbols()))
    # Pull any explicit IPO-watch names (plus an optional 'IPO Watch' scan list).
    extra = [s.upper() for s in include if s]
    try:
        from data.watchlists import load_watchlist, list_watchlists
        if "IPO Watch" in list_watchlists():
            extra += [s.upper() for s in load_watchlist("IPO Watch")]
    except Exception:
        pass
    extra = list(dict.fromkeys(extra))
    if extra:
        price_cache.refresh_prices(extra)
    ordered = extra + [s for s in syms if s not in set(extra)]
    breakouts, watch = [], []
    checked = 0
    for s in ordered:
        if checked >= max_check and s not in extra:
            break
        df = price_cache.get_history(s)
        if df is None or not (5 <= len(df) < 150):
            continue
        checked += 1
        r = assess_new_listing(s, df=df)
        if r.setup == "BREAKOUT":
            breakouts.append({"ticker": s, "score": r.score, "stop": r.suggested_stop,
                              "note": r.action})
        elif r.setup in ("BASING", "EARLY_UPTREND", "TOO_NEW"):
            watch.append({"ticker": s, "setup": r.setup, "days": r.days_listed})
    breakouts.sort(key=lambda x: -x["score"])
    return {"breakouts": breakouts, "watch": watch}


def _load_env_fallback() -> None:
    """Best-effort .env load so unattended runners (launchd, GitHub Actions with a
    written .env) get Gmail credentials even when the shell didn't export them."""
    if os.environ.get("GMAIL_ADDRESS"):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass


def send_email(subject: str, body: str, to: str | None = None,
               attachments: list | None = None) -> bool:
    """Send an email via Gmail SMTP, optionally with file attachments.
    No-op (returns False) if unconfigured — this has bitten us before: with no
    GMAIL_ADDRESS/GMAIL_APP_PASSWORD in .env every alert silently went nowhere.

    Set in .env: GMAIL_ADDRESS, GMAIL_APP_PASSWORD (a Google App Password, not your
    login password), and optionally NOTIFY_EMAIL_TO (defaults to GMAIL_ADDRESS).
    `to` overrides NOTIFY_EMAIL_TO for a single message (e.g. the daily report).
    """
    _load_env_fallback()
    user = os.environ.get("GMAIL_ADDRESS", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to = (to or "").strip() or os.environ.get("NOTIFY_EMAIL_TO", "").strip() or user
    if not (user and pw and to):
        logger.warning("email NOT sent (GMAIL_ADDRESS / GMAIL_APP_PASSWORD missing): %s",
                       subject)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, user, to
        msg.set_content(body)
        for p in attachments or []:
            p = Path(p)
            if not p.exists():
                continue
            maintype, subtype = {
                ".pdf": ("application", "pdf"),
                ".mp3": ("audio", "mpeg"),
                ".mp4": ("video", "mp4"),
                ".md": ("text", "markdown"),
            }.get(p.suffix, ("application", "octet-stream"))
            msg.add_attachment(p.read_bytes(), maintype=maintype,
                               subtype=subtype, filename=p.name)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, pw)
            s.send_message(msg)
        logger.info("email sent to %s: %s", to, subject)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("email send failed: %s", e)
        return False


def notify(title: str, body: str) -> None:
    """Alert log + macOS notification + email (phone). Each is best-effort."""
    line = f"{datetime.utcnow():%Y-%m-%d %H:%M}Z | {title} | {body}"
    try:
        with _ALERTS.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass
    if platform.system() == "Darwin":
        try:
            safe = body.replace('"', "'")[:240]
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{safe}" with title "{title}"'],
                check=False, timeout=10)
        except Exception:
            pass
    send_email(title, body)              # -> phone via Gmail (if configured)
    logger.info("ALERT: %s", line)


def build_alert(changes: dict, opps: dict) -> tuple[str, str] | None:
    """Compose an alert from changes + opportunities, or None if nothing notable."""
    bits = []
    if changes.get("added"):
        bits.append("added " + ", ".join(changes["added"][:6]))
    if changes.get("removed"):
        bits.append("dropped " + ", ".join(changes["removed"][:6]))
    if opps.get("breakouts"):
        bits.append("IPO breakout: " + ", ".join(o["ticker"] for o in opps["breakouts"][:5]))
    if not bits:
        return None
    return ("OROS: portfolio update", "; ".join(bits))


def _demo() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ch = detect_changes(["NVDA", "AMD", "AAPL"], ["NVDA", "INTC"])
    print("changes:", ch)
    opps = scan_opportunities(max_check=200)
    print("opportunities:", {"breakouts": [o["ticker"] for o in opps["breakouts"]],
                             "watch": [w["ticker"] for w in opps["watch"]][:10]})
    alert = build_alert(ch, opps)
    if alert:
        notify(*alert)
        print("notified:", alert)
    else:
        print("nothing notable")


if __name__ == "__main__":
    _demo()
