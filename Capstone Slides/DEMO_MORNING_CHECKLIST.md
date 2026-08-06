# Demo Morning Checklist — 2026-08-07
### Everything verified tonight. Five steps tomorrow, ~5 minutes.

## System status (verified while you slept)
- All commits pushed: local == origin at 4838605. Nothing pending.
- Full codebase compiles clean (ui, ml, scripts, agents, data).
- Breadth panel: 08/06, 494 graded, source scan-now. One grader, consistent.
- Race: day 24, open-ended, "The Race — to <today>". Titles roll daily.
- Cloud workflow on origin has the cap config, mirror PII gate, and provenance.
- Watchdog: cap 500, stands down offline. The 787 ghost cannot return.

## Before recording (in order)

1. **When you wake, run `oros-push` once** — it ships the new PRE-MARKET
   schedule (morning brief now fires 8:00 AM ET, in your inbox before the
   bell from tomorrow on). Then, **after today's cloud run lands (~9:40 AM
   this one last time), run `oros-push` again** to pull today's cloud book —
   every holding then resolves in Stage Detail.

2. **Double-click Launch.command** (the regular one — local only, no
   tunnel needed for a screen recording).

3. **Optional:** press Scan Now once and let it finish BEFORE recording.
   The panel and the cards will then match exactly, same-timestamped.

4. **Record** with `OROS_Demo_Script.md` (same folder): Cmd+Shift+5,
   microphone ON in Options, Do Not Disturb ON. One tab per take is fine.

5. If you demo a Custom Build and want Stage Detail on a holding the
   dropdown can't find: type it into the sidebar "Also check tickers
   (ad hoc)" box — it gets graded on the spot and becomes selectable.
   (Custom builds may legitimately reach beyond the 500 via the Open
   Market top-up pool; the autonomous book never does.)

## Still outstanding (NOT demo blockers)

- **PUBLIC repo purge — do this soon.** Your Chase account tail is still
  visible in two PDFs on github.com/jpromer3006/oros-reports. Paste in
  Terminal:
  ```
  cd /tmp && rm -rf oros-reports && git clone https://github.com/jpromer3006/oros-reports && cd oros-reports
  git rm -q oros_report_2026-07-24.pdf oros_report_2026-07-27.pdf
  git checkout --orphan clean && git add -A && git commit -m "reports (history reset)"
  git branch -M clean main && git push --force origin main
  ```
- Demo with audience phones: use "Launch Demo (Public Link).command" instead
  of the regular launcher.

*Sleep well. The desk is green.*
