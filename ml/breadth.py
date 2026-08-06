"""
breadth.py - Daily stage breadth of the OROS 500 (the Market Analysis segment).

Weinstein's stages are the market's seasons. This module tallies how many of
the scanned universe sit in each stage (Basing / Advancing / Topping /
Declining) plus invalidations, and journals one entry per trading day,
append-only and idempotent (a re-run replaces that day's entry, never the
past). From the second entry onward the broadcast can report the SHIFT —
"Advancing grew from 48 to 52 percent" — which is the forecast feel.

Runs off the in-process stage cache the orchestrator just warmed; cache
misses recompute from the on-disk price cache (pure pandas, no network).

Journal: data/journal/stage_breadth.json
Educational, not financial advice.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_J = Path(__file__).resolve().parent.parent / "data" / "journal"
_FILE = _J / "stage_breadth.json"

STAGE_ORDER = ("Advancing", "Basing", "Topping", "Declining")


_CLOSE_FILE = _J / "stage_breadth_close.json"


def _tally_stages(symbols: list[str]) -> dict:
    """Stage counts across `symbols` off the on-disk price cache."""
    from data import price_cache
    from ml.oros_config import OROS_CAP, run_source
    from ml.stage_analyzer import analyze_stage

    # Write-time drift alarm (2026-08-05 lesson): validate at the producer,
    # never patch at the reader. A read-side filter once hid a week of
    # oversized entries and froze the panel at 7/28.
    if len(symbols) > OROS_CAP:
        logger.warning(
            "CONFIG DRIFT: breadth tally over %d symbols but OROS_CAP=%d — "
            "some entry point is carrying its own cap (source=%s)",
            len(symbols), OROS_CAP, run_source())

    counts = {k: 0 for k in STAGE_ORDER}
    invalidated = graded = 0
    for t in symbols:
        try:
            df = price_cache.get_history(t)
            r = analyze_stage(t, df=df, use_cache=True)
        except Exception:  # noqa: BLE001
            continue
        if getattr(r, "error", None) or not getattr(r, "stage_name", None):
            continue
        name = r.stage_name if r.stage_name in counts else None
        if name is None:
            continue
        counts[name] += 1
        graded += 1
        if getattr(r, "is_invalidated", False):
            invalidated += 1
    return {"date": f"{datetime.now():%Y-%m-%d}", "total": graded,
            "counts": counts, "invalidated": invalidated,
            # Provenance: who wrote this entry (cloud / cron / watchdog /
            # app-button / scan-now / local). Audits become a one-line grep.
            "source": run_source()}


def _journal(entry: dict, path: Path) -> None:
    hist = {"entries": []}
    if path.exists():
        try:
            hist = json.loads(path.read_text())
        except Exception:  # noqa: BLE001
            pass
    entries = hist.get("entries") or []
    if entries and entries[-1].get("date") == entry["date"]:
        entries[-1] = entry                       # idempotent per date
    else:
        entries.append(entry)
    _J.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}, indent=1))


def _entry_from_results(results) -> dict:
    """Tally an entry from ALREADY-COMPUTED StageResults (no re-grading).

    Uniformity fix (JP, 2026-08-06): re-grading symbols after a scan used a
    second pass over the price cache, and borderline names flipped between
    Basing and Advancing — the journaled panel disagreed with the very scan
    that wrote it (159 vs 177 Basing). One scan, one grade: the journal now
    derives from the same StageResult objects the UI renders.
    """
    from ml.oros_config import OROS_CAP, run_source
    if len(results) > OROS_CAP:
        logger.warning(
            "CONFIG DRIFT: breadth from %d results but OROS_CAP=%d "
            "(source=%s)", len(results), OROS_CAP, run_source())
    counts = {k: 0 for k in STAGE_ORDER}
    invalidated = graded = 0
    for r in results:
        if getattr(r, "error", None):
            continue
        name = getattr(r, "stage_name", None)
        if name not in counts:
            continue
        counts[name] += 1
        graded += 1
        if getattr(r, "is_invalidated", False):
            invalidated += 1
    return {"date": f"{datetime.now():%Y-%m-%d}", "total": graded,
            "counts": counts, "invalidated": invalidated,
            "source": run_source()}


def record_breadth_from_results(results) -> dict:
    """Journal today's breadth from a finished scan's StageResults —
    the Scan Now path. Idempotent per date, same file as record_breadth."""
    entry = _entry_from_results(results)
    _journal(entry, _FILE)
    logger.info("stage breadth journaled from scan results: %s of %d graded",
                entry["counts"], entry["total"])
    return entry


def record_breadth(symbols: list[str]) -> dict:
    """Tally stages across `symbols` and journal today's (morning) entry."""
    entry = _tally_stages(symbols)
    _journal(entry, _FILE)
    logger.info("stage breadth journaled: %s of %d graded",
                entry["counts"], entry["total"])
    return entry


def record_close_breadth(symbols: list[str]) -> dict:
    """The evening Second Look: tally stages after the close, journaled to
    a separate file so the morning entry survives the day. The Closing
    Bell diffs the two — that intraday shift is the market insight."""
    entry = _tally_stages(symbols)
    _journal(entry, _CLOSE_FILE)
    logger.info("closing stage breadth journaled: %s of %d graded",
                entry["counts"], entry["total"])
    return entry


def intraday_shift() -> dict | None:
    """Today's morning entry vs tonight's close entry.
    Returns {"open": entry|None, "close": entry} or None when no close
    entry exists for today."""
    def _last(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            entries = json.loads(path.read_text()).get("entries") or []
        except Exception:  # noqa: BLE001
            return None
        return entries[-1] if entries else None

    today = f"{datetime.now():%Y-%m-%d}"
    close = _last(_CLOSE_FILE)
    if not close or close.get("date") != today:
        return None
    opening = _last(_FILE)
    if opening and opening.get("date") != today:
        opening = None
    return {"open": opening, "close": close}


def latest_breadth(lookback: int = 5) -> dict | None:
    """Latest entry, plus the comparison entry ~`lookback` runs earlier
    (or the oldest available) so the segment can narrate the shift."""
    if not _FILE.exists():
        return None
    try:
        entries = json.loads(_FILE.read_text()).get("entries") or []
    except Exception:  # noqa: BLE001
        return None
    if not entries:
        return None
    # The newest entry wins, whatever its size (JP, 2026-08-05: "it should
    # relate to either the autonomous scan or the scan now" — never pinned
    # to an old date). The old <=500 filter hid oversized legacy-cap entries
    # and froze the panel at the last clean date; the cap fossils in
    # oros_cron.sh / oros_watchdog.sh are fixed at the source instead, and
    # the panel reports the entry's real total so it stays honest either way.
    cur = dict(entries[-1])
    prior = entries[max(0, len(entries) - 1 - lookback)] if len(entries) > 1 else None
    cur["prior"] = prior if (prior and prior.get("date") != cur["date"]) else None
    return cur


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    b = latest_breadth()
    if not b:
        print("no breadth journaled yet")
    else:
        tot = max(b["total"], 1)
        for k in STAGE_ORDER:
            print(f"  {k:10} {b['counts'][k]:4}  ({100*b['counts'][k]/tot:.0f}%)")
        print(f"  invalidated {b['invalidated']}  as of {b['date']}"
              + (f"  (prior {b['prior']['date']})" if b.get("prior") else ""))
