"""
calendar_context.py — Chapter 3 seasonal and timing context.

Replicates the spirit of the Merrill monthly-seasonality study and the
Hirsch day-of-week findings using public S&P 500 data since 1950,
extended through the current date. We rebuild the tables ourselves from
yfinance so nothing in this module reproduces the book's numbers
verbatim — it computes them from raw history, then captions the result.

Three precomputed historical tables:

    monthly_positivity   percent of years each month was net positive
                         (close on last trading day of month vs prior month)
    dow_positivity       percent of days each weekday closed up vs prior
    holiday_proximity    list of "day-before-holiday" dates for the last
                         several years, used to flag adjacency

The user-facing API is `get_calendar_context(date=today)` which returns a
CalendarContext dataclass with everything the heatmap widget and the
Chartist Strategist need.

Caching:
    Seasonal tables are written to data/cache/calendar/seasonal_tables.json
    on first compute. They are refreshed automatically when the most
    recent data point in cache is older than 30 days.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BENCHMARK_INDEX = "^GSPC"        # S&P 500, daily history back to 1950
SEASONAL_START_YEAR = 1950        # match the Merrill/Hirsch lookback window
CACHE_REFRESH_DAYS = 30           # rebuild tables monthly

_PROJECT_ROOT = Path(__file__).parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "cache" / "calendar"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_SEASONAL_CACHE = _CACHE_DIR / "seasonal_tables.json"

# Days that fall in this set (Mark Twain via Weinstein Ch. 3) are flagged
# as "dangerous." This is a descriptive caption, not a directive.
_TWAIN_DANGEROUS_MONTHS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}
# Actually Twain listed every month — the joke is the markets are
# dangerous all the time. We flag only the historically weak months
# per Merrill: Feb, May, Jun, Sep.
_WEAK_MONTHS = {2, 5, 6, 9}

_MONTH_NAME = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}
_DOW_NAME = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday",
    3: "Thursday", 4: "Friday",
}

# ---------------------------------------------------------------------------
# Presidential cycle (Weinstein Ch. 3, "The Ideal Time to Buy", pp. 69-70)
# ---------------------------------------------------------------------------
# Weinstein calls the four-year presidential cycle "the most important" cycle
# and gives a year-by-year characterization. He also explicitly debunks the
# "a Republican is better for stocks" myth (Merrill: the Dow gained MORE points
# under Democratic presidents, partly from higher inflation) and stresses the
# CYCLE matters more than the party. We therefore condition the seasonality
# chart on the cycle YEAR (the effect the book emphasizes) and surface the
# party only as labelled context with Weinstein's caveat.

# Party of the president holding office for most of each calendar year.
# (Inauguration is Jan 20, so the year is assigned to the incoming president.)
_PARTY_RANGES = [
    (1949, 1952, "Democratic"),    # Truman
    (1953, 1960, "Republican"),    # Eisenhower
    (1961, 1968, "Democratic"),    # Kennedy / Johnson
    (1969, 1976, "Republican"),    # Nixon / Ford
    (1977, 1980, "Democratic"),    # Carter
    (1981, 1992, "Republican"),    # Reagan / Bush
    (1993, 2000, "Democratic"),    # Clinton
    (2001, 2008, "Republican"),    # Bush
    (2009, 2016, "Democratic"),    # Obama
    (2017, 2020, "Republican"),    # Trump I
    (2021, 2024, "Democratic"),    # Biden
    (2025, 2028, "Republican"),    # Trump II
]

# Faithful paraphrase of Weinstein's per-year characterization (pp. 69-70).
_CYCLE_PHASES = {
    1: ("Post-Election (Year 1)",
        "Weinstein Ch.3: the year after an election is 'usually a disaster' — "
        "first years like 1969, 1973, 1977 and 1981 all saw bear markets. "
        "Go easy unless the charts are strongly positive."),
    2: ("Midterm (Year 2)",
        "Weinstein Ch.3: the bear 'will continue until a bottom is reached "
        "around mid-year' (as in Aug 1982), then 'the rest of year two is "
        "bullish.' The midterm low is the classic buying opportunity."),
    3: ("Pre-Election (Year 3)",
        "Weinstein Ch.3: 'the third year of the presidential term is the best "
        "one of the cycle.' Historically the strongest — favor offense."),
    4: ("Election (Year 4)",
        "Weinstein Ch.3: the election year is 'a choppy one, with weakness "
        "usually in the first half and strength in the second half.'"),
}

_PARTY_NOTE = (
    "Weinstein Ch.3 debunks the 'Republicans are better for stocks' myth: per "
    "Merrill, the Dow gained more points under Democratic presidents (partly "
    "from higher inflation). He stresses the four-year CYCLE matters far more "
    "than which party holds office — so the chart above is conditioned on the "
    "cycle year, with party shown only as context."
)


def _party_for_year(year: int) -> str:
    for lo, hi, party in _PARTY_RANGES:
        if lo <= year <= hi:
            return party
    return "Unknown"


def _cycle_year(year: int) -> int:
    """Return the presidential-cycle year: 1=post-election, 2=midterm,
    3=pre-election, 4=election. Election years are divisible by 4."""
    pos = year % 4
    return 4 if pos == 0 else pos


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CalendarContext:
    """Plain-language seasonal and timing context for a given date."""
    date: str                                            # ISO yyyy-mm-dd
    monthly_positivity: dict[int, float] = field(default_factory=dict)
    dow_positivity: dict[int, float] = field(default_factory=dict)

    # Today's slot in the tables
    current_month: int = 0
    current_month_pct: float = 0.0                       # 0.0-1.0
    current_month_rank: int = 0                          # 1 = strongest
    current_month_label: str = ""

    current_dow: int = 0                                  # 0=Mon, 4=Fri
    current_dow_pct: float = 0.0
    current_dow_rank: int = 0
    current_dow_label: str = ""

    # Holiday context
    holiday_adjacency: bool = False
    holiday_name: Optional[str] = None
    holiday_position: Optional[str] = None               # "before" or "after"

    # Weinstein/Merrill "weak months" flag for caption use
    weak_month: bool = False

    # Presidential cycle (Weinstein Ch.3, pp.69-70)
    presidential_cycle_year: int = 0                      # 1-4
    cycle_phase_label: str = ""                           # "Midterm (Year 2)"
    cycle_phase_note: str = ""                            # Weinstein's words
    president_party: str = ""                             # "Republican"/"Democratic"
    party_note: str = ""                                  # Weinstein's party caveat
    cycle_monthly_positivity: dict[int, float] = field(default_factory=dict)
    cycle_dow_positivity: dict[int, float] = field(default_factory=dict)
    cycle_sample_years: int = 0                           # # of distinct years in sample
    cycle_book_note: str = ""                             # one-line citable summary

    # Plain-language verdict line ready for the dossier
    verdict_line: str = ""

    # Meta
    history_start_year: int = SEASONAL_START_YEAR
    history_end_year: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# History loader
# ---------------------------------------------------------------------------

def _fetch_history() -> Optional[pd.DataFrame]:
    """Pull S&P 500 daily history since SEASONAL_START_YEAR."""
    try:
        import yfinance as yf
        df = yf.Ticker(BENCHMARK_INDEX).history(
            start=f"{SEASONAL_START_YEAR}-01-01",
            interval="1d",
            auto_adjust=True,
        )
        if df is None or df.empty:
            return None
        # Strip tz so date math works against naive datetimes downstream
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df[["Close"]]
    except Exception as exc:
        logger.warning(f"yfinance fetch failed for {BENCHMARK_INDEX}: {exc}")
        return None


# ---------------------------------------------------------------------------
# Seasonal table computation
# ---------------------------------------------------------------------------

def _compute_monthly_positivity(daily: pd.DataFrame) -> dict[int, float]:
    """For each month 1-12, what percent of years closed net positive?

    Method: resample to month-end, compute month-over-month % change,
    then for each month group the fraction of years where the change > 0.
    """
    monthly = daily["Close"].resample("ME").last()
    rets = monthly.pct_change().dropna()
    positivity: dict[int, float] = {}
    for m in range(1, 13):
        slice_ = rets[rets.index.month == m]
        if len(slice_) > 0:
            positivity[m] = float((slice_ > 0).mean())
        else:
            positivity[m] = 0.5
    return positivity


def _compute_monthly_positivity_by_cycle(
    daily: pd.DataFrame,
) -> tuple[dict[int, dict[int, float]], dict[int, int]]:
    """Monthly positivity conditioned on the presidential-cycle year.

    Returns ({cycle_year: {month: pct}}, {cycle_year: distinct_years_count}).
    Lets the heatmap recolor to show, e.g., 'June in midterm years' rather
    than June across all years — the Weinstein Ch.3 cycle effect.
    """
    monthly = daily["Close"].resample("ME").last()
    rets = monthly.pct_change().dropna()
    years = [int(y) for y in rets.index.year]
    months = [int(m) for m in rets.index.month]
    cyc = [_cycle_year(y) for y in years]
    pos = (rets.to_numpy() > 0)

    table: dict[int, dict[int, float]] = {1: {}, 2: {}, 3: {}, 4: {}}
    sample_years: dict[int, int] = {}
    for cy in (1, 2, 3, 4):
        sample_years[cy] = len({years[i] for i in range(len(years)) if cyc[i] == cy})
        for m in range(1, 13):
            hits = [pos[i] for i in range(len(pos)) if cyc[i] == cy and months[i] == m]
            table[cy][m] = float(sum(hits) / len(hits)) if hits else 0.5
    return table, sample_years


def _compute_dow_positivity(daily: pd.DataFrame) -> dict[int, float]:
    """For each weekday 0-4 (Mon-Fri), what percent of days closed up vs prior?"""
    rets = daily["Close"].pct_change().dropna()
    positivity: dict[int, float] = {}
    for d in range(5):
        slice_ = rets[rets.index.dayofweek == d]
        if len(slice_) > 0:
            positivity[d] = float((slice_ > 0).mean())
        else:
            positivity[d] = 0.5
    return positivity


def _compute_dow_positivity_by_cycle(
    daily: pd.DataFrame,
) -> dict[int, dict[int, float]]:
    """Day-of-week positivity conditioned on the presidential-cycle year.

    Returns {cycle_year: {dow: pct}} so the day-of-week strip recolors with
    the same lens as the monthly grid (e.g. 'Mondays in midterm years').
    """
    rets = daily["Close"].pct_change().dropna()
    years = [int(y) for y in rets.index.year]
    dows = [int(d) for d in rets.index.dayofweek]
    cyc = [_cycle_year(y) for y in years]
    pos = (rets.to_numpy() > 0)

    table: dict[int, dict[int, float]] = {1: {}, 2: {}, 3: {}, 4: {}}
    for cy in (1, 2, 3, 4):
        for d in range(5):
            hits = [pos[i] for i in range(len(pos)) if cyc[i] == cy and dows[i] == d]
            table[cy][d] = float(sum(hits) / len(hits)) if hits else 0.5
    return table


def _build_holiday_proximity_set(daily: pd.DataFrame) -> dict[str, str]:
    """Build a map of {iso_date: holiday_descriptor} for trading days that
    are immediately before or after a major US market holiday.

    Uses pandas USFederalHolidayCalendar (close enough for our captions;
    excludes Good Friday and a few quirks but covers the headline holidays).
    """
    from pandas.tseries.holiday import USFederalHolidayCalendar
    cal = USFederalHolidayCalendar()
    start = daily.index.min()
    # Look ahead 60 days so today and tomorrow are covered even if the
    # cache is a couple of weeks stale
    end = daily.index.max() + pd.Timedelta(days=60)
    holidays = cal.holidays(start=start, end=end)
    trading_days = set(daily.index.normalize())

    proximity: dict[str, str] = {}
    for h in holidays:
        h = pd.Timestamp(h).normalize()
        # Find the trading day strictly before the holiday (skip weekends)
        before = h - pd.Timedelta(days=1)
        for _ in range(5):
            if before in trading_days:
                # Try to name the holiday: pandas returns observed dates
                # but the actual holiday name is not on the index, so we
                # infer it from the date.
                name = _name_for_holiday(h)
                proximity[before.strftime("%Y-%m-%d")] = f"day before {name}"
                break
            before -= pd.Timedelta(days=1)
        # Day after
        after = h + pd.Timedelta(days=1)
        for _ in range(5):
            if after in trading_days:
                name = _name_for_holiday(h)
                proximity[after.strftime("%Y-%m-%d")] = f"day after {name}"
                break
            after += pd.Timedelta(days=1)
    return proximity


def _name_for_holiday(h: pd.Timestamp) -> str:
    """Best-effort holiday naming from the observed date."""
    m, d = h.month, h.day
    if m == 1 and d <= 3:
        return "New Year"
    if m == 1 and 14 <= d <= 21:
        return "MLK Day"
    if m == 2 and 15 <= d <= 22:
        return "Presidents Day"
    if m == 5 and d >= 25:
        return "Memorial Day"
    if (m == 7 and d <= 5) or (m == 6 and d == 30):
        return "Independence Day"
    if m == 9 and d <= 7:
        return "Labor Day"
    if m == 11 and 22 <= d <= 28:
        return "Thanksgiving"
    if m == 12 and 24 <= d <= 26:
        return "Christmas"
    return "holiday"


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def _load_cached_tables() -> Optional[dict]:
    if not _SEASONAL_CACHE.exists():
        return None
    try:
        with open(_SEASONAL_CACHE) as f:
            data = json.load(f)
        # Check freshness
        built = datetime.fromisoformat(data.get("built_at", "1970-01-01"))
        if (datetime.now() - built).days > CACHE_REFRESH_DAYS:
            return None
        return data
    except Exception:
        return None


def _save_cached_tables(data: dict) -> None:
    try:
        with open(_SEASONAL_CACHE, "w") as f:
            json.dump(data, f, default=str)
    except Exception as exc:
        logger.warning(f"calendar cache write failed: {exc}")


def _build_tables(force: bool = False) -> dict:
    """Build (or load from cache) the precomputed seasonal tables."""
    if not force:
        cached = _load_cached_tables()
        if cached is not None:
            # Rehydrate int keys (JSON serializes them as strings)
            cached["monthly_positivity"] = {
                int(k): v for k, v in cached["monthly_positivity"].items()
            }
            cached["dow_positivity"] = {
                int(k): v for k, v in cached["dow_positivity"].items()
            }
            # Cycle table is nested {cycle_year: {month: pct}}. Older caches
            # (built before this feature) won't have it — force a rebuild so
            # the cycle view is populated rather than silently empty.
            raw_cycle = cached.get("cycle_monthly_positivity")
            raw_cycle_dow = cached.get("cycle_dow_positivity")
            # Older caches (before cycle, or before the dow-cycle table) lack
            # one of these — rebuild so both lenses are populated.
            if not raw_cycle or not raw_cycle_dow:
                return _build_tables(force=True)
            cached["cycle_monthly_positivity"] = {
                int(cy): {int(m): v for m, v in months.items()}
                for cy, months in raw_cycle.items()
            }
            cached["cycle_dow_positivity"] = {
                int(cy): {int(d): v for d, v in dows.items()}
                for cy, dows in raw_cycle_dow.items()
            }
            cached["cycle_sample_years"] = {
                int(k): v for k, v in cached.get("cycle_sample_years", {}).items()
            }
            return cached

    daily = _fetch_history()
    if daily is None or daily.empty:
        # Fall back to known long-run averages (1950-2024) so the dossier
        # still renders even if yfinance is down.
        return {
            "monthly_positivity": {
                1: 0.62, 2: 0.55, 3: 0.62, 4: 0.71, 5: 0.59, 6: 0.55,
                7: 0.59, 8: 0.55, 9: 0.45, 10: 0.61, 11: 0.66, 12: 0.74,
            },
            "dow_positivity": {0: 0.51, 1: 0.51, 2: 0.55, 3: 0.53, 4: 0.56},
            "holiday_proximity": {},
            "cycle_monthly_positivity": {},   # empty → UI falls back to all-years
            "cycle_dow_positivity": {},
            "cycle_sample_years": {},
            "history_start_year": SEASONAL_START_YEAR,
            "history_end_year": datetime.now().year,
            "built_at": datetime.now().isoformat(),
            "from_fallback": True,
        }

    cycle_table, cycle_counts = _compute_monthly_positivity_by_cycle(daily)
    cycle_dow_table = _compute_dow_positivity_by_cycle(daily)
    tables = {
        "monthly_positivity": _compute_monthly_positivity(daily),
        "dow_positivity": _compute_dow_positivity(daily),
        "holiday_proximity": _build_holiday_proximity_set(daily),
        "cycle_monthly_positivity": cycle_table,
        "cycle_dow_positivity": cycle_dow_table,
        "cycle_sample_years": cycle_counts,
        "history_start_year": int(daily.index.min().year),
        "history_end_year": int(daily.index.max().year),
        "built_at": datetime.now().isoformat(),
        "from_fallback": False,
    }
    _save_cached_tables(tables)
    return tables


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def get_calendar_context(date: Optional[datetime] = None) -> CalendarContext:
    """Return seasonal and timing context for a given date.

    If `date` is None, uses today (naive local time).
    """
    if date is None:
        date = datetime.now()
    iso = date.strftime("%Y-%m-%d")

    tables = _build_tables()
    monthly = tables["monthly_positivity"]
    dow = tables["dow_positivity"]
    holidays = tables.get("holiday_proximity", {})

    cur_month = date.month
    cur_month_pct = monthly.get(cur_month, 0.5)
    # Rank 1 = strongest; ties broken by month number
    month_ranking = sorted(monthly.items(), key=lambda x: -x[1])
    cur_month_rank = next(
        (i + 1 for i, (m, _) in enumerate(month_ranking) if m == cur_month), 0
    )

    cur_dow = date.weekday()  # 0=Mon, 6=Sun
    cur_dow_pct = dow.get(cur_dow, 0.5)
    dow_ranking = sorted(dow.items(), key=lambda x: -x[1])
    cur_dow_rank = next(
        (i + 1 for i, (d, _) in enumerate(dow_ranking) if d == cur_dow), 0
    )

    # Labels
    n_months = len(monthly)
    month_name = _MONTH_NAME.get(cur_month, "Unknown")
    cur_month_label = (
        f"{month_name} ({cur_month_pct:.0%} of years positive, "
        f"ranked {cur_month_rank}/{n_months})"
    )

    n_dow = len(dow)
    if cur_dow in _DOW_NAME:
        dow_name = _DOW_NAME[cur_dow]
        cur_dow_label = (
            f"{dow_name} ({cur_dow_pct:.0%} of days up, "
            f"ranked {cur_dow_rank}/{n_dow})"
        )
    else:
        dow_name = date.strftime("%A")
        cur_dow_label = f"{dow_name} (weekend, no trading reference)"

    # Holiday adjacency
    hol_desc = holidays.get(iso)
    holiday_adjacency = hol_desc is not None
    holiday_name = None
    holiday_position = None
    if hol_desc:
        # "day before Thanksgiving" → position="before", name="Thanksgiving"
        if hol_desc.startswith("day before "):
            holiday_position = "before"
            holiday_name = hol_desc[len("day before "):]
        elif hol_desc.startswith("day after "):
            holiday_position = "after"
            holiday_name = hol_desc[len("day after "):]
        else:
            holiday_name = hol_desc

    weak_month = cur_month in _WEAK_MONTHS

    # ── Presidential cycle (Weinstein Ch.3, pp.69-70) ───────────────────
    year = date.year
    cyc = _cycle_year(year)
    cycle_phase_label, cycle_phase_note = _CYCLE_PHASES[cyc]
    president_party = _party_for_year(year)
    cycle_tbl = tables.get("cycle_monthly_positivity", {}).get(cyc, {})
    cycle_dow_tbl = tables.get("cycle_dow_positivity", {}).get(cyc, {})
    cycle_sample_years = tables.get("cycle_sample_years", {}).get(cyc, 0)
    cycle_book_note = (
        f"{year} is {cycle_phase_label} of the four-year presidential cycle "
        f"under a {president_party} administration. {cycle_phase_note}"
    )
    # This-month read under the current cycle year (the chart-changing stat)
    cycle_month_pct = cycle_tbl.get(cur_month)

    # Plain-language verdict line that the dossier can drop in directly
    parts: list[str] = []
    if cur_dow in _DOW_NAME:
        parts.append(
            f"Today is {dow_name}, historically {cur_dow_pct:.0%} of days "
            f"up (rank {cur_dow_rank} of 5)."
        )
    parts.append(
        f"Current month is {month_name}, {cur_month_pct:.0%} of years "
        f"positive since {tables['history_start_year']} "
        f"(rank {cur_month_rank} of 12)."
    )
    if weak_month and cur_month_pct < 0.55:
        parts.append(
            "This is one of the historically weaker months; treat fresh "
            "entries with a smaller starting size."
        )
    elif cur_month_pct >= 0.65:
        parts.append("This is one of the historically stronger months.")
    if holiday_adjacency:
        parts.append(
            f"Today sits {holiday_position} {holiday_name}, which adds a "
            f"small seasonal tailwind/tail risk per Hirsch's day-of-week "
            f"work."
        )
    # Presidential-cycle line (book-grounded)
    cycle_sentence = (
        f"Presidential cycle: {year} is {cycle_phase_label} under a "
        f"{president_party} administration"
    )
    if cycle_month_pct is not None and cycle_sample_years > 0:
        cycle_sentence += (
            f" — {month_name} has been positive {cycle_month_pct:.0%} of the "
            f"time in the {cycle_sample_years} comparable {cycle_phase_label.split(' (')[0].lower()} "
            f"years (vs {cur_month_pct:.0%} across all years)"
        )
    cycle_sentence += "."
    parts.append(cycle_sentence)
    verdict_line = " ".join(parts)

    return CalendarContext(
        date=iso,
        monthly_positivity=monthly,
        dow_positivity=dow,
        current_month=cur_month,
        current_month_pct=cur_month_pct,
        current_month_rank=cur_month_rank,
        current_month_label=cur_month_label,
        current_dow=cur_dow,
        current_dow_pct=cur_dow_pct,
        current_dow_rank=cur_dow_rank,
        current_dow_label=cur_dow_label,
        holiday_adjacency=holiday_adjacency,
        holiday_name=holiday_name,
        holiday_position=holiday_position,
        weak_month=weak_month,
        presidential_cycle_year=cyc,
        cycle_phase_label=cycle_phase_label,
        cycle_phase_note=cycle_phase_note,
        president_party=president_party,
        party_note=_PARTY_NOTE,
        cycle_monthly_positivity=cycle_tbl,
        cycle_dow_positivity=cycle_dow_tbl,
        cycle_sample_years=cycle_sample_years,
        cycle_book_note=cycle_book_note,
        verdict_line=verdict_line,
        history_start_year=tables["history_start_year"],
        history_end_year=tables["history_end_year"],
    )


def clear_calendar_cache() -> int:
    """Wipe the seasonal tables cache. Returns 1 if a file existed."""
    if _SEASONAL_CACHE.exists():
        _SEASONAL_CACHE.unlink()
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    ctx = get_calendar_context()
    print(f"Calendar context for {ctx.date}")
    print(f"  Lookback: {ctx.history_start_year} to {ctx.history_end_year}")
    print(f"  {ctx.current_dow_label}")
    print(f"  {ctx.current_month_label}")
    if ctx.holiday_adjacency:
        print(f"  Holiday adjacency: {ctx.holiday_position} {ctx.holiday_name}")
    print(f"\nMonthly positivity rates ranked:")
    for m, pct in sorted(ctx.monthly_positivity.items(), key=lambda x: -x[1]):
        print(f"  {_MONTH_NAME[m]:<10} {pct:.0%}")
    print(f"\nDay-of-week positivity rates ranked:")
    for d, pct in sorted(ctx.dow_positivity.items(), key=lambda x: -x[1]):
        print(f"  {_DOW_NAME[d]:<10} {pct:.0%}")
    print(f"\nVerdict line:\n  {ctx.verdict_line}")
