# Agentic RFP Evaluation and Supplier Ranking

A Streamlit app that reads supplier proposals (PDFs) for a startup's laptop purchase, asks an LLM to
score them against configurable criteria with evidence, and then uses deterministic Python to rank
the suppliers. A human reviewer can question, re-evaluate or override results.

> The full README (architecture, formulas, deployment, future scope) is written in feature F22.

## Run locally (Windows, PowerShell)

```powershell
cd "C:\Users\sriha\OneDrive\Desktop\vibe coding projects\RPF Evaluator\rfp_evaluator"
python -m venv .venv
.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
pytest
streamlit run app.py                # opens http://localhost:8501
```
