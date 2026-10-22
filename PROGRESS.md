# Progress

Status values: Not started / In review / Approved. A feature is done only when its tests pass and the
user approves it. Milestones (M1 to M4) are push points: pre-push checks, then ask, then push only on "yes".

| # | Feature | Status | Date | Notes | Change rounds |
|---|---|---|---|---|---|
| F01 | Project skeleton and theme | Approved | 2026-10-22 | Approved by the user after 3 change rounds (mint/teal colours, card layout). Never screenshotted by the build agent: Streamlit can't be installed in the build workspace | 3 |
| F02 | Database and seed | Approved | 2026-10-22 | Approved by the user. 23 database tests pass in the build workspace; Setup tab rendering never seen by the build agent | 0 |
| F03 | Ranking engine | Approved | 2026-10-22 | Approved by the user. 29 ranking tests pass on the user's machine; 52 total in the build workspace; spec 16.1 fixture matches exactly | 0 |
| F04 | Validator and gates | Approved | 2026-10-22 | Approved by the user after running pytest locally with no failures. 108 validator + 47 gate tests; validator tests need pydantic (added to requirements.txt) | 0 |
| F05 | Industry score and strength labels | Approved | 2026-10-22 | Approved by the user after 1 change round (price bands follow the run's budget and quantity). 178 industry + 24 strength tests; no new packages | 1 |
| F06 | PDF extraction | Approved | 2026-10-22 | Approved by the user after 1 change round (bullets and leading spaces, so PyMuPDF and pypdf give identical text). 509 tests pass on the user's machine; PDFs are uploaded at runtime, never read from disk (DECISIONS 82); BM25 index is F14 | 1 |
| F07 | LLM settings sidebar | Approved | 2026-10-23 | Approved by the user after 1 change round (one test's fake provider now refuses a blank key like the real client). 139 client + 16 sidebar tests; 664 tests pass on the user's machine; provider calls never tried live by the build agent (no key in the build workspace); adds httpx and litellm | 1 |
| F08 | Evaluation agent (one supplier) | Approved | 2026-10-23 | Approved by the user after 1 change round. 73 tests; ran once on the user's machine with gpt-4o-mini (worked); gpt-5-nano timed out at 60 s, timeout left unchanged; round 2 added two prompt sentences (DECISIONS 111); requirements text uses the run's budget and quantity (DECISIONS 69) | 1 |
| F09 | Full run (orchestrator) | Approved | 2026-10-23 | Approved by the user after 2 change rounds. Orchestrator, New evaluation tab (upload, forms, checks, live step log, ranked results table), run saved to the database; budget and quantity stored with the run (DECISIONS 69); PDFs only from the upload box (DECISIONS 82). Round 2: date read from the PDF, experience rating optional. Round 3: date filled in for any PDF, sidebar lock fixed, F08 prompt file delivered. 165 new tests; 902 expected on the user's machine (the user ran 846 before round 3: 3 failures, all fixed); the tab was never run in real Streamlit by the build agent | 2 |
| F10 | Overview tab | Approved | 2026-10-23 | Approved by the user after 2 change rounds (round 3: the runner-up shown as a readable tinted strip). Recommendation card (runner-up, industry warning), leaderboard with 3 scores and eligibility badges (hover shows the failed check), tie-break note, strength table; recommendation rule in tools/insights.py; runs read back from the database. 58 new tests; the tab was never run in real Streamlit by the build agent. See DECISIONS 137 to 149 | 2 |
| F11 | Scorecards tab | Not started | | | |
| F12 | Audit: run details and JSON | Not started | | | |
| M1 | Milestone: first push and first deploy | Not started | | | |
| F13 | Human review: rate and re-evaluate | Not started | | | |
| F14 | Ask the RFP | Not started | | | |
| M2 | Milestone: push | Not started | | | |
| F15 | Evidence checker | Not started | | | |
| F16 | Pros and cons | Not started | | | |
| F17 | Price vs budget bar | Not started | | Use the run's budget, not a constant (DECISIONS 69) | |
| F18 | Analysis tab | Not started | | Scatter budget line uses the run's budget (DECISIONS 69) | |
| F19 | "Not happy?" flow | Not started | | | |
| F20 | Negotiation suggestions | Not started | | Budget points use the run's budget (DECISIONS 69) | |
| F21 | Audit: history and trail | Not started | | | |
| M3 | Milestone: push | Not started | | | |
| F22 | README and sample run | Not started | | | |
| F23 | Deployment readiness | Not started | | | |
| M4 | Milestone: final push | Not started | | | |
