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
| F06 | PDF extraction | Not started | | | |
| F07 | LLM settings sidebar | Not started | | | |
| F08 | Evaluation agent (one supplier) | Not started | | Evaluator requirements text uses the run's budget and quantity (DECISIONS 69) | |
| F09 | Full run (orchestrator) | Not started | | Add Budget (Rs) and Quantity inputs, pre-filled Rs 60,00,000 and 100, stored with the run (DECISIONS 69) | |
| F10 | Overview tab | Not started | | | |
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
