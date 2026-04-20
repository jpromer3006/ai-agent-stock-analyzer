# Team Deliverables Structure

A recommended Google Drive (or shared OneDrive/Dropbox) folder layout
for the team to collaborate on the report and presentation without
stepping on each other's work.

---

## 📁 Recommended folder structure

```
📁 Applied Finance Project — Ai-Agent Stock Analyzer/
│
├── 📁 00_README/
│   └── 📄 Kickoff_Notes.md         ← what we agreed on at the team meeting
│
├── 📁 01_Raw_Material/              ← everything the team pulls FROM
│   ├── 📁 Screenshots/              ← JP uploads per SCREENSHOTS_GUIDE.md
│   │   ├── 01_landing_page.png
│   │   ├── 02_research_ticker_input.png
│   │   ├── 03_agents_working.png
│   │   ├── 04_memo_with_citations.png
│   │   ├── 05_audio_button.png
│   │   ├── 06_chat_panel.png
│   │   ├── 07_add_to_top_picks.png
│   │   ├── 08_market_regime_banner.png
│   │   ├── 09_stage_distribution.png
│   │   ├── 10_leaderboards.png
│   │   ├── 11_universe_scan_table.png
│   │   ├── 12_detail_chart.png
│   │   ├── 13_trade_setup_card.png
│   │   ├── 14_eval_results.png
│   │   ├── 15_architecture_diagram.png
│   │   └── 16_github_repo.png
│   │
│   ├── 📁 Reference_Docs/           ← source-of-truth reading material
│   │   ├── 📄 README.md             (copy of repo README — primary source)
│   │   ├── 📄 CONTRIBUTORS.md       (copy of repo file)
│   │   ├── 📄 SCREENSHOTS_GUIDE.md  (this file's sibling)
│   │   └── 📄 Lecture_9_Rubric.pdf  (Andy Li's Applied Finance Projects brief)
│   │
│   ├── 📁 Evaluation_Data/          ← concrete numbers to cite
│   │   └── 📄 retrieval_results.json  (from evaluation/results/)
│   │
│   └── 📁 Demo_Recording/           ← optional
│       └── 📹 30sec_walkthrough.mov  (JP records if time permits)
│
├── 📁 02_Report/                    ← Report Lead owns this folder
│   ├── 📄 Report_DRAFT.docx         ← working draft
│   ├── 📄 Report_FINAL.docx         ← handed to JP for review, then to Prof. Li
│   └── 📄 Report_FINAL.pdf          ← exported on submission day
│
├── 📁 03_Presentation/              ← Presentation Lead owns this folder
│   ├── 📄 Slides_DRAFT.pptx         ← working draft
│   ├── 📄 Slides_FINAL.pptx         ← handed to JP for review
│   ├── 📄 Slides_FINAL.pdf          ← pdf export on presentation day
│   └── 📄 Speaker_Notes.md          ← who says what, slide by slide
│
├── 📁 04_Final_Submission/          ← what gets sent to Prof. Li
│   ├── 📄 Team_Project_Writeup.pdf  (final report)
│   ├── 📄 Team_Project_Deck.pdf     (final slides)
│   ├── 📄 GitHub_Link.txt           (link to repo)
│   ├── 📄 Live_Demo_Instructions.txt (URL + "active while JP's Mac is on")
│   └── 📄 Contributions_Statement.md (one-paragraph honest division of labor)
│
└── 📁 99_Archive/                   ← old drafts, superseded files, screenshots
    └── (whatever)
```

---

## 👥 Role-based access

- **JP (Project Lead)** — full edit access to everything; primary owner of `01_Raw_Material` and `04_Final_Submission`; review/approve `02_Report` and `03_Presentation` before submission
- **Report Lead (Teammate 1)** — primary edit rights on `02_Report/`; read-only on everything else
- **Presentation Lead (Teammate 2)** — primary edit rights on `03_Presentation/`; read-only on everything else

All three have view access to every folder so nobody is blocked
waiting for shares.

---

## 📝 Kickoff_Notes.md — template for tomorrow's meeting

Create this doc first. Paste this template, then fill in with what's
actually agreed at the meeting:

```markdown
# Team Kickoff — Applied Finance Project
Date: [tomorrow's date]
Attendees: JP Romero, [Teammate 1], [Teammate 2]

## Division of labor
- **Engineering (built before kickoff):** JP Romero — all software,
  repository, live demo, deployment
- **Written Report:** [Teammate 1]
- **Presentation Slides:** [Teammate 2]
- **Live demo during presentation:** JP
- **Technical Q&A:** JP

## Timeline
- [Date+1] — report outline done, deck skeleton done
- [Date+3] — first full drafts circulated for review
- [Date+5] — final drafts reviewed by JP
- [Submission date] — PDFs delivered to 04_Final_Submission/

## Commitments
- Weekly sync: [day + time]
- Slack/text group: [link or phone numbers]
- JP will keep live demo running whenever possible; if tunnel dies, JP
  restarts it and messages group with new URL

## Acknowledgement
All team members understand that:
- This project is released MIT-licensed on GitHub
- JP is the sole code author; commercial rights belong to AdminAI LLC
- Teammates fully own their report + slide contributions and can
  showcase them on resume/LinkedIn
- This split is disclosed to Prof. Li in the repo's CONTRIBUTORS.md
```

---

## ✉️ What to email the Report Lead

```
Subject: Applied Finance Project — report materials

Hey [name],

Here's everything you need to draft our Applied Finance Project report.
Target: 2–3 pages, 6 sections (Lecture 9 rubric).

Start here: READ the README from our GitHub repo. It's literally
structured as the 6 sections you need to write, so your job is to
turn bullet points into polished prose.

  README:       [link to README.md on GitHub]
  GitHub repo:  https://github.com/jpromer3006/ai-agent-stock-analyzer
  Live demo:    https://cells-gate-refused-stream.trycloudflare.com

All the concrete numbers you'll need are in evaluation/results/.
Screenshots for the report are in our Google Drive under
01_Raw_Material/Screenshots/.

Suggested structure (from Lecture 9 rubric):
  1. Business Problem (1/2 page) — the "4–8 hours per ticker" story
  2. Architecture (1/2 page) — use the diagram from 01_Raw_Material/Screenshots/15_architecture_diagram.png
  3. Dataset (1/4 page) — 50-ticker universe, SEC EDGAR stats
  4. Evaluation (1/2–1 page) — the 34-question golden set, sector results,
     honest limitations (Healthcare 0/4)
  5. Limitations (1/4 page) — pick top 4 from README section 5
  6. Next Steps (1/4 page) — top 3 from README section 6

Target: full draft by [date]. I'll do a review pass before we submit.

— JP
```

---

## ✉️ What to email the Presentation Lead

```
Subject: Applied Finance Project — deck materials

Hey [name],

Here's everything you need to build our Applied Finance Project deck.
Target: ~10 slides, ~10 minute talk.

Start here: see docs/SCREENSHOTS_GUIDE.md — it has the exact slide
order I'd recommend, with the right screenshot for each slide and
suggested captions.

  GitHub repo:  https://github.com/jpromer3006/ai-agent-stock-analyzer
  Live demo:    https://cells-gate-refused-stream.trycloudflare.com
  Screenshots:  [Google Drive link]/01_Raw_Material/Screenshots/

Suggested slide flow:
  1. Title + team names
  2. Business problem (why this matters)
  3. Solution overview
  4. Architecture diagram
  5-6. Research demo (memo + audio + chat)
  7-8. Trader demo (regime banner + leaderboard + detail chart)
  9. Evaluation results — our honest numbers
  10. Limitations + Next Steps
  11. Thank you / Q&A

Target: skeleton by [date], full draft by [date+2]. I'll review
before we submit. I'll handle the live demo during the actual
presentation, so you focus on making the visual story stick.

— JP
```

---

## ✅ Final submission checklist

Before submitting to Prof. Li:

- [ ] Report is a PDF, not a Word doc
- [ ] Deck is a PDF AND a PPTX
- [ ] Both files have team names in header/footer
- [ ] GitHub repo URL is in both deliverables
- [ ] Live demo URL is documented (with "active while JP's Mac is on" note)
- [ ] CONTRIBUTORS.md is accurate with real teammate names
- [ ] CONTRIBUTORS.md has contribution paragraph in 04_Final_Submission/
- [ ] All three team members have seen + approved final versions

---

*Last updated: April 2026*
