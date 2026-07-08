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

  git fetch origin main --quiet 2>/dev/null
  if git log origin/main --grep="journal ${TODAY}" --oneline | grep -q journal; then
    echo "cloud already delivered today's run - nothing to do"
    exit 0
  fi

  echo "NO cloud journal for ${TODAY} by 10:20 ET - running local fallback"
  if [[ -f "$CONDA_BASE/bin/activate" ]]; then
    source "$CONDA_BASE/bin/activate"
    conda activate "$CONDA_ENV" 2>/dev/null
  fi
  PYTHONPATH=. python3 scripts/oros_run.py --mandate auto --cap 800
  echo "watchdog fallback finished: exit $?"
} >> "$LOG" 2>&1
