"""
oros500.py - The OROS 500: the branded, journaled scan universe.

The broadcast says "the OROS 500, the five hundred most liquid names our
scanner grades every night" — this module makes that claim true by
construction. The runner hands us the exact symbol list it scanned; we
journal it with entry/exit diffs vs the previous constituent list, giving
the Market Desk an index-rebalance story for free.

Journal: data/journal/oros500.json (latest constituents + last diff).
Educational, not financial advice.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_J = Path(__file__).resolve().parent.parent / "data" / "journal"
_FILE = _J / "oros500.json"

INDEX_NAME = "OROS 500"
TARGET_SIZE = 500


def record_constituents(symbols: list[str]) -> dict:
    """Journal today's scanned universe; diff vs the previous list."""
    prev_syms: list[str] = []
    if _FILE.exists():
        try:
            prev_syms = json.loads(_FILE.read_text()).get("symbols") or []
        except Exception:  # noqa: BLE001
            pass
    cur = list(dict.fromkeys(symbols))          # order-preserving dedupe
    prev_set, cur_set = set(prev_syms), set(cur)
    rec = {
        "name": INDEX_NAME,
        "date": f"{datetime.now():%Y-%m-%d}",
        "count": len(cur),
        "target": TARGET_SIZE,
        "entered": sorted(cur_set - prev_set) if prev_syms else [],
        "left": sorted(prev_set - cur_set) if prev_syms else [],
        "symbols": cur,
    }
    _J.mkdir(parents=True, exist_ok=True)
    _FILE.write_text(json.dumps(rec, indent=1))
    return rec


def load_constituents() -> dict | None:
    if not _FILE.exists():
        return None
    try:
        return json.loads(_FILE.read_text())
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    c = load_constituents()
    print("no constituents journaled yet" if not c else
          f"{c['name']} as of {c['date']}: {c['count']} names "
          f"(+{len(c['entered'])}/-{len(c['left'])} vs prior)")
