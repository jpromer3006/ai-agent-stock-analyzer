#!/bin/zsh
#
# oros_watchdog.sh - the guarantee layer (JP, 2026-07-08).
#
# Runs at 10:20 ET on weekdays via launchd (com.oros.watchdog). Checks whether
# the cloud runner already delivered today's journal commit; if yes, exits
# silently. If the cloud is silent, runs the full OROS morning locally so the
# report reaches JP's inbox with NO human intervention, ever.
#
# Worst case on a late-cloud day: duplicate emails. Never a missing report.

set -uo pipefail
PROJECT_DIR="/Users/jpromero/Ai-Agent Oros"
CONDA_BASE="/Users/jpromero/anaconda3"
CONDA_ENV="dev2"

cd "$PROJECT_DIR" || exit 1
LOG="data/journal/watchdog_$(date +%Y-%m-%d).log"

{
  echo "==== watchdog $(date '+%Y-%m-%d %H:%M:%S %Z') ===="
  TODAY=$(date +%Y-%m-%d)
  HOUR=$(date +%H)

  # Two patrols per trading day. The evening patrol must NOT be satisfied by
  # the morning's journal commit, so it only counts commits after 16:30.
  if [[ ${HOUR} -ge 16 ]]; then
    SINCE="${TODAY} 16:30:00"
    PATROL="close-wrap (16:50 cloud slot)"
  else
    SINCE="${TODAY} 00:00:00"
    PATROL="morning (9:05 cloud slot)"
  fi

  # If we cannot even reach GitHub, the Mac is offline: the cloud has almost
  # certainly delivered (it runs on GitHub's servers), we just can't see it —
  # and a local fallback couldn't email anyway. Stand down instead of
  # journaling an offline scan over stale prices (JP, 2026-08-05).
  if ! git fetch origin main --quiet 2>/dev/null; then
    echo "git fetch failed (offline?) - standing down, no local fallback"
    exit 0
  fi
  if git log origin/main --grep="journal ${TODAY}" --since="${SINCE}" --oneline | grep -q journal; then
    echo "cloud already delivered the ${PATROL} run - nothing to do"
    exit 0
  fi

  echo "NO cloud journal for the ${PATROL} - running local fallback"
  if [[ -f "$CONDA_BASE/bin/activate" ]]; then
    source "$CONDA_BASE/bin/activate"
    conda activate "$CONDA_ENV" 2>/dev/null
  fi
  # cap 500 = the OROS 500, same as the cloud (was a fossil 800 — it wrote
  # 787-name breadth entries that broke uniformity with every other surface)
  PYTHONPATH=. python3 scripts/oros_run.py --mandate auto --cap 500
  echo "watchdog fallback finished: exit $?"
} >> "$LOG" 2>&1
