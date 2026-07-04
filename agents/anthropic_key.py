"""
anthropic_key.py — single source of truth for resolving the Anthropic API key.

Resolution order: Mac Keychain first, then `.env` / environment. Crucially,
a stale Keychain key falls back to a working `.env` key: each candidate is
validated with a zero-token `models.list()` auth probe and the first that
authenticates is returned. This avoids the failure where a revoked Keychain
key is returned blindly and every Claude call 401s with `invalid x-api-key`.

Public API:
    candidate_anthropic_keys() -> list[str]   keys in priority order, de-duped
    key_authenticates(key)     -> bool        True unless a genuine 401
    resolve_anthropic_key()    -> str | None   first authenticating key, else None
    require_anthropic_key()    -> str          like resolve, but raises if none
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def candidate_anthropic_keys() -> list[str]:
    """All plausible keys in priority order: Keychain first, then .env.
    Returned de-duplicated and order-preserving so a stale Keychain key can
    fall back to a working .env key."""
    candidates: list[str] = []

    # Keychain first
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "ANTHROPIC_API_KEY", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            candidates.append(r.stdout.strip())
    except Exception:
        pass

    # .env / environment fallback
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key:
        candidates.append(env_key)

    # De-dupe, preserve order
    seen: set[str] = set()
    return [k for k in candidates if not (k in seen or seen.add(k))]


def key_authenticates(key: str) -> bool:
    """Zero-token auth probe. True if the key is accepted, False only on a
    genuine auth rejection (401). Transient/network errors return True so a
    flaky connection never masks a valid key."""
    try:
        import anthropic
        anthropic.Anthropic(api_key=key).models.list(limit=1)
        return True
    except Exception as exc:
        cls = exc.__class__.__name__.lower()
        msg = str(exc).lower()
        if "authentication" in cls or "invalid x-api-key" in msg or "401" in msg:
            return False
        return True  # network/other — don't reject a possibly-valid key


def resolve_anthropic_key() -> Optional[str]:
    """First candidate that actually authenticates, so a stale Keychain key
    transparently falls back to a working .env key. None if nothing works."""
    for key in candidate_anthropic_keys():
        if key_authenticates(key):
            return key
    return None


_FALLBACK_MODEL = "claude-sonnet-4-6"  # current Sonnet; alias auto-tracks point releases


def default_model() -> str:
    """Model id for agent/chat calls. Env override (ANTHROPIC_MODEL) wins so a
    model rename needs no code edit; otherwise the current Sonnet alias. Using
    the alias form (like the classifier's claude-haiku-4-5) avoids the dated
    snapshot pin that 404'd once that snapshot was retired by the API."""
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or _FALLBACK_MODEL


def require_anthropic_key() -> str:
    """Like resolve_anthropic_key() but raises a helpful error if no usable
    key is found (none configured, or every candidate rejected)."""
    key = resolve_anthropic_key()
    if key:
        return key
    if candidate_anthropic_keys():
        raise RuntimeError(
            "ANTHROPIC_API_KEY found but every candidate was rejected (401). "
            "Update the Keychain key:  security add-generic-password -U -a \"$USER\" "
            "-s ANTHROPIC_API_KEY -w 'sk-ant-...'  (or fix the key in .env)."
        )
    raise RuntimeError("ANTHROPIC_API_KEY not set. Add to Keychain or .env.")
