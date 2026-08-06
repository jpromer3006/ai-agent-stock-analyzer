#!/bin/bash
#
# oros_cron.sh — launchd/cron wrapper for the unattended OROS morning run.
#
# Activates the conda env, runs scripts/oros_run.py (propose-and-journal-only;
# it NEVER trades), and appends all output to a dated log in data/journal/.
# Invoked by ~/Library/LaunchAgents/com.oros.morningrun.plist.

set -uo pipefail

PROJECT_DIR="/Users/jpromero/Ai-Agent Oros"
CONDA_BASE="/Users/jpromero/anaconda3"
CONDA_ENV="dev2"

cd "$PROJECT_DIR" || { echo "ERROR: cannot cd to $PROJECT_DIR"; exit 1; }

LOG_DIR="$PROJECT_DIR/data/journal"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/cron_$(date +%Y-%m-%d).log"

{
  echo "===================================================================="
  echo "OROS morning run start: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "===================================================================="

  # Activate conda env (mirrors Launch.command)
  if [[ -f "$CONDA_BASE/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$CONDA_BASE/bin/activate"
    conda activate "$CONDA_ENV" 2>/dev/null
  fi

  if [[ "${CONDA_DEFAULT_ENV:-}" != "$CONDA_ENV" ]]; then
    echo "WARN: conda env '$CONDA_ENV' not active (got '${CONDA_DEFAULT_ENV:-none}'); using python3 on PATH."
  fi

  # scan cap comes from ml/oros_config.py — never restated in runner scripts
  OROS_RUN_SOURCE=cron PYTHONPATH=. python3 scripts/oros_run.py --positions 12
  rc=$?

  echo "OROS morning run end: $(date '+%Y-%m-%d %H:%M:%S %Z') | exit=$rc"
  exit $rc
} >> "$LOG" 2>&1
