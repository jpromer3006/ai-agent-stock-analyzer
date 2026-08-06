# OROS Security & Robustness Review — 2026-08-06

Full-codebase audit: secrets, injection surfaces, AI-agent layer (prompt
injection, tool-use loop, LLM output handling), autonomy guardrails, and the
RPA/automation chain. Three parallel audit passes + manual spot verification
of every HIGH finding. 130 Python files, 660 tracked files reviewed.

**Verified clean (the foundation is solid):** no committed API keys or tokens
anywhere in the repo or its git history; `.env` never tracked; no `eval`,
`exec`, `pickle`, `yaml.load`, or `shell=True` anywhere; every subprocess
call uses argv-list form with timeouts; every HTTP call has a timeout; no
`verify=False`; no trading capability exists in the code (propose-and-journal
holds at code level); position sizing is pure arithmetic unreachable from any
LLM output; `claude_selector` validates model-picked tickers against the
offered candidate set; emails are plain text (no HTML injection surface);
watchlist names are sanitized; no fork-PR secret exposure (schedule +
dispatch triggers only).

---

## TIER 1 — Fix first (real exposure today)

### 1.1 Real brokerage PII is committed to the repo  [HIGH]
`data/journal/my_portfolio.json` (tracked) holds "Chase Self-Directed
(...8661)", margin debt, shares, and cost basis. The same account tail +
equity appear text-extractable in 5 tracked report PDFs (2026-07-07/24/27).
The workflow mirrors same-day PDFs to the PUBLIC `oros-reports` repo and
links them from YouTube descriptions; `OROS_PERSONAL_BOOK=1` would re-arm
the leak. Repo privacy is currently the ONLY control.
**Fix:** `git filter-repo` purge of `my_portfolio.json` + the 5 PDFs;
gitignore both paths; hard-gate the mirror step against personal-book PDFs.

### 1.2 Public Cloudflare tunnel, zero authentication  [HIGH]
`Launch.command:125` opens the full app to the internet; `demo_qr.py`
auto-shows the URL as a QR. Remote visitors reach every control, including
cron_control buttons that spawn processes and install launchd jobs on the
Mac. This finding is the amplifier for everything else in this list.
**Fix:** shared-secret gate in `ui/app.py` before rendering (or Cloudflare
Access); render cron_control only for local sessions.
Also: `/tmp/cloudflared` is executed if present (world-writable dir, no
checksum) — install to `~/.oros/bin` and verify SHA256.

### 1.3 Silent email failure — the exact failure the system was built to prevent  [HIGH]
`ml/daily_report.py:1194`: `ok = send_email(...)` — on SMTP failure it logs
a warning, exits 0, and no alert fires. A Gmail app-password expiry produces
a missing report that looks like a quiet day.
**Fix:** raise on `not ok` so the workflow failure alert fires.

### 1.4 Journal writes are unlocked and destroy history on corrupt read  [HIGH]
`ml/breadth.py _journal`, `picks_ledger`, `my_portfolio`: a truncated read
falls back to `{"entries": []}` and the next write erases all history,
silently. Four concurrent writers exist (app, launchd, watchdog, Actions).
Zero uses of `os.replace` or file locks in the repo.
**Fix:** temp-file + `os.replace` atomic writes; abort (never reset) on
`JSONDecodeError`; `fcntl.flock` sidecar lock for read-modify-write.

### 1.5 Prompt injection into the research agent, unfenced  [HIGH]
`agents/orchestrator.py:389` passes raw 10-K/news text into tool_result
blocks with no data/instruction fencing, and no system-prompt clause about
ignoring embedded instructions. Live evidence: an unverified Yahoo headline
became a cited claim in an emailed research memo (research_PLD_2026-07-29).
Worse: `search_10k` auto-indexes fetched text into ChromaDB, so injected
text POISONS every future query for that ticker (persistent injection).
The model also controls the `ticker` argument unvalidated.
**Fix:** wrap tool results in `<untrusted_source>` fencing + system-prompt
clause; validate ticker against the universe before dispatch; make RAG
indexing an explicit step, not a side effect of an LLM-chosen call.

### 1.6 Autonomous YouTube uploads default to PUBLIC  [HIGH]
`ml/publish_youtube.py:282` defaults `OROS_YT_PRIVACY` to "public" while the
docstring claims "unlisted by design." The unattended path sets nothing, so
every automated episode publishes publicly — including any whose script an
injected PDF shaped (the sign-off disclosure check covers only ONE scene).
**Fix:** default "unlisted"; explicit env flag for public; extend the
deterministic post-check (banned phrases: "guaranteed", "buy now", targets
without stops) to ALL rewritten scenes before render.

## TIER 2 — Correctness of the autonomy loop

### 2.1 Duplicate proposals feed tomorrow's decisions  [HIGH]
Proposals are keyed by minute, never deduped: 22 records on 6/29, 10 on
7/29, and 2026-08-05 shows cloud (13:50) + watchdog (14:20) both journaling.
`compare_by_mandate` aggregates ALL of them (including UI experiments) and
that scoreboard drives `best_mandate` → tomorrow's mandate, positions,
risk %, and the drawdown throttle that scales real position sizes. A day
that ran three times votes three times. This is the feedback loop unguarded.
**Fix:** idempotent per (date, mandate, source); exclude "UI build" notes
from the scoreboard (the report already does — the chooser must too).

### 2.2 Budget cap checked once a day; costs recorded as flat $0.10  [MED]
`budget_ok()` runs once at startup; the 26-round memo loop resends a
monotonically growing transcript with no per-call timeout (SDK default
10 min × 25 rounds ≈ 4 h hang ceiling) and `record_cost(0.10)` is fiction,
so the $60 cap can never bind.
**Fix:** `timeout=90` on every `messages.create`; re-check budget in-loop;
record real cost from `response.usage` tokens × price.

### 2.3 Prompt mandates a tool that does not exist  [MED]
`_shared.py:147` orders every specialist to call `get_weinstein_stage` and
emit a cited Weinstein section. The tool is registered nowhere — a
structured invitation to hallucinate stage numbers (currently the section
silently doesn't ship).
**Fix:** register a real tool wrapping `ml/stage_analyzer.py` (the code
already exists and is deterministic) — this is also a product win.

### 2.4 Advertised citation validator never runs  [MED]
Prompts threaten "memos under 70/80% citation coverage will be rejected";
`evaluation/citation_validator.py` is never imported on the live path.
**Fix:** run it in `_finalize_memo`, stamp measured coverage into the memo.

### 2.5 RiskAgent has no veto despite being described as having one  [MED]
Concentration flags are logged, never enforced; agent exceptions are
swallowed, so a crashed ConstructionAgent still journals a "legitimate"
record. Nothing validates a plan before journaling.
**Fix:** refuse to journal on empty holdings, trace errors, or hard-limit
breaches; alert instead.

### 2.6 Duplicate-send protection is partial and per-machine  [MED]
The report `.sent` marker is gitignored (cloud and Mac can't see each
other's); the advisor digest fires BEFORE the marker check; the evening
Closing Bell has no marker at all (second run = second public episode).
The idempotent-per-date pattern exists (breadth, picks, chain) — apply it.
**Fix:** committed journal state keyed (date, edition), checked before
digest, YouTube publish, and evening report.

### 2.7 Watchdog can stand down on a half-failed cloud run  [LOW]
It checks for a "journal <date>" commit, which the workflow makes with
`if: always()` — so a run that journaled then died before emailing still
satisfies the watchdog.
**Fix:** check for the day's report PDF in the committed journal instead.

## TIER 3 — Hardening (defense in depth)

- **Ticker path traversal** [MED]: 7 modules build cache paths from
  unvalidated tickers (`../../x` traverses; price_cache sanitizes, others
  forgot). Fix: one shared `safe_ticker()` with `re.fullmatch(r"[A-Z0-9.\-]{1,10}")`.
- **AppleScript injection** [MED]: `notifier.py:147` interpolates title
  unescaped into `osascript -e`. Fix: pass via argv (`on run argv`).
- **HTML injection in-app** [MED]: 4 `unsafe_allow_html` sites interpolate
  untrusted strings (ticker, narratives). Fix: `html.escape()` them.
- **SEC rate limit bypass** [MED]: `_load_ticker_cik_map` skips the
  limiter; the limiter itself is per-process only. Fix: route through
  `_sec_get`; flock-backed shared timestamp.
- **Workflow hygiene** [MED]: job-wide `contents: write` +
  `persist-credentials: true` while running LLM loops over untrusted data
  with range-pinned deps. Fix: `persist-credentials: false`, least-privilege
  permissions, step-level env instead of a plaintext `.env` on the runner.
- **Google OAuth client secret in `docs/`** [MED]: ignored but inside the
  shareable tree. Move to `~/.config/oros/`.
- **Unguarded `json.loads` on raw completion** [MED]: `oros_chat.py:100`
  (video script path). Fix: try/except + key whitelist + per-scene length
  cap (copy the defensive pattern from `claude_selector`).
- **pypdf unpinned, uploads unbounded** [LOW]: add `pypdf>=5.1` to
  requirements; 10 MB cap; check `%PDF-` magic bytes.
- **No retries / no resume** [LOW]: LLM, SMTP, YouTube upload (loads whole
  MP4 into memory) all single-shot; price-cache Actions key never hits.
- **Sentiment tilt from headlines** [MED]: VADER over Yahoo headlines moves
  ranking (±15%) and can hard-exclude names via red_flags — a headline farm
  influences the book. Fix: clamp the tilt, require ≥3 independent
  headlines, log the tilt into the journal for audit.
- **Personal emails hardcoded** [LOW]: 8 files. Move to env.
- **Absolute `/Users/jpromero` paths** [LOW]: 12 files. Derive from script dir.

---

## Suggested fix order

Sprint 1 (exposure): 1.1 PII purge → 1.2 tunnel auth → 1.3 email raise →
1.6 unlisted default. Sprint 2 (integrity): 1.4 atomic journals → 2.1
proposal dedupe → 2.6 send markers. Sprint 3 (agent hardening): 1.5
fencing + ticker validation → 2.2 budget/timeout → 2.3 real Weinstein tool
→ 2.4 validator. Sprint 4: Tier 3 sweep.

*Educational research system; propose-and-journal only; never trades.*
