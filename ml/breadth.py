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


def record_breadth(symbols: list[str]) -> dict:
    """Tally stages across `symbols` and journal today's entry."""
    from data import price_cache
    from ml.stage_analyzer import analyze_stage

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

    entry = {"date": f"{datetime.now():%Y-%m-%d}", "total": graded,
             "counts": counts, "invalidated": invalidated}

    hist = {"entries": []}
    if _FILE.exists():
        try:
            hist = json.loads(_FILE.read_text())
        except Exception:  # noqa: BLE001
            pass
    entries = hist.get("entries") or []
    if entries and entries[-1].get("date") == entry["date"]:
        entries[-1] = entry                       # idempotent per date
    else:
        entries.append(entry)
    _J.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps({"entries": entries}, indent=1))
    logger.info("stage breadth journaled: %s of %d graded", counts, graded)
    return entry


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
