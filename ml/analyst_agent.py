"""
analyst_agent.py - An independent Claude analyst as a second opinion on the books.

The strategy registry is the quant decision; this agent is a skeptical human-style
cross-check. For each finalist it asks Claude (Haiku, cheap) whether it AGREES the
name is a reasonable buy right now and to flag risks a price-based model can miss
(valuation, known fundamental weakness, sector crowding, hype). It is a live/forward
signal only - it cannot be backtested, because the model may already know what
happened after a historical date.

Cost: finalist-only, a few cents per run on Haiku. Guard with the budget cap and run
it on demand (the UI button), not on every unattended cycle.

    PYTHONPATH=. python3 ml/analyst_agent.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AnalystVerdict:
    ticker: str
    stance: str = "neutral"          # agree | neutral | disagree
    confidence: float = 0.0
    rationale: str = ""
    flags: list = field(default_factory=list)


def _candidate_keys() -> list[str]:
    """All plausible keys, Keychain first then .env, so a stale Keychain key falls
    through to the rotated .env key automatically."""
    keys = []
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", "ANTHROPIC_API_KEY", "-w"],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            keys.append(r.stdout.strip())
    except Exception:
        pass
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except Exception:
        pass
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env:
        keys.append(env)
    return list(dict.fromkeys(keys))


_PROMPT = """You are an independent equity analyst giving a deliberately skeptical
second opinion on a quantitative model's stock pick. You are NOT given the future.
Using the model's signals plus your own knowledge, judge whether you AGREE this is a
reasonable buy candidate right now, and flag risks a price-trend model can miss
(stretched valuation, known fundamental weakness, sector crowding, hype/froth,
binary catalysts). Be concise and honest; disagreeing is fine and useful.

Stock: {ticker}  Sector: {sector}
Model conviction: {conviction} (led by strategy: {led_by})
News sentiment: {sentiment}   Flags: {flags}
Proposed entry {entry}, stop {stop}

Respond with ONLY a JSON object:
{{"stance":"agree|neutral|disagree","confidence":0.0-1.0,"rationale":"1-2 sentences","flags":["short risk", "..."]}}"""


def analyze_holding(h, model: str = "claude-haiku-4-5") -> AnalystVerdict:
    v = AnalystVerdict(ticker=h.ticker)
    try:
        import anthropic
    except Exception:
        v.rationale = "analyst unavailable (anthropic SDK not installed)"
        return v
    keys = _candidate_keys()
    if not keys:
        v.rationale = "analyst unavailable (no API key found)"
        return v
    led = max(h.strategy_scores, key=h.strategy_scores.get) if h.strategy_scores else "n/a"
    flags = ((["earnings"] if getattr(h, "earnings_risk", False) else [])
             + (["froth"] if getattr(h, "froth", False) else [])) or ["none"]
    prompt = _PROMPT.format(ticker=h.ticker, sector=h.sector, conviction=round(h.composite, 2),
                            led_by=led, sentiment=round(getattr(h, "sentiment", 0.0), 2),
                            flags=", ".join(flags), entry=h.entry, stop=h.stop)
    last_err = ""
    for key in keys:                                    # try Keychain, then .env
        try:
            r = anthropic.Anthropic(api_key=key).messages.create(
                model=model, max_tokens=300, messages=[{"role": "user", "content": prompt}])
            text = r.content[0].text
            m = re.search(r"\{.*\}", text, re.S)
            d = json.loads(m.group(0)) if m else {}
            v.stance = str(d.get("stance", "neutral")).lower()
            v.confidence = float(d.get("confidence", 0) or 0)
            v.rationale = str(d.get("rationale", "")).strip()
            v.flags = list(d.get("flags", []) or [])
            return v
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            if "authentication" in last_err.lower() or "401" in last_err:
                continue                                # stale key -> try the next one
            v.rationale = f"analyst error: {last_err}"
            return v
    v.rationale = "analyst error: all keys rejected (update the Keychain key)"
    return v


def run_analyst(plan, max_holdings: int = 15) -> list[AnalystVerdict]:
    """Independent verdict on each finalist. Returns a list of AnalystVerdict."""
    return [analyze_holding(h) for h in plan.holdings[:max_holdings]]


class AnalystAgent:
    """Optional orchestrator agent; only runs when config.use_analyst is True."""
    name, role = "AnalystAgent", "independent second opinion"

    def run(self, ctx):
        if not getattr(ctx.config, "use_analyst", False) or ctx.plan is None:
            ctx.log(self.name, self.role, "skipped (run on demand to control token cost)")
            return
        verdicts = run_analyst(ctx.plan)
        ctx.analyst = verdicts
        dis = [v.ticker for v in verdicts if v.stance == "disagree"]
        msg = f"{len(verdicts)} reviewed" + (f"; disagrees on {', '.join(dis)}" if dis else "; no disagreements")
        ctx.log(self.name, self.role, msg)


def _demo() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    from dataclasses import dataclass as _dc

    @_dc
    class _H:
        ticker: str; sector: str; composite: float; strategy_scores: dict
        sentiment: float; earnings_risk: bool; froth: bool; entry: float; stop: float
    h = _H("NTRS", "Financials", 0.79, {"sepa": 1.0, "weinstein": 0.6}, 0.35, False, True, 172.11, 164.68)
    print("Testing analyst on one holding (NTRS, has froth flag) ...")
    v = analyze_holding(h)
    print(f"  stance={v.stance} conf={v.confidence} flags={v.flags}\n  {v.rationale}")


if __name__ == "__main__":
    _demo()
