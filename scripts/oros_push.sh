#!/bin/zsh
#
# oros_push.sh — the sync that never trips on report PDFs (JP, 2026-08-05).
#
# The cloud runner commits data/journal/reports/*.pdf; the local app writes
# the SAME files untracked. Every pull then aborts with "untracked working
# tree files would be overwritten by checkout." Those PDFs are regenerable
# (and the cloud's tracked copy arrives with the pull), so we delete the
# untracked ones first, then rebase with autostash, then push.
#
# Point the alias here once:  alias oros-push='zsh "$HOME/Ai-Agent Oros/scripts/oros_push.sh"'

set -uo pipefail
cd "/Users/jpromero/Ai-Agent Oros" || exit 1

# 1. Clear untracked regenerable PDFs that would block checkout
git status --porcelain -- data/journal/reports 2>/dev/null \
  | awk '/^\?\? / {sub(/^\?\? /,""); print}' \
  | while IFS= read -r f; do
      echo "removing untracked regenerable: $f"
      rm -f -- "$f"
    done

# 2. Rebase on the cloud (cloud wins on journal data if it conflicts)
if ! git pull --rebase --autostash origin main; then
  echo "rebase hit conflicts — taking the cloud's journal/watchlists"
  git checkout --ours -- data/journal data/watchlists 2>/dev/null
  git add data/journal data/watchlists 2>/dev/null
  GIT_EDITOR=true git rebase --continue || { echo "manual fix needed"; exit 1; }
fi

# 3. Push
git push origin main
