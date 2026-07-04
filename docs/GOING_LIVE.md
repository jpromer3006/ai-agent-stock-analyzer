# OROS Going Live — unattended daily runs, reports, and email

OROS now runs, scans, builds its own portfolio, and emails a daily gains/losses
report to **JPR20@fordham.edu** without a human in the loop. Two runners exist:

| Runner | Fires when | Needs the Mac on? |
|---|---|---|
| GitHub Actions (`.github/workflows/oros_daily.yml`) | 9:50 ET weekdays (cloud) | No |
| launchd `com.oros.morningrun` | 9:35 ET weekdays (local) | Yes |

Guardrail unchanged: OROS **proposes and journals only — it never trades**.
All output is educational, not financial advice.

## One-time setup (about 10 minutes)

### 1. Create a Gmail App Password (email was silently broken until now)

`ml/notifier.py` sends via Gmail SMTP, but `.env` never had credentials, so
every alert was a silent no-op. Fix:

1. Go to https://myaccount.google.com/apppasswords (requires 2-Step Verification
   on the Google account).
2. Create an app password named `OROS`.
3. Add to `.env` in the project root (never commit `.env`):

```
GMAIL_ADDRESS=juanromero4767@gmail.com
GMAIL_APP_PASSWORD=<16-character app password>
OROS_REPORT_TO=JPR20@fordham.edu
```

### 2. Make the GitHub repo private and add secrets

The remote is `github.com/jpromer3006/ai-agent-stock-analyzer`. The workflow
commits the journal back, so the repo **must stay private**.

In GitHub: Settings → Secrets and variables → Actions → New repository secret:

```
GMAIL_ADDRESS         juanromero4767@gmail.com
GMAIL_APP_PASSWORD    <the app password from step 1>
OROS_REPORT_TO        JPR20@fordham.edu
FRED_API_KEY          <from .env, optional>
ANTHROPIC_API_KEY     <optional; unattended runs are token-free>
ALPHA_VANTAGE_API_KEY <optional>
FMP_API_KEY           <optional>
```

### 3. Push and test

```bash
cd "/Users/jpromero/Ai-Agent Oros"
git add .github ml/daily_report.py ml/auto_config.py ml/notifier.py \
        scripts/oros_run.py ui/components/portfolio_mode.py docs/GOING_LIVE.md
git commit -m "Go live: unattended daily run, auto portfolio, emailed PDF report"
git push
```

Then in GitHub → Actions → "OROS daily run" → **Run workflow** to test
immediately. Expect two emails: "OROS: scan complete ..." and
"OROS Daily Report ..." with the PDF attached.

### 4. Avoid duplicate emails from the local job

The cloud and local runners don't share state, so both would email. Either
disable the local job:

```bash
launchctl unload ~/Library/LaunchAgents/com.oros.morningrun.plist
```

or keep it as a fallback and accept an occasional duplicate on days the Mac
is awake. (Recommended: disable it; re-enable any time with `launchctl load`.)

## What runs each weekday

`scripts/oros_run.py --mandate auto` now:

1. Refreshes prices and ranks the liquid universe (cloud cache restores the
   prior day's price cache, so the refresh is incremental).
2. **Chooses its own settings** (`ml/auto_config.py`): mandate from the
   paper-trade scoreboard once ≥3 proposals are scored, else the regime
   default; risk/positions/liquidity scale with the regime (bear = fewer
   names, tighter risk — Weinstein capital preservation).
3. Builds and journals the portfolio (never trades).
4. Emails **"scan complete"** — confirmation the scan took place, every day,
   even when nothing changed.
5. Emails the **OROS Daily Report** PDF (`ml/daily_report.py`):
   market regime + index snapshot vs the ~30-week MA, per-holding paper
   gains/losses with stop breaches flagged, portfolio changes, IPO scan
   summary, the forward scoreboard, and the growth-of-$10k comparison
   against SPY / QQQ / VTI / VTSAX / FXAIX.
6. Commits the journal back to the repo so the forward track record accrues.

## In the app

The Portfolio tab settings are now hidden in a sidebar expander
("Portfolio settings (manual — click to open)"). The primary action is
**🤖 Auto-build — OROS picks the settings**: same auto logic as the cloud run,
with the benchmark comparison chart rendered under each built book.

## Troubleshooting

- No email? Run `PYTHONPATH=. python3 ml/daily_report.py --force` locally and
  watch the log — "email NOT sent (GMAIL_ADDRESS / GMAIL_APP_PASSWORD missing)"
  means step 1 wasn't completed in that environment.
- Report PDF is also uploaded as a workflow artifact (Actions → run → Artifacts)
  and saved under `data/journal/reports/`.
- DST: the cron is 13:50 UTC (9:50 ET in summer, 8:50 ET in winter). Adjust in
  `oros_daily.yml` if the winter time matters.
