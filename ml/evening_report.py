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
  5. The Second Look: re-scans the morning's universe off closing bars
     (journaled separately, so the morning breadth entry survives) and
     reads the system's own scoreboard — wins/losses in the book, trailing
     expectancy, the self-imposed throttle (The Loop).
  6. Renders the Closing Bell video (verdict, second look, loop, tally),
     publishes to YouTube, emails it.

Wins and losses are DETERMINED HERE, in the evening — the morning only
proposes. Educational, not financial advice. OROS proposes and journals only.
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


def _index_day_moves() -> dict:
    """Day % for the index quartet, off the (just refreshed) cache."""
    from data import price_cache
    out = {}
    for t in ("SPY", "QQQ", "DIA", "IWM"):
        try:
            c = price_cache.get_history(t)["Close"].dropna()
            out[t] = 100.0 * (float(c.iloc[-1]) / float(c.iloc[-2]) - 1)
        except Exception:  # noqa: BLE001
            out[t] = None
    return out


def _evening_scan() -> dict | None:
    """The Second Look (JP, 2026-07-15): re-grade the same universe the
    morning scanned, off closing bars, journaled to its own file so the
    morning entry survives. Returns breadth.intraday_shift() or None.
    Cap via OROS_EVENING_SCAN_CAP (default 500); OROS_EVENING_SCAN=0
    disables. Best-effort — a failed re-scan never blocks the grading."""
    if os.environ.get("OROS_EVENING_SCAN", "1") == "0":
        return None
    try:
        from data import price_cache
        from data.universe import load_liquid_ranking
        from ml.breadth import intraday_shift, record_close_breadth
        cap = int(os.environ.get("OROS_EVENING_SCAN_CAP", "500"))
        symbols = (load_liquid_ranking() or [])[:cap]
        if not symbols:
            logger.warning("evening scan: no liquid ranking on disk; skipped")
            return None
        try:
            price_cache.refresh_prices(sorted(symbols))
        except Exception as e:  # noqa: BLE001
            logger.warning("evening scan refresh partial: %s", e)
        record_close_breadth(symbols)
        return intraday_shift()
    except Exception as e:  # noqa: BLE001
        logger.warning("evening scan failed: %s", e)
        return None


def _reflection(rec: dict, pnl_rows: list[dict], shift: dict | None) -> dict:
    """The Loop scene's data: the system reading its own scoreboard —
    today's wins and losses inside the book, trailing expectancy, the
    self-imposed throttle, and what tonight hands to tomorrow's open."""
    refl: dict = {}
    rows = [r for r in (pnl_rows or []) if r.get("day_pct") is not None]
    if rows:
        scored = sorted(((r["ticker"], float(r["day_pct"])) for r in rows),
                        key=lambda x: -x[1])
        refl["book"] = {"total": len(rows),
                        "up": sum(1 for _, p in scored if p > 0),
                        "down": sum(1 for _, p in scored if p < 0),
                        "best": scored[0], "worst": scored[-1]}
    mandate = (rec.get("mandate") or "swing_momentum").strip()
    try:
        from ml.portfolio_engine import drawdown_throttle
        thr, note = drawdown_throttle(mandate)
        refl["throttle"] = thr
        refl["throttle_line"] = (note or "throttle: full size "
                                 "(record too thin to judge yet)")
    except Exception as e:  # noqa: BLE001
        logger.warning("throttle read failed: %s", e)
    try:
        from ml.portfolio_journal import compare_by_mandate
        v = compare_by_mandate().get(mandate) or {}
        n, exp = v.get("positions_scored"), v.get("expectancy_pct")
        if n and exp is not None:
            refl["expectancy_line"] = (f"expectancy {exp:+.2f}% per trade "
                                       f"over {n} graded positions")
    except Exception as e:  # noqa: BLE001
        logger.warning("expectancy read failed: %s", e)
    # What tonight hands to tomorrow: honest, one line.
    parts = []
    close = (shift or {}).get("close") or {}
    opening = (shift or {}).get("open") or {}
    if close.get("total"):
        adv = (close.get("counts") or {}).get("Advancing", 0)
        parts.append(f"{adv} names close the day in Stage 2")
        if opening:
            d_inv = (int(close.get("invalidated") or 0)
                     - int(opening.get("invalidated") or 0))
            if d_inv > 0:
                parts.append(f"{d_inv} new invalidation"
                             f"{'s' if d_inv != 1 else ''} queue exit reviews")
    thr = refl.get("throttle", 1.0)
    if thr < 1.0:
        parts.append(f"position size stays cut to {thr:.0%} until the "
                     f"record earns it back")
    refl["tomorrow"] = ("; ".join(parts) + "." if parts else
                        "the gates reset at the open — no setup, no trade.")
    return refl


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

    # 5. The Second Look: re-scan the morning's universe off closing bars
    #    (separate journal file — the morning entry survives), then read
    #    the system's own scoreboard for The Loop scene.
    shift = _evening_scan()
    idx_moves = _index_day_moves()
    pnl_rows = (pnl.get("rows") or []) if rec else []
    refl = _reflection(rec, pnl_rows, shift)

    # 6. Video -> YouTube -> email. Full report data feeds the evening's
    # Movers and Race scenes (advisor restructure, 2026-07-27).
    data = None
    try:
        from ml.daily_report import build_report_data
        data = build_report_data()
    except Exception as e:  # noqa: BLE001
        logger.warning("evening report data unavailable: %s", e)
    video, note = generate_evening_brief(verdicts, tally, shift=shift,
                                         reflection=refl,
                                         idx_moves=idx_moves, data=data)
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
                   f"Season record vs frozen: {vf.get('wins', 0)}-{vf.get('losses', 0)}"]
    close_e = (shift or {}).get("close")
    if close_e and close_e.get("total"):
        open_e = (shift or {}).get("open")
        tot_c = max(int(close_e["total"]), 1)
        seg = []
        for k in ("Advancing", "Basing", "Topping", "Declining"):
            pc = 100 * (close_e["counts"] or {}).get(k, 0) / tot_c
            if open_e and open_e.get("total"):
                po = 100 * (open_e["counts"] or {}).get(k, 0) / max(int(open_e["total"]), 1)
                seg.append(f"{k} {po:.0f}%->{pc:.0f}%")
            else:
                seg.append(f"{k} {pc:.0f}%")
        body_lines += ["", "Second Look (evening re-scan vs open): " + "  ".join(seg),
                       f"Invalidations on watch: {close_e.get('invalidated', 0)}"]
    if refl.get("throttle_line"):
        body_lines.append(refl["throttle_line"])
    if refl.get("tomorrow"):
        body_lines.append("Tomorrow: " + refl["tomorrow"])
    body_lines += [watch,
                   "",
                   "Simulated paper trading. OROS proposes and journals only;"
                   " it never trades. Educational, not financial advice."]
    # Watch link instead of MP4 attachment (JP, 2026-07-29): the episode is
    # public on YouTube; attach the file only when publishing failed.
    send_email(title, "\n".join(body_lines), to=to,
               attachments=[video] if (video and not watch.strip()) else None)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    send_evening_report()
