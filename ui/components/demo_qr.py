"""
demo_qr.py — Show a QR code so audience phones can join the live demo.

When the host runs Streamlit on `0.0.0.0:8501`, anyone on the same WiFi
can reach the app at `http://<mac-lan-ip>:8501`. This component:

  - Detects the Mac's LAN IP (en0 → en1 → fallback)
  - Generates a QR code pointing to that URL
  - Renders a sidebar button that opens a big QR popup

Used in:
  - ui/app.py sidebar       (toggle button in Research Mode)
  - scripts/launch_demo.command (CLI prints terminal QR via show_qr.py)
"""

from __future__ import annotations

import io
import socket
import subprocess
from pathlib import Path
from typing import Optional

import streamlit as st


_PROJECT_ROOT = Path(__file__).parent.parent.parent
_TUNNEL_URL_FILE = _PROJECT_ROOT / "data" / "cache" / "tunnel_url.txt"


def get_public_tunnel_url() -> Optional[str]:
    """Read the Cloudflare tunnel URL persisted by Launch.command.

    The launcher script writes the active tunnel URL to
    ``data/cache/tunnel_url.txt`` when the public tunnel is running,
    and removes the file on shutdown. If the file exists, return its
    content; else return None and the caller falls back to LAN IP.

    Why prefer the tunnel: a tunnel URL works for ANY audience on ANY
    network. The LAN IP only works for people on the same WiFi.
    """
    try:
        if _TUNNEL_URL_FILE.exists():
            url = _TUNNEL_URL_FILE.read_text().strip()
            if url.startswith("https://") and "trycloudflare.com" in url:
                return url
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# LAN IP detection
# ---------------------------------------------------------------------------
def get_lan_ip() -> Optional[str]:
    """Find the Mac's LAN IPv4 address (the one phones on same WiFi can hit).

    Tries in order:
      1. `ipconfig getifaddr en0` (most common WiFi interface on Apple Silicon)
      2. `ipconfig getifaddr en1` (secondary interface)
      3. Connect-and-introspect socket trick (cross-platform fallback)

    Returns:
        IPv4 string like "192.168.1.42", or None if no LAN IP could be found.
    """
    # Try the standard macOS interfaces first
    for iface in ("en0", "en1", "en2"):
        try:
            r = subprocess.run(
                ["ipconfig", "getifaddr", iface],
                capture_output=True,
                text=True,
                timeout=2,
            )
            ip = r.stdout.strip()
            if ip and ip != "127.0.0.1":
                return ip
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    # Cross-platform fallback: ask the OS which IP it would use to reach the internet
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


def get_lan_url(port: int = 8501) -> Optional[str]:
    """Return `http://<lan-ip>:{port}` or None if no LAN IP detected."""
    ip = get_lan_ip()
    return f"http://{ip}:{port}" if ip else None


def get_demo_url(port: int = 8501) -> tuple[Optional[str], str]:
    """Return the best demo URL for an audience to scan, plus its scope.

    Resolution order:
      1. **Cloudflare tunnel URL** (public, anyone on any network) — preferred
      2. **LAN IP URL** (same WiFi only) — fallback when tunnel isn't running
      3. None if neither is available

    Returns
    -------
    (url, scope) where scope is "public", "wifi", or "none". The UI shows
    different instructional copy depending on which scope is available.
    """
    tunnel = get_public_tunnel_url()
    if tunnel:
        return tunnel, "public"
    lan = get_lan_url(port)
    if lan:
        return lan, "wifi"
    return None, "none"


# ---------------------------------------------------------------------------
# QR generation
# ---------------------------------------------------------------------------
def make_qr_png_bytes(url: str, box_size: int = 14, border: int = 2) -> bytes:
    """Generate a QR-code PNG for `url` and return raw bytes (for st.image).

    box_size=14 produces ~500px QR — large enough to scan from across a room
    when projected on a screen.
    """
    import qrcode  # imported lazily so the rest of the app loads if it's missing

    qr = qrcode.QRCode(
        version=None,                                  # auto-size
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Streamlit renderers
# ---------------------------------------------------------------------------
def render_demo_qr_sidebar(port: int = 8501) -> None:
    """Render a 'Show Demo QR' toggle in the Streamlit sidebar.

    Auto-enables the QR display on first load if a Cloudflare tunnel URL
    exists — when the host runs `Launch.command` (which starts the tunnel),
    they're clearly running a demo and want the QR visible immediately.

    The button still toggles, so the host can hide the QR mid-presentation
    once everyone has scanned.
    """
    tunnel_url = get_public_tunnel_url()

    # First-load auto-enable: if a tunnel exists, default to "show QR".
    # Subsequent toggles by the user are respected via the session state.
    if "show_demo_qr" not in st.session_state:
        st.session_state.show_demo_qr = bool(tunnel_url)

    with st.sidebar:
        # Bright banner — impossible to miss when running a live demo
        if tunnel_url:
            st.markdown(
                "<div style='background:#0d2818;border:1px solid #3fb950;"
                "border-radius:8px;padding:10px 12px;margin-bottom:6px'>"
                "<div style='font-size:11px;color:#3fb950;letter-spacing:1.5px;"
                "font-weight:700'>🌍 LIVE DEMO · PUBLIC TUNNEL ACTIVE</div>"
                "<div style='font-size:12px;color:#c9d1d9;margin-top:4px'>"
                "Audience can scan the QR from anywhere — no WiFi required.</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown("### 📱 Live Demo")
            st.caption(
                "Run `Launch.command` to start the public Cloudflare tunnel "
                "(then refresh this page). Without the tunnel, the QR will "
                "only work for phones on the same WiFi as this Mac."
            )

        label = "🛑 Hide QR code" if st.session_state.show_demo_qr else "📲 Show Demo QR"
        btn_type = "primary" if not st.session_state.show_demo_qr else "secondary"
        if st.button(label, use_container_width=True, key="demo_qr_toggle",
                     type=btn_type):
            st.session_state.show_demo_qr = not st.session_state.show_demo_qr
            st.rerun()


def render_demo_qr_panel(port: int = 8501) -> None:
    """If the sidebar toggle is on, render a big QR code at top of main area.

    Prefers the Cloudflare tunnel URL (works for any phone on any network)
    and falls back to LAN IP (same WiFi only) when no tunnel is detected.
    The instructional copy adapts to scope so the host can tell the
    audience exactly what they need to do.
    """
    if not st.session_state.get("show_demo_qr"):
        return

    url, scope = get_demo_url(port)
    if url is None:
        st.error(
            "📡 Could not detect a public URL or LAN IP. Run `Launch.command` "
            "(which starts the Cloudflare tunnel), or make sure Streamlit is "
            "bound to `--server.address 0.0.0.0` if you only want LAN access."
        )
        return

    # Scope-specific instructions
    if scope == "public":
        scope_label = "PUBLIC — works for ANY phone, ANY network"
        scope_color = "#3fb950"
        instructions = (
            "1. Open your phone's <b>camera app</b>.<br>"
            "2. Point it at the QR code.<br>"
            "3. Tap the link that appears — it opens this app on your phone."
        )
    else:  # "wifi"
        scope_label = "WIFI ONLY — audience must be on the same WiFi as my Mac"
        scope_color = "#d29922"
        instructions = (
            "1. Connect your phone to <b>the same WiFi as my Mac</b>.<br>"
            "2. Open your camera app and point it at the QR code.<br>"
            "3. Tap the link that appears."
        )

    with st.container():
        st.markdown(
            """
            <div style="
                padding: 1.5rem 2rem;
                margin-bottom: 1.5rem;
                background: linear-gradient(135deg, #0a1628 0%, #1e3a5f 100%);
                border-radius: 12px;
                border: 1px solid rgba(255,255,255,0.1);
            ">
            """,
            unsafe_allow_html=True,
        )
        col_qr, col_info = st.columns([1, 2])
        with col_qr:
            png = make_qr_png_bytes(url, box_size=12)
            st.image(png, caption=None, width=320)
        with col_info:
            st.markdown(
                f"""
                <div style="color: white; padding: 1rem 0;">
                  <div style="font-size: 0.78rem; opacity: 0.85; letter-spacing: 2px;
                              color: {scope_color}; font-weight: 600;">
                    {scope_label}
                  </div>
                  <div style="font-size: 2.2rem; font-weight: 700; margin: 0.5rem 0;">
                    Ai-Agent Stock Analyzer
                  </div>
                  <div style="font-size: 1.05rem; opacity: 0.85; line-height: 1.5;">
                    {instructions}
                  </div>
                  <div style="
                    margin-top: 1rem;
                    padding: 0.6rem 0.9rem;
                    background: rgba(0,0,0,0.35);
                    border-radius: 6px;
                    font-family: ui-monospace, 'SF Mono', Menlo, monospace;
                    font-size: 0.95rem;
                    color: #7dd3fc;
                    word-break: break-all;
                  ">
                    {url}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)
