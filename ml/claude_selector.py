"""
claude_selector.py - LLM-first candidate selection.

"Claude picks first": instead of the quant engine choosing names and Claude
auditing them, Claude selects the names from the candidate universe and assigns
each to a mandate. The quant engine then sizes, gates, and risk-stops those
picks and places them into the matching book.

Design guards:
  - Claude only sees a compact, liquidity-ranked slice of the universe (the
    first ``universe_cap`` symbols), so the prompt stays small and cheap.
  - Claude may only return tickers that were in the candidate list; anything
    else is dropped.
  - On any failure (no API key, bad JSON) the caller falls back to the normal
    quant flow, so the build never hard-fails.

    PYTHONPATH=. python3 ml/claude_selector.py
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Plain-language description of each mandate so Claude assigns sensibly.
_MANDATE_HELP = {
    "fast_momentum": "very short term, a day to a few days, strongest relative strength",
    "swing_momentum": "days to weeks, momentum coming out of a clean base",
    "growth_hold": "medium to long term, durable revenue and earnings growth",
    "value_hold": "longer horizon, undervalued quality, margin of safety",
}


def select_and_assign(
    symbols: list[str],
    mandates: list[str],
    max_per_mandate: int = 15,
    universe_cap: int = 200,
    model: str = "claude-haiku-4-5",
) -> tuple[dict[str, list[str]], str]:
    """Ask Claude to pick names from ``symbols`` for each mandate.

    Returns (picks_by_mandate, error). ``error`` is "" on success. Picks are
    validated against the candidate set and de-duplicated. On any failure the
    picks dict is empty and ``error`` explains why, so the caller can fall back
    to the quant flow.
    """
    if not symbols or not mandates:
        return {}, "no candidates or no mandates"

    try:
        import anthropic
    except Exception:
        return {}, "anthropic SDK not installed"

    # Reuse the app's key discovery (Keychain first, then .env).
    try:
        from ml.analyst_agent import _candidate_keys
        keys = _candidate_keys()
    except Exception:
        keys = []
    if not keys:
        return {}, "no Anthropic API key found"

    # Compact, liquidity-ordered slice keeps the prompt small and the cost low.
    candidate = list(dict.fromkeys(s.upper() for s in symbols))[:universe_cap]
    candidate_set = set(candidate)

    wanted = [m for m in mandates if m in _MANDATE_HELP] or list(mandates)
    mandate_lines = "\n".join(f"- {m}: {_MANDATE_HELP.get(m, m)}" for m in wanted)

    prompt = (
        "You are selecting US stock tickers for one or more portfolio mandates.\n"
        "From ONLY the candidate tickers listed below, choose the names you judge "
        "most promising right now for each mandate, using your own knowledge of the "
        "companies. Do NOT invent tickers that are not in the list.\n\n"
        "Mandates and what each wants:\n"
        f"{mandate_lines}\n\n"
        f"Pick up to {max_per_mandate} tickers per mandate (fewer is fine).\n"
        "Make the mandate lists DISTINCT: spread your choices across different "
        "sectors and company sizes, and avoid putting the same ticker in more than "
        "one mandate unless it genuinely fits both. Do not just repeat the largest "
        "mega caps in every mandate; use the full breadth of the candidate list.\n\n"
        f"Candidate tickers ({len(candidate)}):\n{', '.join(candidate)}\n\n"
        "Respond with ONLY a JSON object whose keys are the requested mandate names "
        "and whose values are arrays of tickers, for example "
        '{"swing_momentum":["AAPL","MSFT"]}. Use only these mandate keys: '
        f"{', '.join(wanted)}."
    )

    last_err = ""
    for key in keys:
        try:
            r = anthropic.Anthropic(api_key=key).messages.create(
                model=model, max_tokens=900,
                messages=[{"role": "user", "content": prompt}],
            )
            text = r.content[0].text
            m = re.search(r"\{.*\}", text, re.S)
            data = json.loads(m.group(0)) if m else {}
            picks: dict[str, list[str]] = {}
            for mand in wanted:
                raw = data.get(mand, []) or []
                clean = [t.upper() for t in raw if isinstance(t, str)]
                # Keep only tickers that were actually offered, de-duplicated.
                clean = list(dict.fromkeys(t for t in clean if t in candidate_set))
                picks[mand] = clean[:max_per_mandate]
            if not any(picks.values()):
                return {}, "Claude returned no usable picks"
            return picks, ""
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            continue
    return {}, f"Claude selection failed: {last_err}"


if __name__ == "__main__":
    demo = ["AAPL", "MSFT", "NVDA", "AMD", "INTC", "JPM", "BAC", "XOM",
            "CVX", "KO", "PG", "TSLA", "META", "GOOGL", "AMZN", "NFLX"]
    out, err = select_and_assign(demo, ["fast_momentum", "value_hold"], max_per_mandate=5)
    print("error:", err or "none")
    for k, v in out.items():
        print(f"{k}: {v}")
