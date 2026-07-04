"""
audio_client.py — ElevenLabs Text-to-Speech wrapper.

Converts analysis text into MP3 audio via ElevenLabs API.
Aggressively cached by SHA-256 hash of cleaned text — same input
returns the same cached file forever, no repeat API calls.

Features:
    - Strips markdown formatting and emoji before TTS
    - Caches to data/cache/audio/<sha>.mp3
    - Graceful degradation: returns None if API key missing or call fails
    - Default voice: Adam (warm male, 'pNInz6obpgDQGcFmaJgB')
      Override via ELEVENLABS_VOICE_ID in .env

CLI test:
    PYTHONPATH=. python3 data/audio_client.py "Hello, this is a test."
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
_AUDIO_CACHE = _PROJECT_ROOT / "data" / "cache" / "audio"
_AUDIO_CACHE.mkdir(parents=True, exist_ok=True)

ELEVENLABS_API = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_VOICE_ADAM = "pNInz6obpgDQGcFmaJgB"      # warm male, analyst-like
MODEL_ID = "eleven_turbo_v2_5"                   # fastest, good quality


# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

def _load_env_once():
    """Load .env only once, cheaply."""
    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception:
        pass


def _get_api_key() -> Optional[str]:
    _load_env_once()
    return os.environ.get("ELEVENLABS_API_KEY", "").strip() or None


def _get_voice_id() -> str:
    _load_env_once()
    custom = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    return custom if custom else DEFAULT_VOICE_ADAM


# ---------------------------------------------------------------------------
# Text cleaning (strip markdown + emoji before TTS)
# ---------------------------------------------------------------------------

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F000-\U0001F9FF"
    "\u25CB\u25CF\u25B2\u25BC\u2713\u2717\u2190-\u21FF"
    "]+", flags=re.UNICODE,
)


# ---------------------------------------------------------------------------
# Audio-focused memo extraction
# ---------------------------------------------------------------------------

_AUDIO_SECTIONS_PRIORITY = (
    "Executive Summary",
    "Technical Setup (Weinstein)",
    "Analyst Take",
)


def extract_audio_summary(memo: str) -> str:
    """
    Pull only the Executive Summary + Technical Setup + Analyst Take
    sections out of a full research memo for TTS narration.

    Why: full memos are ~2000 words with stacked numerical citations and
    long financial breakdowns that don't make good audio. The 3 sections
    here are the actionable layer — what the stock IS, what the chart
    SAYS, and what the analyst RECOMMENDS. Cuts TTS cost ~80%.

    Returns the extracted prose (still markdown — `_clean_text_for_tts`
    handles the final stripping). If the memo doesn't have these section
    markers, falls back to the full memo so audio is never empty.
    """
    if not memo:
        return ""

    # Split memo into (header, body) chunks at every "## " heading
    pattern = re.compile(r"^##\s+(.+?)\s*$", flags=re.MULTILINE)
    matches = list(pattern.finditer(memo))
    if not matches:
        return memo

    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(memo)
        sections[title] = memo[body_start:body_end].strip()

    # Cherry-pick the 3 audio-priority sections
    out_parts: list[str] = []
    for wanted in _AUDIO_SECTIONS_PRIORITY:
        body = sections.get(wanted)
        if body is None:
            # Tolerant starts-with match so "Analyst Take" picks up
            # "Analyst Take and Recommendation" etc.
            for k, v in sections.items():
                if k.lower().startswith(wanted.lower()):
                    body = v
                    break
        if body:
            out_parts.append(f"## {wanted}\n{body}")

    if not out_parts:
        return memo

    # Lead with the # title if present (e.g., "# AXR Research Memo")
    title_match = re.search(r"^#\s+(.+?)\s*$", memo, flags=re.MULTILINE)
    intro = f"# {title_match.group(1)}\n\n" if title_match else ""
    return intro + "\n\n".join(out_parts)


def _clean_text_for_tts(text: str) -> str:
    """
    Turn markdown-rich analysis into clean spoken English.
    - Strip emoji, markdown formatting, bullet points
    - Collapse whitespace
    - Replace $X.XM / $X.XB with spoken equivalents
    """
    t = text
    t = _EMOJI_RE.sub("", t)

    # Markdown: bold, italic, code
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", t)
    t = re.sub(r"`(.+?)`", r"\1", t)
    t = re.sub(r"_(.+?)_", r"\1", t)

    # Headers
    t = re.sub(r"^#+\s+", "", t, flags=re.MULTILINE)

    # Bullets
    t = re.sub(r"^[\s]*[-•·*]\s+", "", t, flags=re.MULTILINE)

    # Footnote references: "[1]", "[12]", "[3][4]" → drop entirely.
    # Without this, ElevenLabs reads them as "one", "twelve" mid-sentence.
    # We keep [Source: ...] brackets because the TTS cleaner is also used
    # on text that may not have been footnoted yet.
    t = re.sub(r"\[\d+\]", "", t)

    # Money: "$5,749M" → "5.75 billion"; "$1.2B" → "1.2 billion"
    def _money(match):
        amt = match.group(1).replace(",", "")
        suffix = match.group(2) or ""
        try:
            val = float(amt)
        except ValueError:
            return match.group(0)
        if suffix.upper() == "B":
            return f"{val:.1f} billion dollars"
        if suffix.upper() == "M":
            # 1000+ million → billion
            if val >= 1000:
                return f"{val/1000:.2f} billion dollars"
            return f"{val:.0f} million dollars"
        if suffix.upper() == "K":
            return f"{val:.0f} thousand dollars"
        # Plain dollars
        return f"{val:,.2f} dollars"

    # \s? before [BMK] is now grouped so it only consumes whitespace
    # when a B/M/K suffix is actually present. Prevents "$28.53 stop"
    # from collapsing to "28.53 dollarsstop".
    t = re.sub(r"\$\s?([\d,]+\.?\d*)(?:\s?([BMK]))?\b", _money, t)

    # Percentages: "+9.1%" → "up 9.1 percent"; "-3%" → "down 3 percent"
    def _pct(match):
        sign = match.group(1)
        num = match.group(2)
        direction = ""
        if sign == "+":
            direction = "up "
        elif sign == "-":
            direction = "down "
        return f"{direction}{num} percent"

    t = re.sub(r"([+\-]?)(\d+\.?\d*)\s?%", _pct, t)

    # Ratios: "1.5x" → "1.5 times"
    t = re.sub(r"(\d+\.\d+)\s?x\b", r"\1 times", t)

    # Cleanup duplicates introduced when LLM wrote "up +5%" or "down -5%":
    # the percent regex turned "+" / "-" into "up "/"down ", giving "up up 5..."
    t = re.sub(r"\bup up\b", "up", t, flags=re.IGNORECASE)
    t = re.sub(r"\bdown down\b", "down", t, flags=re.IGNORECASE)

    # Strip horizontal-rule separators (markdown `---`) that bleed in from
    # extracted sections — the TTS reads them as "dash dash dash".
    t = re.sub(r"^[-—–]{2,}\s*$", "", t, flags=re.MULTILINE)

    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()

    return t


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """True if ElevenLabs API key is configured."""
    return _get_api_key() is not None


def _cache_path(text: str, voice_id: str) -> Path:
    """Deterministic cache path for given text + voice."""
    key = hashlib.sha256(f"{voice_id}|{text}".encode("utf-8")).hexdigest()[:24]
    return _AUDIO_CACHE / f"{key}.mp3"


def generate_audio(text: str, voice_id: Optional[str] = None,
                    force_refresh: bool = False) -> Optional[Path]:
    """
    Generate (or return cached) MP3 audio for `text`.

    Returns
    -------
    Path to MP3 file, or None on any failure (key missing, API down, etc.)
    """
    if not text or not text.strip():
        return None

    cleaned = _clean_text_for_tts(text)
    if not cleaned:
        return None

    # Length guard — ElevenLabs is expensive per character
    if len(cleaned) > 2500:
        cleaned = cleaned[:2497] + "..."

    voice_id = voice_id or _get_voice_id()
    cache_file = _cache_path(cleaned, voice_id)

    if cache_file.exists() and not force_refresh and cache_file.stat().st_size > 100:
        return cache_file

    api_key = _get_api_key()
    if not api_key:
        logger.info("ELEVENLABS_API_KEY not set — audio disabled")
        return None

    url = ELEVENLABS_API.format(voice_id=voice_id)
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": cleaned,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.15,
            "use_speaker_boost": True,
        },
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
    except Exception as exc:
        logger.warning(f"ElevenLabs request failed: {exc}")
        return None

    if resp.status_code != 200:
        logger.warning(
            f"ElevenLabs returned {resp.status_code}: {resp.text[:200]}"
        )
        return None

    try:
        cache_file.write_bytes(resp.content)
    except Exception as exc:
        logger.warning(f"Could not cache audio: {exc}")
        return None

    return cache_file


def cache_stats() -> dict:
    """Stats about the audio cache (for sidebar display)."""
    files = list(_AUDIO_CACHE.glob("*.mp3"))
    total_bytes = sum(f.stat().st_size for f in files)
    return {
        "count": len(files),
        "mb": round(total_bytes / 1024 / 1024, 2),
        "path": str(_AUDIO_CACHE),
    }


def clear_cache() -> int:
    """Delete all cached audio. Returns count deleted."""
    count = 0
    for f in _AUDIO_CACHE.glob("*.mp3"):
        try:
            f.unlink()
            count += 1
        except Exception:
            pass
    return count


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    text = " ".join(sys.argv[1:]) or (
        "NVDA is getting tired. Stage 3 topping. Price is 9 percent above "
        "the 30-week moving average, but the MA itself has flattened — "
        "momentum is fading."
    )
    print(f"Input ({len(text)} chars): {text}")
    cleaned = _clean_text_for_tts(text)
    print(f"Cleaned ({len(cleaned)} chars): {cleaned}")
    print(f"API key configured: {is_available()}")
    print(f"Voice ID: {_get_voice_id()}")
    if is_available():
        path = generate_audio(text)
        if path:
            print(f"✓ Generated: {path} ({path.stat().st_size:,} bytes)")
        else:
            print("✗ Generation failed")
    print(f"Cache stats: {cache_stats()}")
