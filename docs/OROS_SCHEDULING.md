# OROS Phase 6 - Unattended Scheduling

The runner `scripts/oros_run.py` is the single entry point for unattended operation.
It refreshes data, ranks the universe by liquidity, runs the multi-agent orchestrator,
and journals the proposed portfolio. It never places orders or moves money
(propose-and-journal-only), and a deferring budget cap protects the API balance.

## Manual run

```bash
cd "/Users/jpromero/Ai-Agent Oros"
PYTHONPATH=. python3 scripts/oros_run.py --refresh-cap 1500 --cap 800 --positions 12
# quick run using the warm cache (no bulk refresh):
PYTHONPATH=. python3 scripts/oros_run.py --light --cap 800
```

Output: an OROS BRIEF (regime, agent trace, holdings, stops) and a dated proposal
in `data/journal/`. Past proposals get their realized returns filled in automatically.

## Wrapper script

Create `scripts/run_oros.command` (chmod +x):

```bash
#!/bin/bash
cd "/Users/jpromero/Ai-Agent Oros"
export PATH="/usr/local/bin:/opt/homebrew/bin:$PATH"
PYTHONPATH=. /usr/bin/python3 scripts/oros_run.py --refresh-cap 1500 --cap 800 \
  >> data/journal/cron.log 2>&1
```

## Option A - cron (simple)

`crontab -e`, then (weekdays):

```
# Nightly full refresh + portfolio, 6:15pm ET (adjust to your timezone)
15 18 * * 1-5  /Users/jpromero/Ai-Agent\ Oros/scripts/run_oros.command
# Optional midday light run off the warm cache, 12:30pm ET
30 12 * * 1-5  cd "/Users/jpromero/Ai-Agent Oros" && PYTHONPATH=. /usr/bin/python3 scripts/oros_run.py --light --cap 800 >> data/journal/cron.log 2>&1
```

## Option B - launchd (recommended on macOS, survives sleep)

`~/Library/LaunchAgents/com.oros.nightly.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.oros.nightly</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/jpromero/Ai-Agent Oros/scripts/run_oros.command</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>/Users/jpromero/Ai-Agent Oros/data/journal/launchd.log</string>
  <key>StandardErrorPath</key><string>/Users/jpromero/Ai-Agent Oros/data/journal/launchd.err</string>
</dict></plist>
```

Load it: `launchctl load ~/Library/LaunchAgents/com.oros.nightly.plist`

## Guardrails (enforced by the runner)

- Propose-and-journal-only: no orders, no transfers. Ever.
- Deferring budget cap (`--monthly-cap`, default $60): LLM narrative work is skipped
  when the cap is hit; the quant engine is token-free, so core runs cost ~$0.
- Every run is journaled with its agent trace for audit and the forward track record.

## Before trusting it with anything real

Per the pre-Phase-6 checklist in the main plan: run it in paper-trade mode for a
while, let `data/journal/` accumulate proposals and outcomes, and review the realized
hit rate and returns out of sample before any real capital. This is educational
tooling, not financial advice.
