"""
universe.py - Dynamic, full-market ticker universe for OROS.

Why this module exists
----------------------
The legacy scannable set was two hardcoded Python lists: the 80-name
`UNIVERSE` in `data/tickers.py` and a ~150-name pool in
`ml/momentum_screener.py`. Any symbol absent from both was invisible to the
scanner, which is the root cause of the SPCX miss: it was never in a list, so
it was never priced, staged, or memoed.

This module replaces the hardcoded coverage with a universe built from
authoritative listing sources, refreshed on a schedule and filtered for
liquidity. It is ADDITIVE: `tickers.py` still owns specialist categories and
curated metadata, and every existing import keeps working. This module feeds
the scanner with the full investable set.

Sources
-------
* Nasdaq Trader symbol directory (most complete, includes ETFs):
    - nasdaqlisted.txt  : Nasdaq-listed securities
    - otherlisted.txt   : NYSE, NYSE American, NYSE Arca, Cboe BZX, etc.
* SEC company_tickers.json : CIK mapping for the 10-K RAG (operating companies).

Design notes
------------
* Network fetches use a descriptive User-Agent (SEC requirement) and are cached
  to disk so a CRON run does not refetch listings every time.
* Phase 1 scope: build the raw universe + a liquidity filter. The full liquidity
  pass over ~8,000 names rides on the daily price cache built in Phase 2; the
  `liquidity_filter` here works on any sample so it is testable today.
* Balanced default filter: drop sub-$3 names and thin tickers, keep names like
  SPCX. Tunable via the constants below.

Run the self-test:
    PYTHONPATH=. python3 data/universe.py
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "universe"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_LISTING_TTL = 24 * 60 * 60          # refresh listing files once a day
_SEC_TTL = 7 * 24 * 60 * 60          # SEC CIK map changes slowly

# SEC requires a descriptive UA. Override via env per .env.example.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "OROS Research admin@adminaillc.com")

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# Balanced liquidity defaults (tunable).
PRICE_MIN = 3.0                      # drop sub-$3 / penny names
AVG_DOLLAR_VOL_MIN = 2_000_000.0     # >= $2M average daily dollar volume
LIQUIDITY_LOOKBACK_DAYS = 20

# Symbol hygiene: exclude warrants/units/rights/preferreds and test issues.
# These suffix patterns appear in exchange feeds for non-common securities.
_EXCLUDE_SUFFIXES = ("W", "U", "R", "P")     # applied only to 5th char of 5-char symbols
_EXCLUDE_CHARS = ("$", ".", "+")             # preferreds / when-issued / special


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class UniverseEntry:
    symbol: str
    name: str
    exchange: str            # NASDAQ, NYSE, NYSE American, NYSE Arca, Cboe, ...
    is_etf: bool
    cik: Optional[str] = None


# ---------------------------------------------------------------------------
# Cached fetch helper
# ---------------------------------------------------------------------------
def _fetch(url: str, cache_name: str, ttl: int, headers: Optional[dict] = None) -> str:
    """Fetch a URL with on-disk caching. Returns the body text."""
    cache_path = _CACHE_DIR / cache_name
    if cache_path.exists() and (time.time() - cache_path.stat().st_mtime) < ttl:
        return cache_path.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(url, headers=headers or {"User-Agent": SEC_USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8", "replace")
    cache_path.write_text(body, encoding="utf-8")
    return body


# ---------------------------------------------------------------------------
# Listing parsers
# ---------------------------------------------------------------------------
def _parse_pipe_table(text: str) -> list[dict]:
    """Parse a Nasdaq Trader pipe-delimited file, dropping the footer line."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("|")
    rows = []
    for ln in lines[1:]:
        if ln.startswith("File Creation Time"):
            continue
        parts = ln.split("|")
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts)))
    return rows


def _is_clean_common_or_etf(symbol: str) -> bool:
    """Keep common stock and ETFs; drop warrants, units, rights, preferreds."""
    if not symbol or any(c in symbol for c in _EXCLUDE_CHARS):
        return False
    if len(symbol) == 5 and symbol[-1] in _EXCLUDE_SUFFIXES:
        # 5-letter Nasdaq symbols ending in W/U/R/P are usually non-common.
        return False
    if not symbol.isalpha():
        return False
    return True


def fetch_nasdaq_listed() -> list[UniverseEntry]:
    text = _fetch(NASDAQ_LISTED_URL, "nasdaqlisted.txt", _LISTING_TTL)
    out = []
    for r in _parse_pipe_table(text):
        if r.get("Test Issue") == "Y":
            continue
        sym = r.get("Symbol", "").strip()
        if not _is_clean_common_or_etf(sym):
            continue
        out.append(UniverseEntry(
            symbol=sym,
            name=r.get("Security Name", "").strip(),
            exchange="NASDAQ",
            is_etf=(r.get("ETF", "N") == "Y"),
        ))
    return out


def fetch_other_listed() -> list[UniverseEntry]:
    text = _fetch(OTHER_LISTED_URL, "otherlisted.txt", _LISTING_TTL)
    exch_map = {
        "A": "NYSE American", "N": "NYSE", "P": "NYSE Arca",
        "Z": "Cboe BZX", "V": "IEX",
    }
    out = []
    for r in _parse_pipe_table(text):
        if r.get("Test Issue") == "Y":
            continue
        sym = (r.get("ACT Symbol") or r.get("NASDAQ Symbol") or "").strip()
        if not _is_clean_common_or_etf(sym):
            continue
        out.append(UniverseEntry(
            symbol=sym,
            name=r.get("Security Name", "").strip(),
            exchange=exch_map.get(r.get("Exchange", ""), r.get("Exchange", "OTHER")),
            is_etf=(r.get("ETF", "N") == "Y"),
        ))
    return out


def fetch_sec_cik_map() -> dict[str, str]:
    """Return {SYMBOL: zero-padded-CIK} for SEC filers (for the 10-K RAG)."""
    try:
        text = _fetch(SEC_TICKERS_URL, "company_tickers.json", _SEC_TTL)
        data = json.loads(text)
    except Exception as e:  # noqa: BLE001
        logger.warning("SEC CIK map fetch failed: %s", e)
        return {}
    out = {}
    for row in data.values():
        sym = str(row.get("ticker", "")).upper().strip()
        cik = str(row.get("cik_str", "")).strip()
        if sym and cik:
            out[sym] = cik.zfill(10)
    return out


# ---------------------------------------------------------------------------
# Universe builder
# ---------------------------------------------------------------------------
def build_raw_universe(attach_cik: bool = True) -> dict[str, UniverseEntry]:
    """Union of all exchange-listed clean common stock + ETFs, keyed by symbol."""
    entries: dict[str, UniverseEntry] = {}
    for e in fetch_nasdaq_listed() + fetch_other_listed():
        entries.setdefault(e.symbol, e)        # first listing wins on dup
    if attach_cik:
        cik_map = fetch_sec_cik_map()
        for sym, e in entries.items():
            e.cik = cik_map.get(sym)
    return entries


def liquidity_filter(
    symbols: Iterable[str],
    price_min: float = PRICE_MIN,
    dollar_vol_min: float = AVG_DOLLAR_VOL_MIN,
    lookback_days: int = LIQUIDITY_LOOKBACK_DAYS,
) -> dict[str, dict]:
    """
    Keep symbols whose last close >= price_min AND average dollar volume over
    the lookback window >= dollar_vol_min. Returns {symbol: metrics} for passers.

    Uses yfinance batch download. In Phase 2 this reads the cached daily bars
    instead of pulling live, so the full ~8,000 pass is cheap.
    """
    symbols = list(dict.fromkeys(s.upper() for s in symbols))
    if not symbols:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed; cannot run liquidity filter.")
        return {}

    passers: dict[str, dict] = {}
    period = f"{max(lookback_days * 2, 40)}d"
    data = yf.download(
        symbols, period=period, interval="1d",
        auto_adjust=False, group_by="ticker", threads=True, progress=False,
    )
    for sym in symbols:
        try:
            df = data[sym] if len(symbols) > 1 else data
            df = df.dropna()
            if df.empty:
                continue
            close = float(df["Close"].iloc[-1])
            vol = df["Volume"].tail(lookback_days)
            px = df["Close"].tail(lookback_days)
            avg_dollar_vol = float((px * vol).mean())
            if close >= price_min and avg_dollar_vol >= dollar_vol_min:
                passers[sym] = {
                    "close": round(close, 2),
                    "avg_dollar_vol": round(avg_dollar_vol, 0),
                }
        except Exception:
            continue
    return passers


def get_universe(filtered: bool = False, **filter_kwargs) -> dict[str, UniverseEntry]:
    """
    Main entry point.
      filtered=False : the full raw listed universe (fast, no price data).
      filtered=True  : apply the liquidity filter (pulls price data).
    """
    raw = build_raw_universe()
    if not filtered:
        return raw
    keep = liquidity_filter(raw.keys(), **filter_kwargs)
    return {s: e for s, e in raw.items() if s in keep}


# ---------------------------------------------------------------------------
# Liquidity-ranked universe (built by the nightly job; read by the Portfolio)
# ---------------------------------------------------------------------------
_RANK_PATH = _CACHE_DIR / "liquid_ranked.json"


def build_liquid_ranking(min_price: float = 5.0, min_dollar_vol: float = 5_000_000.0,
                         max_keep: int = 3000) -> list[str]:
    """Rank every cached name by 20-day average dollar volume, keep the investable
    ones, and persist the ordered list. Run after the nightly price refresh so the
    Portfolio scans by liquidity, not alphabetically."""
    from data import price_cache
    rows = []
    for s in price_cache.cached_symbols():
        df = price_cache.get_history(s)
        if df is None or len(df) < 30:
            continue
        c, v = df["Close"], df["Volume"]
        price = float(c.iloc[-1])
        adv = float((c.tail(20) * v.tail(20)).mean())
        if price >= min_price and adv >= min_dollar_vol:
            rows.append((adv, s))
    rows.sort(reverse=True)
    ranked = [s for _, s in rows[:max_keep]]
    _RANK_PATH.write_text(json.dumps(ranked))
    logger.info("liquid ranking: %d names kept (of %d cached)", len(ranked), len(rows))
    return ranked


def load_liquid_ranking() -> Optional[list[str]]:
    if _RANK_PATH.exists():
        try:
            return json.loads(_RANK_PATH.read_text())
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def _selftest() -> None:
    print("Building raw universe from SEC + exchange listings ...")
    raw = build_raw_universe()
    by_exch: dict[str, int] = {}
    etf = 0
    with_cik = 0
    for e in raw.values():
        by_exch[e.exchange] = by_exch.get(e.exchange, 0) + 1
        etf += int(e.is_etf)
        with_cik += int(bool(e.cik))
    print(f"\nTotal symbols : {len(raw)}")
    print(f"ETFs          : {etf}")
    print(f"With SEC CIK  : {with_cik}")
    print("By exchange   :", dict(sorted(by_exch.items(), key=lambda x: -x[1])))

    target = "SPCX"
    if target in raw:
        e = raw[target]
        print(f"\nSPCX CHECK: FOUND -> {e.name} | {e.exchange} | ETF={e.is_etf} | CIK={e.cik}")
    else:
        print(f"\nSPCX CHECK: not in listings (may be delisted). "
              f"Sample of close matches: "
              f"{[s for s in raw if s.startswith('SPC')][:10]}")

    sample = ["SPCX", "AAPL", "O", "PWR", "NVDA", "PLTR"]
    sample = [s for s in sample if s in raw][:6]
    print(f"\nLiquidity filter demo on sample {sample} "
          f"(price>=${PRICE_MIN}, $vol>=${AVG_DOLLAR_VOL_MIN:,.0f}):")
    for sym, m in liquidity_filter(sample).items():
        print(f"  PASS {sym:6} close=${m['close']:<9} avg$vol=${m['avg_dollar_vol']:,.0f}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _selftest()
