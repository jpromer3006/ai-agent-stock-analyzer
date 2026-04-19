#!/bin/bash
#
# Launch.command — Double-click launcher for Ai-Agent Stock Analyzer
#
# On macOS, double-clicking this file opens Terminal, activates the conda env,
# starts the Streamlit UI, starts a Cloudflare tunnel, and prints the team-
# shareable URL. Press Ctrl+C (or close the window) to shut everything down.
#

# ─── Colors ────────────────────────────────────────────────────────────
BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
RESET='\033[0m'

# ─── Go to project directory (works from anywhere) ─────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# ─── Header ────────────────────────────────────────────────────────────
clear
echo -e "${BOLD}${BLUE}╔═══════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${BLUE}║${RESET}   ${BOLD}🤖 Ai-Agent Stock Analyzer — Launcher${RESET}                        ${BOLD}${BLUE}║${RESET}"
echo -e "${BOLD}${BLUE}║${RESET}   Fordham Applied Finance Project · Lecture 9                    ${BOLD}${BLUE}║${RESET}"
echo -e "${BOLD}${BLUE}╚═══════════════════════════════════════════════════════════════════╝${RESET}"
echo
echo -e "Project: ${CYAN}$SCRIPT_DIR${RESET}"
echo

# ─── Cleanup trap ──────────────────────────────────────────────────────
cleanup() {
    echo
    echo -e "${YELLOW}🛑 Shutting down...${RESET}"
    [[ -n "$STREAMLIT_PID" ]] && kill "$STREAMLIT_PID" 2>/dev/null
    [[ -n "$TUNNEL_PID" ]] && kill "$TUNNEL_PID" 2>/dev/null
    # Kill any orphaned children
    pkill -f "streamlit run ui/app.py" 2>/dev/null
    pkill -f "cloudflared tunnel --url" 2>/dev/null
    echo -e "${GREEN}✓ Clean exit.${RESET}"
    echo
    echo "Press any key to close this window..."
    read -n 1 -s
    exit 0
}
trap cleanup INT TERM EXIT

# ─── Step 1: Kill any previous instances ───────────────────────────────
echo -e "${BOLD}[1/5]${RESET} Cleaning up any previous sessions..."
pkill -f "streamlit run ui/app.py" 2>/dev/null
pkill -f "cloudflared tunnel --url" 2>/dev/null
sleep 1
echo -e "      ${GREEN}✓ Ready${RESET}"

# ─── Step 2: Activate conda env ────────────────────────────────────────
echo -e "${BOLD}[2/5]${RESET} Activating conda env 'dev2'..."
if [[ ! -f "/Users/jpromero/anaconda3/bin/activate" ]]; then
    echo -e "      ${RED}✗ Anaconda not found at /Users/jpromero/anaconda3/${RESET}"
    echo -e "      Update the path in Launch.command if your Anaconda lives elsewhere."
    exit 1
fi
# shellcheck disable=SC1091
source /Users/jpromero/anaconda3/bin/activate
conda activate dev2 2>/dev/null
if [[ "$CONDA_DEFAULT_ENV" != "dev2" ]]; then
    echo -e "      ${RED}✗ Failed to activate conda env 'dev2'${RESET}"
    echo -e "      Run: ${CYAN}conda env list${RESET} to see available envs."
    exit 1
fi
echo -e "      ${GREEN}✓ Active: $CONDA_DEFAULT_ENV${RESET}"

# ─── Step 3: Ensure cloudflared is available ───────────────────────────
echo -e "${BOLD}[3/5]${RESET} Checking Cloudflare tunnel binary..."
CLOUDFLARED="/tmp/cloudflared"
if [[ ! -x "$CLOUDFLARED" ]]; then
    echo -e "      ${YELLOW}⚠ Not found at /tmp/cloudflared — downloading...${RESET}"
    curl -sL \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz \
        -o /tmp/cloudflared.tgz
    tar -xzf /tmp/cloudflared.tgz -C /tmp/ 2>/dev/null
    chmod +x "$CLOUDFLARED"
    if [[ ! -x "$CLOUDFLARED" ]]; then
        echo -e "      ${RED}✗ Download failed${RESET}"
        exit 1
    fi
fi
echo -e "      ${GREEN}✓ $($CLOUDFLARED --version 2>&1 | head -1)${RESET}"

# ─── Step 4: Start Streamlit ───────────────────────────────────────────
echo -e "${BOLD}[4/5]${RESET} Starting Streamlit UI on port 8501..."
STREAMLIT_LOG="/tmp/adminai_streamlit.log"
python3 -m streamlit run ui/app.py \
    --server.port 8501 \
    --server.headless true \
    --browser.gatherUsageStats false \
    > "$STREAMLIT_LOG" 2>&1 &
STREAMLIT_PID=$!

# Wait up to 20 seconds for Streamlit to be ready
for i in {1..20}; do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:8501 | grep -q "200\|303"; then
        break
    fi
    sleep 1
done
if ! curl -s -o /dev/null -w "%{http_code}" http://localhost:8501 | grep -q "200\|303"; then
    echo -e "      ${RED}✗ Streamlit didn't start within 20s. Log:${RESET}"
    tail -20 "$STREAMLIT_LOG"
    exit 1
fi
echo -e "      ${GREEN}✓ Running at http://localhost:8501 (PID $STREAMLIT_PID)${RESET}"

# ─── Step 5: Start Cloudflare tunnel ───────────────────────────────────
echo -e "${BOLD}[5/5]${RESET} Starting Cloudflare tunnel (this gives your team a shareable URL)..."
TUNNEL_LOG="/tmp/adminai_tunnel.log"
"$CLOUDFLARED" tunnel --url http://localhost:8501 > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

# Wait up to 20 seconds for tunnel URL
TUNNEL_URL=""
for i in {1..20}; do
    if grep -q "trycloudflare.com" "$TUNNEL_LOG" 2>/dev/null; then
        TUNNEL_URL=$(grep -o "https://[a-zA-Z0-9-]*\.trycloudflare\.com" "$TUNNEL_LOG" | head -1)
        if [[ -n "$TUNNEL_URL" ]]; then
            break
        fi
    fi
    sleep 1
done

if [[ -z "$TUNNEL_URL" ]]; then
    echo -e "      ${YELLOW}⚠ Tunnel URL not detected yet. Local URL still works.${RESET}"
fi

# ─── Success banner ────────────────────────────────────────────────────
echo
echo -e "${BOLD}${GREEN}╔═══════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║${RESET}                  ${BOLD}✅  ALL SYSTEMS RUNNING${RESET}                         ${BOLD}${GREEN}║${RESET}"
echo -e "${BOLD}${GREEN}╚═══════════════════════════════════════════════════════════════════╝${RESET}"
echo
echo -e "  ${BOLD}Local URL:${RESET}     ${CYAN}http://localhost:8501${RESET}"
if [[ -n "$TUNNEL_URL" ]]; then
    echo -e "  ${BOLD}Team URL:${RESET}      ${CYAN}${TUNNEL_URL}${RESET}"
    echo
    echo -e "  ${YELLOW}📋 Copy the Team URL above and share it with your teammates.${RESET}"
else
    echo -e "  ${BOLD}Team URL:${RESET}      (not yet ready — check ${CYAN}$TUNNEL_LOG${RESET})"
fi
echo
echo -e "${BOLD}Controls:${RESET}"
echo -e "  • ${BOLD}Ctrl+C${RESET} (or close this window) to shut everything down"
echo -e "  • Streamlit logs: ${CYAN}$STREAMLIT_LOG${RESET}"
echo -e "  • Tunnel logs:    ${CYAN}$TUNNEL_LOG${RESET}"
echo
echo -e "${BOLD}${BLUE}─────────────────────────────────────────────────────────────────────${RESET}"
echo -e "Watching logs... (Streamlit output below)"
echo -e "${BOLD}${BLUE}─────────────────────────────────────────────────────────────────────${RESET}"
echo

# ─── Tail Streamlit log so the window stays alive and useful ───────────
tail -f "$STREAMLIT_LOG"
