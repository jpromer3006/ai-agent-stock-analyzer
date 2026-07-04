#!/usr/bin/env bash
#
# launch_demo.command — Double-click this file to start the live demo.
#
# What it does:
#   1. cd into the project
#   2. Activate the dev2 conda env
#   3. Detect the Mac's LAN IP
#   4. Print a big banner + ASCII QR code so audience phones can join
#   5. Launch Streamlit bound to 0.0.0.0 so phones on the same WiFi can connect
#
# To enable double-click:
#   chmod +x scripts/launch_demo.command
# (Done once; the file stays executable.)
#
# Tip: drag this .command file to your Dock or Desktop for one-click demos.

set -e

# --- Resolve project root (parent of scripts/) ------------------------------
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." &> /dev/null && pwd )"
cd "$PROJECT_ROOT"

# --- Pretty colors ----------------------------------------------------------
BOLD=$'\033[1m'
CYAN=$'\033[36m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
RESET=$'\033[0m'

clear
cat <<EOF
${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════════╗
║  Ai-Agent Stock Analyzer — Live Demo Launcher                    ║
║  Fordham Applied Finance Project                                 ║
╚══════════════════════════════════════════════════════════════════╝${RESET}

EOF

# --- Activate conda env -----------------------------------------------------
echo "${YELLOW}▸ Activating conda env 'dev2'...${RESET}"
# shellcheck disable=SC1091
source /Users/jpromero/anaconda3/bin/activate
conda activate dev2

PYBIN="$(which python3)"
echo "  Using: $PYBIN"
echo

# --- Detect LAN IP ----------------------------------------------------------
LAN_IP=""
for IFACE in en0 en1 en2; do
    IP=$(ipconfig getifaddr "$IFACE" 2>/dev/null || true)
    if [ -n "$IP" ] && [ "$IP" != "127.0.0.1" ]; then
        LAN_IP="$IP"
        ACTIVE_IFACE="$IFACE"
        break
    fi
done

if [ -z "$LAN_IP" ]; then
    echo "${YELLOW}⚠ Could not detect a LAN IP. Make sure WiFi is connected.${RESET}"
    echo "  Falling back to localhost only."
    DEMO_URL="http://localhost:8501"
else
    DEMO_URL="http://${LAN_IP}:8501"
    echo "${GREEN}▸ Detected LAN: ${LAN_IP} (interface: ${ACTIVE_IFACE})${RESET}"
fi
echo

# --- Print ASCII QR + banner ------------------------------------------------
cat <<EOF
${BOLD}${GREEN}┌─────────────────────────────────────────────────────────────────┐
│  📱  AUDIENCE: Connect to the SAME WiFi as this Mac, then scan: │
│  🔗  ${DEMO_URL}                                          │
└─────────────────────────────────────────────────────────────────┘${RESET}

EOF

# Print ASCII QR code in the terminal (host can show the terminal window too)
"$PYBIN" - <<PYQR
try:
    import qrcode
    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data("${DEMO_URL}")
    qr.make(fit=True)
    qr.print_ascii(invert=True)
except Exception as e:
    print(f"(QR generation skipped: {e})")
PYQR

cat <<EOF

${BOLD}${CYAN}▸ Inside the app, click "📲 Show Demo QR" in the sidebar to project
  a large scannable QR code on your screen.${RESET}

${YELLOW}▸ Starting Streamlit on 0.0.0.0:8501 ...${RESET}
${YELLOW}  (macOS may prompt to allow incoming network connections — click "Allow")${RESET}
${YELLOW}  (Press Ctrl+C in this window to stop the demo)${RESET}

EOF

# --- Launch Streamlit -------------------------------------------------------
exec "$PYBIN" -m streamlit run ui/app.py \
    --server.address=0.0.0.0 \
    --server.port=8501 \
    --browser.gatherUsageStats=false
