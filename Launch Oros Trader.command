#!/bin/bash
# Double click this file to launch the Oros Trader Streamlit UI.
# macOS treats .command files as scripts you can run from Finder.
#
# IMPORTANT: this launcher delegates to ./Launch.command so there is a
# SINGLE source of truth for startup. Launch.command starts Streamlit AND
# the public Cloudflare tunnel, then writes the tunnel URL to
# data/cache/tunnel_url.txt. The in-app "Show Demo QR" component reads that
# file and generates a PUBLIC QR (scannable from any network). A previous
# version of this file ran Streamlit directly and skipped the tunnel, which
# is why the QR fell back to "same WiFi only" — delegating fixes that.

# Always run from the directory of this script, no matter where the
# user double clicked it from.
cd "$(dirname "$0")"

echo ""
echo "============================================================"
echo "  Oros Agentic Finance System  -  Trader Mode"
echo "  Working directory: $(pwd)"
echo "============================================================"
echo ""

# Make sure the sentiment dependency is present without prompting.
# Quiet output, no error if already installed.
python3 -m pip install --quiet --disable-pip-version-check \
    vaderSentiment 2>/dev/null

# Hand off to the main launcher (Streamlit + public Cloudflare tunnel + QR).
if [[ -x "./Launch.command" ]]; then
    exec "./Launch.command"
else
    echo "Launch.command not found or not executable — starting Streamlit only."
    echo "NOTE: without Launch.command the Demo QR will be WiFi-only (no public tunnel)."
    exec python3 -m streamlit run ui/app.py
fi
