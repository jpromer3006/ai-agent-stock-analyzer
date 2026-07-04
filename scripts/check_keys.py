#!/usr/bin/env python3
"""
check_keys.py - OROS API key health check.

Run this AFTER rotating every key. It verifies each credential against the
live provider and prints a clean status report. It never prints a secret:
only presence, length, and provider response are shown.

Resolution order matches the app:
  ANTHROPIC_API_KEY -> Mac Keychain first, then .env
  everything else   -> .env

Usage:
    PYTHONPATH=. python3 scripts/check_keys.py

Exit code is 0 only if every REQUIRED key passes. Optional keys never fail
the run; they are reported for information.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

# Required keys gate the exit code. Optional keys are informational only.
REQUIRED = {"ANTHROPIC_API_KEY", "FRED_API_KEY", "ALPHA_VANTAGE_API_KEY"}
OPTIONAL = {"FMP_API_KEY", "ELEVENLABS_API_KEY"}


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------
def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def keychain_lookup(service: str) -> str | None:
    """Read a generic password from the macOS Keychain, or None."""
    if platform.system() != "Darwin":
        return None
    try:
        res = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-w"],
            capture_output=True, text=True, timeout=10,
        )
        if res.returncode == 0:
            return res.stdout.strip() or None
    except Exception:
        return None
    return None


def fingerprint(value: str) -> str:
    """Non-reversible hint so you can tell two keys apart without exposing them."""
    if not value:
        return "<empty>"
    return f"len={len(value)} ...{value[-2:]}"


# ---------------------------------------------------------------------------
# Provider probes. Each returns (ok: bool, detail: str).
# ---------------------------------------------------------------------------
def _get(url: str, headers: dict[str, str] | None = None, timeout: int = 20):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return None, f"network error: {e}"


def probe_anthropic(key: str):
    status, body = _get(
        "https://api.anthropic.com/v1/models",
        {"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    if status == 200:
        return True, "valid"
    if status == 401:
        return False, "401 unauthorized (rotated/invalid)"
    return False, f"http {status}"


def probe_fred(key: str):
    status, body = _get(
        f"https://api.stlouisfed.org/fred/series?series_id=GDP&file_type=json&api_key={key}"
    )
    if status == 200:
        return True, "valid"
    return False, f"http {status} (bad key returns 400)"


def probe_alpha_vantage(key: str):
    status, body = _get(
        f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey={key}"
    )
    if status != 200:
        return False, f"http {status}"
    low = body.lower()
    if "global quote" in low and '"05. price"' in low:
        return True, "valid (free tier: 25 req/day, 5/min)"
    if "rate limit" in low or "premium" in low or "thank you for using" in low:
        return False, "valid key but rate limited / premium endpoint"
    if "invalid api call" in low or "apikey" in low:
        return False, "rejected (invalid key)"
    return False, f"unexpected body: {body[:80]}"


def probe_fmp(key: str):
    status, body = _get(f"https://financialmodelingprep.com/api/v3/quote/AAPL?apikey={key}")
    if status == 200 and body.strip().startswith("["):
        return True, "valid"
    if status == 403:
        return False, "403 (expired / plan does not allow this endpoint)"
    if status == 401:
        return False, "401 unauthorized"
    return False, f"http {status}"


def probe_elevenlabs(key: str):
    status, body = _get("https://api.elevenlabs.io/v1/user", {"xi-api-key": key})
    if status == 200:
        return True, "valid"
    if status == 401:
        return False, "401 unauthorized"
    return False, f"http {status}"


PROBES = {
    "ANTHROPIC_API_KEY": probe_anthropic,
    "FRED_API_KEY": probe_fred,
    "ALPHA_VANTAGE_API_KEY": probe_alpha_vantage,
    "FMP_API_KEY": probe_fmp,
    "ELEVENLABS_API_KEY": probe_elevenlabs,
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def resolve_key(name: str, env_file: dict[str, str]) -> tuple[str, str]:
    """Return (value, source) using the same precedence as the app."""
    if name == "ANTHROPIC_API_KEY":
        kc = keychain_lookup("ANTHROPIC_API_KEY")
        if kc:
            return kc, "keychain"
    if os.environ.get(name):
        return os.environ[name], "environment"
    if env_file.get(name):
        return env_file[name], ".env"
    return "", "missing"


def main() -> int:
    env_file = load_env_file(ENV_PATH)
    print(f"OROS key health check  ({ENV_PATH})")
    print("=" * 62)

    required_ok = True
    for name, probe in PROBES.items():
        value, source = resolve_key(name, env_file)
        tag = "REQUIRED" if name in REQUIRED else "optional"
        if not value:
            line = f"  MISSING   ({source})"
            ok = False
        else:
            ok, detail = probe(value)
            mark = "PASS " if ok else "FAIL "
            line = f"  {mark} {fingerprint(value)}  via {source}  {detail}"
        print(f"{name:<24} [{tag}]")
        print(line)
        if name in REQUIRED and not ok:
            required_ok = False

    print("=" * 62)
    if required_ok:
        print("All required keys passed.")
        return 0
    print("One or more REQUIRED keys failed. Rotate and re-run.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
