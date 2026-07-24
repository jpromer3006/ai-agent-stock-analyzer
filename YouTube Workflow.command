#!/bin/zsh
# YouTube Workflow.command — manually trigger the OROS daily run on GitHub
# Actions (workflow_dispatch), which builds the Market Desk video and
# publishes it to YouTube. Double-click to run.
#
# Trigger order:
#   1. gh CLI if installed
#   2. GitHub REST API using the same keychain credential git push uses
#   3. Fallback: open the Actions page so you can click "Run workflow"

REPO="jpromer3006/ai-agent-stock-analyzer"
WORKFLOW="oros_daily.yml"
PAGE_URL="https://github.com/$REPO/actions/workflows/$WORKFLOW"

echo "=== OROS: manual YouTube workflow trigger ==="

# --- 1) gh CLI ---------------------------------------------------------------
if command -v gh >/dev/null 2>&1; then
  if gh workflow run "$WORKFLOW" --repo "$REPO" --ref main 2>/dev/null; then
    echo "Triggered via gh CLI. Opening the runs page..."
    open "$PAGE_URL"
    read -k 1 "?Press any key to close..."
    exit 0
  fi
fi

# --- 2) REST API with the keychain credential --------------------------------
TOKEN=$(printf 'protocol=https\nhost=github.com\n\n' \
        | git credential fill 2>/dev/null \
        | awk -F= '/^password=/{print $2}')
if [ -n "$TOKEN" ]; then
  CODE=$(curl -s -o /tmp/oros_dispatch_resp.json -w '%{http_code}' -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/actions/workflows/$WORKFLOW/dispatches" \
    -d '{"ref":"main"}')
  if [ "$CODE" = "204" ]; then
    echo "Workflow triggered (HTTP 204)."
    echo "Watch it run — the report email arrives when it finishes."
    open "$PAGE_URL"
    read -k 1 "?Press any key to close..."
    exit 0
  else
    echo "API dispatch failed (HTTP $CODE):"
    cat /tmp/oros_dispatch_resp.json 2>/dev/null
  fi
fi

# --- 3) Browser fallback ------------------------------------------------------
echo "Opening the Actions page — click 'Run workflow' > 'Run workflow' there."
open "$PAGE_URL"
read -k 1 "?Press any key to close..."
