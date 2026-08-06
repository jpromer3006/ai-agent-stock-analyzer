"""
oros_config.py - Single source of truth for system-identity constants.

Born from the 787 incident (2026-08-05): the scan cap lived in FOUR places
(GitHub workflow, oros_cron.sh, oros_watchdog.sh, argparse default) and two
of them still said 800 weeks after the system standardized on 500. The
breadth journal quietly filled with 787-name entries and the panel froze.

Rule: any constant that defines what the system IS (the OROS 500 is
literally the product's name) is declared exactly once — here. Entry
points read it; none of them restate it.

Provenance: every unattended entry point exports OROS_RUN_SOURCE so journal
entries can say who wrote them (cloud / cron / watchdog / app-button /
scan-now). An audit becomes a one-line grep instead of forensics.
"""

from __future__ import annotations

import os

# The OROS 500: how many liquid names the daily scan grades.
# Override with the OROS_CAP env var (demos, experiments) — never by
# hard-coding a number in a runner script.
OROS_CAP: int = int(os.environ.get("OROS_CAP", "500"))

# How many names the pre-scan price refresh may touch.
OROS_REFRESH_CAP: int = int(os.environ.get("OROS_REFRESH_CAP", "1500"))


def run_source(default: str = "local") -> str:
    """Who is running this process — stamped into journal entries."""
    return os.environ.get("OROS_RUN_SOURCE", default)
