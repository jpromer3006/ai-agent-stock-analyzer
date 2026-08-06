"""
cron_control.py - Morning run automation controls (Portfolio tab + launch prompt).

Gives the user three things, all Mac-side (Streamlit runs on the Mac under the
conda env, so subprocess + launchctl act on the real machine):

    1. A manual "Run morning run now" button (Portfolio tab) that kicks
       scripts/oros_run.py in the background with the SAME interpreter that
       runs the app, logging to data/journal/manual_run_*.log.
    2. An enable/disable control for the launchd schedule
       (com.oros.morningrun, weekdays 9:35 AM local) that bootstraps the
       plist into ~/Library/LaunchAgents.
    3. A one-time launch prompt asking whether to enable the schedule, shown
       only when the job is not already loaded.

Propose-and-journal-only: the underlying run never trades. Additive module:
does not touch Research, Trader, or the portfolio engine.
"""

from __future__ import annotations

import os
import sys
import glob
import subprocess
from datetime import datetime
from pathlib import Path

import streamlit as st

# ── Paths (project root is two levels up: ui/components/ -> project) ─────────
_ROOT = Path(__file__).resolve().parents[2]
_LABEL = "com.oros.morningrun"
_PLIST_SRC = _ROOT / "scripts" / "com.oros.morningrun.plist"
_PLIST_DST = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
_JOURNAL = _ROOT / "data" / "journal"
_RUN_SCRIPT = _ROOT / "scripts" / "oros_run.py"


# ═══════════════════════════════════════════════════════════════════════════
# launchd / subprocess helpers (macOS)
# ═══════════════════════════════════════════════════════════════════════════
def _uid() -> int:
    return os.getuid()


def _have_launchctl() -> bool:
    return subprocess.run(
        ["which", "launchctl"], capture_output=True, text=True
    ).returncode == 0


def cron_is_loaded() -> bool:
    """True when launchd has the job bootstrapped (it will fire on schedule)."""
    try:
        out = subprocess.run(
            ["launchctl", "list"], capture_output=True, text=True, timeout=10
        )
        return _LABEL in out.stdout
    except Exception:
        return False


def cron_installed() -> bool:
    """True when the plist has been copied into ~/Library/LaunchAgents."""
    return _PLIST_DST.exists()


def enable_cron() -> tuple[bool, str]:
    """Install the plist and bootstrap it into launchd (weekdays 9:35 AM)."""
    if not _have_launchctl():
        return False, "launchctl not found (the schedule needs macOS)."
    if not _PLIST_SRC.exists():
        return False, f"plist source missing at {_PLIST_SRC}"
    try:
        _PLIST_DST.parent.mkdir(parents=True, exist_ok=True)
        # Keep the installed copy in sync with the repo source on every enable.
        _PLIST_DST.write_bytes(_PLIST_SRC.read_bytes())
        uid = _uid()
        # bootout any stale instance first so bootstrap can't fail "already loaded".
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", str(_PLIST_DST)],
            capture_output=True, text=True, timeout=10,
        )
        r = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{uid}", str(_PLIST_DST)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            # Legacy fallback for older macOS.
            r2 = subprocess.run(
                ["launchctl", "load", "-w", str(_PLIST_DST)],
                capture_output=True, text=True, timeout=15,
            )
            if r2.returncode != 0 and not cron_is_loaded():
                return False, (r.stderr or r2.stderr or "launchctl failed").strip()
        return True, "Scheduled: weekdays at 9:35 AM (Mac local time)."
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def disable_cron() -> tuple[bool, str]:
    """Remove the job from launchd so it stops firing."""
    if not _have_launchctl():
        return False, "launchctl not found."
    try:
        uid = _uid()
        r = subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", str(_PLIST_DST)],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            subprocess.run(
                ["launchctl", "unload", "-w", str(_PLIST_DST)],
                capture_output=True, text=True, timeout=15,
            )
        return True, "Schedule disabled."
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def run_now(cap: int | None = None, positions: int = 12) -> tuple[bool, str]:
    """Kick the morning run in the background using the app's own interpreter.

    Returns (ok, log_filename). Token-free quant engine; never trades.
    cap=None means the oros_config default (the OROS 500) — callers only
    pass a number to deliberately override it.
    """
    try:
        _JOURNAL.mkdir(parents=True, exist_ok=True)
        log = _JOURNAL / f"manual_run_{datetime.now():%Y%m%d_%H%M%S}.log"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_ROOT)
        env["OROS_RUN_SOURCE"] = "app-button"     # journal provenance
        fh = open(log, "w")
        args = [sys.executable, str(_RUN_SCRIPT),
                "--positions", str(positions),
                # Explicit edition (JP, 2026-07-28): the button says MORNING,
                # so it always runs the morning edition — a 3 PM click must
                # never fire a surprise Closing Bell (auto flips after 14:00).
                "--edition", "morning"]
        if cap is not None:
            args += ["--cap", str(cap)]
        subprocess.Popen(
            args,
            cwd=str(_ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return True, log.name
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# ═══════════════════════════════════════════════════════════════════════════
# Status helpers
# ═══════════════════════════════════════════════════════════════════════════
def _latest(pattern: str) -> Path | None:
    files = glob.glob(str(_JOURNAL / pattern))
    return Path(max(files, key=os.path.getmtime)) if files else None


def _last_proposal() -> str:
    p = _latest("portfolio_*.json")
    if not p:
        return "none yet"
    ts = datetime.fromtimestamp(p.stat().st_mtime)
    return f"{p.name} · {ts:%b %d %H:%M}"


def _tail(path: Path, n: int = 12) -> str:
    try:
        return "".join(path.read_text(errors="replace").splitlines(keepends=True)[-n:])
    except Exception:
        return ""


def _run_finished(text: str) -> bool:
    return ("morning run end" in text.lower()) or ("OROS BRIEF" in text) \
        or ("Traceback" in text) or ("aborting" in text.lower())


# ═══════════════════════════════════════════════════════════════════════════
# Portfolio-tab panel
# ═══════════════════════════════════════════════════════════════════════════
def render_cron_panel() -> None:
    """Automation controls, rendered inside the Portfolio tab."""
    loaded = cron_is_loaded()
    badge = ("🟢 Scheduled" if loaded
             else "🟠 Installed, not scheduled" if cron_installed()
             else "⚪ Not scheduled")

    with st.expander(f"🌅 Morning run & automation — {badge}", expanded=False):
        st.caption(
            "Runs the regime-aware portfolio build, journals a proposal, and "
            "records a training snapshot. Propose-and-journal-only: it never "
            "trades. The schedule uses macOS launchd (weekdays 9:35 AM local)."
        )

        c1, c2 = st.columns([1, 1])

        # ── Manual run ──────────────────────────────────────────────────────
        with c1:
            st.markdown("**Run now**")
            if st.button("▶️ Run morning run now", type="primary",
                         use_container_width=True, key="cron_run_now"):
                ok, info = run_now()
                if ok:
                    st.session_state["cron_run_log"] = info
                    st.session_state["cron_run_started"] = datetime.now().strftime("%H:%M:%S")
                    st.toast("Morning run started in the background.")
                    st.rerun()
                else:
                    st.error(f"Could not start: {info}")
            # Demo button (JP, 2026-07-30): generate and EMAIL today's report
            # on demand — the exact same pipeline as the automated morning
            # email, built from today's journals. No rescan, no video render,
            # so it completes in under a minute, live on stage.
            if st.button("📧 Email me today's report",
                         use_container_width=True, key="cron_email_report"):
                with st.spinner("Building today's report from the journals "
                                "and emailing it to you (~30s)..."):
                    _prev = os.environ.get("OROS_VIDEO")
                    os.environ["OROS_VIDEO"] = "0"      # report only
                    try:
                        from ml.daily_report import send_daily_report
                        pdf = send_daily_report(force=True, research=False)
                        st.success(f"Sent — check your inbox ({pdf.name}). "
                                   "Same pipeline, same journals as the "
                                   "automated morning email.")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Could not send: {type(e).__name__}: {e}")
                    finally:
                        if _prev is None:
                            os.environ.pop("OROS_VIDEO", None)
                        else:
                            os.environ["OROS_VIDEO"] = _prev

        # ── Schedule toggle ─────────────────────────────────────────────────
        with c2:
            st.markdown("**Daily schedule**")
            if loaded:
                if st.button("⏹ Disable 9:35 AM schedule",
                             use_container_width=True, key="cron_disable"):
                    ok, msg = disable_cron()
                    (st.success if ok else st.error)(msg)
                    st.rerun()
            else:
                if st.button("✅ Enable 9:35 AM schedule", type="primary",
                             use_container_width=True, key="cron_enable"):
                    ok, msg = enable_cron()
                    (st.success if ok else st.error)(msg)
                    st.rerun()

        # ── Live status of the most recent manual run ───────────────────────
        log_name = st.session_state.get("cron_run_log")
        if log_name:
            log_path = _JOURNAL / log_name
            text = _tail(log_path, 14)
            done = _run_finished(text)
            started = st.session_state.get("cron_run_started", "")
            head = (f"✅ Finished (started {started})" if done
                    else f"⏳ Running… (started {started})")
            st.markdown(f"**Last manual run:** {head}")
            if not done and st.button("🔄 Refresh status", key="cron_refresh"):
                st.rerun()
            if text:
                st.code(text or "(no output yet)", language="text")

        st.caption(
            f"Last journaled proposal: {_last_proposal()}  ·  "
            f"plist: {'installed' if cron_installed() else 'not installed'}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# One-time launch prompt (call from app.py at startup)
# ═══════════════════════════════════════════════════════════════════════════
def maybe_prompt_cron_on_launch() -> None:
    """On app launch, if the schedule is not loaded, ask once whether to
    enable it. Dismissed for the rest of the session either way."""
    if st.session_state.get("cron_prompt_done"):
        return
    # Permanent opt-out (JP, 2026-07-27): the GitHub Actions cloud run is
    # the single source of truth; once dismissed for good, never nag again.
    from pathlib import Path as _P
    _optout = _P(__file__).resolve().parent.parent.parent / "data" / "journal" / ".cron_prompt_optout"
    if _optout.exists():
        st.session_state["cron_prompt_done"] = True
        return
    # Resolve loaded-state once per session to avoid repeated launchctl calls.
    if "cron_loaded_at_launch" not in st.session_state:
        st.session_state["cron_loaded_at_launch"] = cron_is_loaded()
    if st.session_state["cron_loaded_at_launch"]:
        st.session_state["cron_prompt_done"] = True
        return

    with st.container(border=True):
        st.markdown(
            "🌅 **Enable the OROS morning run?**  The automated 9:35 AM "
            "weekday build is **not scheduled**. Enable it now, or manage it "
            "anytime under **Portfolio → Morning run & automation**."
        )
        a, b, _ = st.columns([1, 1, 3])
        with a:
            if st.button("✅ Enable daily run", type="primary", key="cron_launch_enable"):
                ok, msg = enable_cron()
                st.session_state["cron_prompt_done"] = True
                (st.success if ok else st.error)(msg)
                st.rerun()
        with b:
            if st.button("Not now", key="cron_launch_skip"):
                st.session_state["cron_prompt_done"] = True
                st.rerun()
        if st.button("☁️ The cloud runs it — don't ask again",
                     key="cron_launch_optout"):
            try:
                _optout.parent.mkdir(parents=True, exist_ok=True)
                _optout.write_text("dismissed — GitHub Actions is the "
                                   "scheduler of record\n")
            except Exception:  # noqa: BLE001
                pass
            st.session_state["cron_prompt_done"] = True
            st.rerun()
