# Agentic RFP Evaluation and Supplier Ranking

A Streamlit app that helps a startup choose a laptop supplier. It reads supplier proposals (PDFs), asks an LLM to score each one
against configurable criteria **with quoted evidence**, and then uses plain, tested Python to check must-haves, rank the suppliers
and write the recommendation. A reviewer can question the result, change the priorities (up to three rounds), ask the proposals
questions, and download an audit file or a PDF report.

**The one rule behind everything:** the AI reads and words; Python decides. The AI never picks a supplier, never computes a rank and
never has its key written to any file. A quote the AI gives that cannot be found in the proposal is flagged on the scorecard and listed for the reviewer.

## Contents

1. [Run locally](#run-locally-windows-powershell) · 2. [The 8 tabs](#the-8-tabs) · 3. [Agents and tools](#agents-and-tools) ·
4. [Golden rules](#golden-rules) · 5. [Formulas](#formulas) · 6. [Edge cases](#edge-case-rules) · 7. [Assumptions](#assumptions) ·
8. [Extensions and known differences](#extensions-and-known-differences-from-the-build-prompt) · 9. [Tests](#tests) ·
10. [Deployment](#deployment-streamlit-community-cloud) · 11. [Sample run](#sample-run) · 12. [Future scope](#future-scope)

## Run locally (Windows, PowerShell)

```powershell
cd "C:\Users\sriha\OneDrive\Desktop\vibe coding projects\RPF Evaluator\rfp_evaluator"
python -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
pytest
streamlit run app.py                # opens http://localhost:8501
```

The API key is typed in the sidebar (or placed by you in `.streamlit/secrets.toml`, e.g. `OPENAI_API_KEY = "..."`). It lives only in
the session and is never written to the database, the logs, the audit trail or any export. All paths use `pathlib`, so the app also
runs on Linux. If OneDrive locks a file, wait a few seconds and retry.

## The 8 tabs

| Tab | What it is for |
|---|---|
| Criteria | The criteria, weights (must total 100), must-have gates and industry baselines, read from SQLite. Deactivating a criterion changes the prompt. |
| New evaluation | Upload 2–10 PDFs, name them, set the budget and quantity, optionally tick or write your priorities, choose the model and run. A step log shows progress. |
| Overview | The recommendation, leaderboard with tie-break notes, strength table, price against budget, and (when you asked for something) the pick for your priorities beside the best fit. |
| Scorecards | One supplier at a time: marks, evidence quotes, failed gates, missing items and warnings. |
| Analysis | The what-if chart: where the winner would change if one area counted more. |
| Re-evaluation | Rate the recommendation 1–5; below 4 you say what is wrong and the suppliers are ranked again (3 rounds). After the last round: the final recommendation and negotiation points. A floating "Report" button at the bottom right of every tab opens the PDF download and a mail link. |
| Ask the RFP | Questions answered only from the proposals; otherwise "Not stated in the proposal." |
| Audit | Run history, the change trail (with a score-changes filter), PDF fingerprints (SHA-256) and the JSON download. |

Exactly six charts are drawn, all in the mint/teal theme (`.streamlit/config.toml`, `ui/theme.py`).

## Agents and tools

| Piece | File | Uses the LLM? | Job |
|---|---|---|---|
| Evaluation agent | `agents/evaluator.py` | yes | Reads one proposal (inside `<proposal>` tags, treated as untrusted data) and returns JSON: facts, scores, justifications, quotes. |
| Summariser | `agents/summarizer.py` | yes (optional) | Words the pros, cons and the recommendation reason Python selected. |
| Q&A agent | `agents/qa_agent.py` | yes | Answers from retrieved passages only. |
| Feedback interpreter | `agents/feedback.py` | yes | Reads "what is wrong" in the reviewer's words into a safe, structured request that Python cleans and the reviewer confirms. |
| Negotiator | `agents/negotiator.py` | yes (optional) | Words up to five negotiation points and a fit note for the one supplier Python chose. |
| Orchestrator | `agents/orchestrator.py` | no | The pipeline: validate, check evidence, gates, rank, save, re-rank, audit. |
| Validator, evidence checker | `tools/validator.py`, `tools/evidence_checker.py` | no | Keep scores in range; check that each quote is really in the proposal (`rapidfuzz` when installed) and flag the ones that are not. |
| Ranking, gates, industry, insights | `tools/ranking.py`, `gates.py`, `industry.py`, `insights.py` | **never** | Every number and every order. |
| Reweighting, leaning | `tools/reweight.py`, `tools/leaning.py` | no | Turn a confirmed request into new weights (60% cap on any area). |
| Retrieval | `tools/retrieval.py` | no | Finds passages for Ask the RFP (BM25 keyword ranking, no embeddings). |
| Audit view, report | `tools/audit_view.py`, `tools/report.py` | no | The trail, the JSON file and the PDF/HTML report. |
| LLM client | `llm/client.py` | – | One wrapper over LiteLLM: key check, model list, 60 s timeout, one retry, fixed temperature policy. |
| Database | `db/schema.sql`, `db/repository.py`, `db/audit.py` | – | SQLite only; the audit log is append-only. |

## Golden rules

1. **Architecture:** the LLM never ranks, never chooses a supplier and never does arithmetic that matters. `tools/ranking.py`,
   `industry.py`, `insights.py` and `gates.py` contain no LLM call.
2. **Security:** no key in the database, logs, exports or git; `.env`, `secrets.toml`, `*.db` and `snapshots/` are ignored.
3. **LLM safety:** proposal text is wrapped in `<proposal>` tags and every system prompt says it is untrusted data to be read, never obeyed.
4. **Evidence:** every mark comes with a quote; Python checks the quote against the document and flags any that is not found.
5. **Traceability:** every change is written to the audit trail; the original ranking is kept.
6. **Reproducibility:** the same validated scores always give the same ranking (exact fractions, rounded half up).
7. **Humans decide:** the app recommends; the reviewer accepts, questions or changes the priorities.

## Formulas

For criterion `c`: `s` = validated score, `m` = max score, `w` = weight (weights total 100).

| Quantity | Formula |
|---|---|
| Absolute score (0–100) | Σ (s<sub>c</sub> / m<sub>c</sub>) × w<sub>c</sub> |
| Benchmark | B<sub>c</sub> = the best score any supplier got on c |
| Gap | s<sub>c</sub> − B<sub>c</sub> (0 for the leader, otherwise negative) |
| Relative % | R<sub>c</sub> = s<sub>c</sub> / B<sub>c</sub> × 100 (100 for everyone when B<sub>c</sub> = 0) |
| PPI (peer score) | Σ (R<sub>c</sub> × w<sub>c</sub>) / Σ w<sub>c</sub> |
| Industry score | Each fact is graded Above = 100, Meets = 75, Below = 25, Not stated = 0; the grades are averaged with equal shares inside each criterion's weight. Shown beside the ranking, **never** changes the order. Price bands follow the buyer's own budget. |
| Strength label | Strong ≥ 80% of the maximum, Average 50% to under 80%, Weak under 50% |

**Tie-break order** (one sort key, scores rounded to 2 decimals first): higher PPI, earlier submission date, higher experience rating,
supplier name A to Z (case-insensitive). Each pair separated by a rule gets a written explanation.

**Must-have gates:** warranty of at least 3 years; BIS registration named; a price table present; delivery within 8 weeks. A missing fact
fails the gate ("Not stated"). A supplier that fails a gate is still scored and ranked but is marked not eligible and is never recommended.

## Edge-case rules

| Case | Rule |
|---|---|
| Benchmark is 0 | Relative % is 100 for everyone |
| Weights do not total 100 | The run is blocked |
| One supplier | Not allowed in a run here: a run needs 2–10 PDFs (a warning says so) |
| Duplicate supplier names | Blocked at input |
| The LLM fails twice | All scores 0 and an "Evaluation failed" warning |
| A gate fails | Ranked but not eligible |
| No supplier meets every must-have | No recommendation; the page says so |

## Assumptions

- Industry baseline values (price bands, battery hours, etc.) are **planning assumptions** for a fictional startup, not market research.
- The reviewer is a **typed name** (required), not a login. There is no user authentication.
- SQLite on Streamlit Community Cloud may reset on restart or redeploy. `samples/sample_run.json` is the persistent sample.
- Budget (default Rs 60,00,000) and quantity (default 100 laptops) are set per run.
- Proposal text stays in the session only; the database keeps results, quotes and SHA-256 fingerprints.

## Extensions and known differences from the build prompt

These were decided with the user and are recorded in `DECISIONS.md`; they are listed here so nothing is a surprise.

| Topic | Build prompt | This project | Why |
|---|---|---|---|
| Re-evaluation rounds | 2 | **3**; the tab is called "Re-evaluation" | The user asked for it. |
| Price scoring | Formula-based price scoring is "do not build" | Used **only** when the reviewer asks for the cheapest or the dearest; otherwise the AI's own marks stand | Lets "I want the cheapest" be honoured in a way that can be explained. |
| "Your priorities" before a run | – | Tick boxes and free text; weights change for that run only, any area is capped at 60% | Real evaluations start from priorities. |
| Two picks | – | When something was asked, the app shows the pick for the priorities **and** the best fit; if the buyer asked for the dearest and a better fit costs less, a red warning says so | Honest about the trade-off. |
| After 3 unhappy rounds | – | Returns to the original weights and the best fit | Avoids ending on a result the reviewer is still unhappy with. |
| Negotiation | Pick any supplier | One final supplier, chosen by Python, after all rounds | Keeps the choice deterministic. |
| Report | – | PDF download and a mail link (no sending from the app) | Easy to share; no mail server or password to protect. |
| "A score looks wrong" / "We don't want this supplier" | Offered in the Review flow | Not offered as buttons | Re-ranking by priorities covers the main need; set-aside is only read from the reviewer's words. |

## Tests

```powershell
pytest -q        # no API key and no network are needed; the LLM is replaced by a fake model
```

About 2,500 tests cover the formulas, gates, validator, evidence checker, reweighting, every tab's drawing and clicks, the audit trail
and the export. Tests that drive the whole app with Streamlit's `AppTest` need the real Streamlit and run on your machine.

## Deployment (Streamlit Community Cloud)

1. Push the repository to GitHub (keep it **private** until submission; make it public only if the instructor needs the code).
2. share.streamlit.io → **Create app** → **Yup, I have an app** → repository, branch `main`, main file `app.py`.
3. **Advanced settings:** Python 3.12; paste your secrets (for example `OPENAI_API_KEY = "..."`) yourself.
4. **Deploy.** `app.py` creates and seeds the database on first start.
5. Run one full evaluation on the live URL. Common cloud-only problems: a package missing from `requirements.txt`, a Windows-style path, a missing secret, too much memory.

Streamlit Cloud redeploys on every push to `main`.

## Screenshots

Screenshots of each tab are kept in the local `snapshots/` folder (not committed). Add the ones you want to show here after your live run.

## Sample run

`samples/sample_run.json` follows the export format of the Audit tab (the same keys the Audit download writes: `rfp_run_id`,
`created_at`, `status`, `provider`, `model`, `temperature`, `template`, `criteria_snapshot`, `suppliers`, `recommendation`,
`tie_break_explanations`, `feedback_rounds`, `negotiation_points`, `totals`). It was produced by the app's own export function from
an offline run with a scripted test model (`provider: OpenAI`, `model: test-model`), so it contains no key and no real proposal text.

## Future scope

Embedding or hybrid retrieval for 50+ suppliers with 100-page proposals · 3-year total cost of ownership · market price benchmarks ·
multi-reviewer voting · user login · a hosted database for permanent audit history · side-by-side run comparison · editable baselines and
must-have gates on the Criteria tab · formula-based price scoring beyond the cheapest/dearest request.
