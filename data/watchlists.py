"""
watchlists.py — Persisted ticker watchlists for Trader Mode.

Watchlists live at data/watchlists/*.txt — one ticker per line.
A 'default.txt' is auto-created from the full 50-ticker UNIVERSE on
first use.

Functions:
    list_watchlists()                 → list[str]           (names)
    load_watchlist(name)              → list[str]           (tickers)
    save_watchlist(name, tickers)     → Path
    delete_watchlist(name)            → bool
    add_ticker(name, ticker)          → list[str]
    remove_ticker(name, ticker)       → list[str]
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_WATCHLIST_DIR = Path(__file__).parent / "watchlists"
_WATCHLIST_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_NAME = "default"


def _path(name: str) -> Path:
    # Sanitize name — alphanumeric, underscore, dash
    safe = "".join(c for c in name if c.isalnum() or c in "_-").strip()
    if not safe:
        safe = "unnamed"
    return _WATCHLIST_DIR / f"{safe}.txt"


def _ensure_default():
    """Create default.txt from UNIVERSE if it doesn't exist."""
    p = _path(DEFAULT_NAME)
    if not p.exists():
        try:
            from data.tickers import UNIVERSE
            tickers = list(UNIVERSE.keys())
        except Exception:
            tickers = []
        p.write_text("\n".join(tickers) + "\n" if tickers else "")


def list_watchlists() -> list[str]:
    """Return all watchlist names (without .txt extension)."""
    _ensure_default()
    return sorted(p.stem for p in _WATCHLIST_DIR.glob("*.txt"))


def load_watchlist(name: str) -> list[str]:
    """Load tickers from a watchlist file. Returns empty list if not found."""
    _ensure_default()
    p = _path(name)
    if not p.exists():
        return []
    tickers: list[str] = []
    for line in p.read_text().splitlines():
        line = line.strip().upper()
        if line and not line.startswith("#"):
            tickers.append(line)
    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in tickers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def save_watchlist(name: str, tickers: list[str]) -> Path:
    """Save a list of tickers to a watchlist file."""
    p = _path(name)
    cleaned = []
    seen: set[str] = set()
    for t in tickers:
        t = t.strip().upper()
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    p.write_text("\n".join(cleaned) + "\n" if cleaned else "")
    return p


def delete_watchlist(name: str) -> bool:
    """Delete a watchlist (the default can't be deleted)."""
    if name == DEFAULT_NAME:
        return False
    p = _path(name)
    if p.exists():
        p.unlink()
        return True
    return False


def add_ticker(name: str, ticker: str) -> list[str]:
    """Add a ticker to a watchlist. Returns updated list."""
    tickers = load_watchlist(name)
    t = ticker.strip().upper()
    if t and t not in tickers:
        tickers.append(t)
        save_watchlist(name, tickers)
    return tickers


def remove_ticker(name: str, ticker: str) -> list[str]:
    """Remove a ticker from a watchlist. Returns updated list."""
    tickers = load_watchlist(name)
    t = ticker.strip().upper()
    if t in tickers:
        tickers = [x for x in tickers if x != t]
        save_watchlist(name, tickers)
    return tickers


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Available watchlists:")
    for name in list_watchlists():
        tickers = load_watchlist(name)
        print(f"  {name} ({len(tickers)} tickers): {', '.join(tickers[:8])}{'...' if len(tickers) > 8 else ''}")
