"""
test_scan_regime.py - Tests for the scan artifact and the regime ladder.

Written alongside the 2026-09-04 change that made the regime honest. The
old label came from SPY's stage alone and read BULL on all 14 recorded
sessions while under half the market advanced; these tests exist so it can
never silently collapse to one value again.

Run: PYTHONPATH=. python3 tests/test_scan_regime.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import market_context as mc  # noqa: E402

_FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        _FAILURES.append(name)


def _report(advancing: int, total: int = 500, declining: int = 50,
            topping: int = 20) -> dict:
    """A synthetic scan report with a chosen stage mix."""
    rows = []
    rows += [{"stage_name": "Advancing"}] * advancing
    rows += [{"stage_name": "Declining"}] * declining
    rows += [{"stage_name": "Topping"}] * topping
    rows += [{"stage_name": "Basing"}] * (total - advancing - declining - topping)
    return {"rows": rows, "session_date": "2026-09-04", "scan_hash": "test"}


def test_ladder():
    print("\nladder thresholds")
    cases = [(300, "BROAD BULL"), (275, "BROAD BULL"), (274, "NARROW BULL"),
             (225, "NARROW BULL"), (224, "MIXED"), (175, "MIXED"),
             (174, "DEFENSIVE"), (0, "DEFENSIVE")]
    for adv, expected in cases:
        out = mc.regime_from_scan(_report(adv), spy_stage=2, journal=False)
        check(f"{adv}/500 advancing -> {expected}",
              out["label"] == expected, f"got {out['label']}")


def test_spy_stage_is_a_ceiling():
    print("\nSPY stage can only downgrade, never promote")
    broad = _report(300)
    check("Stage 4 forces DEFENSIVE despite 60% advancing",
          mc.regime_from_scan(broad, spy_stage=4, journal=False)["label"] == "DEFENSIVE")
    check("Stage 3 caps a broad read at MIXED",
          mc.regime_from_scan(broad, spy_stage=3, journal=False)["label"] == "MIXED")
    check("Stage 1 does not promote a weak tape",
          mc.regime_from_scan(_report(100), spy_stage=1, journal=False)["label"] == "DEFENSIVE")


def test_label_is_not_constant():
    """The defect that started this: a label with one reachable value."""
    print("\nthe label actually varies with real recorded breadth")
    # Real Advancing percentages from data/journal/stage_breadth.json,
    # 2026-08-09 through 2026-09-04.
    real_pcts = [49.2, 49.2, 50.5, 51.5, 48.5, 47.9, 48.5, 47.4,
                 45.2, 44.8, 42.2, 42.4, 43.6, 44.8]
    labels = set()
    for pct in real_pcts:
        adv = int(round(pct * 5))          # out of 500
        labels.add(mc.regime_from_scan(_report(adv), spy_stage=2,
                                       journal=False)["label"])
    check("more than one label across recorded history",
          len(labels) > 1, f"got {labels}")
    check("plain BROAD BULL is not claimed on a 42-51% tape",
          "BROAD BULL" not in labels, f"got {labels}")


def test_spoken_numbers_come_from_the_scan():
    print("\nspoken line carries the run's own counts")
    out = mc.regime_from_scan(_report(223, declining=67), spy_stage=2,
                              journal=False)
    check("advancing count appears verbatim", "223 of 500" in out["spoken"],
          out["spoken"])
    check("declining count appears verbatim", "67 declining" in out["spoken"],
          out["spoken"])
    check("headline carries the scanned total", "500 scanned" in out["headline"],
          out["headline"])


def test_empty_scan_refuses_to_guess():
    print("\nan empty scan says so rather than inventing a read")
    out = mc.regime_from_scan({"rows": [], "session_date": "2026-09-04"},
                              journal=False)
    check("label is UNKNOWN", out["label"] == "UNKNOWN")
    check("no percentage claimed", out["pct_advancing"] == 0.0)


def test_streak_counts_consecutive_sessions():
    print("\nstreak counts back through matching sessions only")
    with tempfile.TemporaryDirectory() as d:
        original = mc._REGIME_FILE
        mc._REGIME_FILE = Path(d) / "regime_history.json"
        try:
            mc._REGIME_FILE.write_text(json.dumps({"entries": [
                {"date": "2026-09-01", "label": "MIXED"},
                {"date": "2026-09-02", "label": "MIXED"},
                {"date": "2026-09-03", "label": "MIXED"},
            ]}))
            out = mc.regime_from_scan(_report(223), spy_stage=2, journal=False)
            check("three prior MIXED sessions plus today = 4",
                  out["streak"] == 4, f"got {out['streak']}")
            check("streak is spoken", "4th straight" in out["spoken"],
                  out["spoken"])

            mc._REGIME_FILE.write_text(json.dumps({"entries": [
                {"date": "2026-09-02", "label": "NARROW BULL"},
                {"date": "2026-09-03", "label": "MIXED"},
            ]}))
            out = mc.regime_from_scan(_report(223), spy_stage=2, journal=False)
            check("a different label breaks the streak",
                  out["streak"] == 2, f"got {out['streak']}")
            check("a streak of 1 is not narrated",
                  "straight session" not in
                  mc.regime_from_scan(_report(300), spy_stage=2,
                                      journal=False)["spoken"])
        finally:
            mc._REGIME_FILE = original


def test_breadth_gate_excludes_basing():
    """The original defect: Stage 1 counted as healthy participation."""
    print("\nmarket_context breadth gate counts Stage 2 only")
    src = Path(__file__).resolve().parent.parent / "ml" / "market_context.py"
    text = src.read_text()
    check("healthy is Stage 2 alone",
          "healthy = len(buckets[2])\n" in text)
    check("the old Stage 1 + Stage 2 sum is gone",
          "len(buckets[2]) + len(buckets[1])" not in text)


if __name__ == "__main__":
    test_ladder()
    test_spy_stage_is_a_ceiling()
    test_label_is_not_constant()
    test_spoken_numbers_come_from_the_scan()
    test_empty_scan_refuses_to_guess()
    test_streak_counts_consecutive_sessions()
    test_breadth_gate_excludes_basing()
    print("\n" + ("ALL PASS" if not _FAILURES
                  else f"{len(_FAILURES)} FAILED: {_FAILURES}"))
    sys.exit(1 if _FAILURES else 0)
