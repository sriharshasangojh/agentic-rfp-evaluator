"""The header banner (look only): scenario line, title, subtitle and a four-step journey.

The banner never shows the provider, the model, the budget or the number of laptops: those change with every session or
run and live in the sidebar and on New evaluation.
"""
from __future__ import annotations

import html
import sqlite3
from collections.abc import Sequence
from typing import Any

from db import repository

TITLE = "RFP Evaluation & Supplier Ranking"
SUBTITLE = "Evidence-backed scoring, ranked by tested Python."
SCENARIO = "Meridian Analytics Pvt. Ltd. · RFP MA-RFP-2026-017"
DONE, NOW, LATER = "done", "now", ""

# (step label, what it says when finished, what it says otherwise)
STEPS: tuple[tuple[str, str, str], ...] = (
    ("1 · Criteria", "Weights set", "Set the weights"),
    ("2 · Proposals", "Uploaded", "Upload the PDFs"),
    ("3 · Evaluation", "Scored and ranked", "Not run yet"),
    ("4 · Decision", "Accept or re-weigh", "After the evaluation"),
)


def step_states(criteria_ready: bool, run_shown: bool) -> list[str]:
    """DONE for a finished step, NOW for the one to do next (the last step stays NOW once everything before it is done)."""
    finished = [criteria_ready, run_shown, run_shown, False]
    states = [DONE if flag else LATER for flag in finished]
    states[next((i for i, flag in enumerate(finished) if not flag), 3)] = NOW
    return states


def banner_html(states: Sequence[str]) -> str:
    steps = []
    for (label, done_text, todo_text), state in zip(STEPS, states):
        mark = "✓ " if state == DONE else ""
        text = done_text if state == DONE or (state == NOW and label.startswith("4")) else todo_text
        steps.append(f'<div class="hero-step {state}">{mark}{html.escape(label)}<b>{html.escape(text)}</b></div>')
    return (f'<div class="hero-scenario">{html.escape(SCENARIO)}</div><div class="hero-title">{html.escape(TITLE)}</div>'
            f'<div class="hero-sub">{html.escape(SUBTITLE)}</div><div class="hero-steps">{"".join(steps)}</div>')


def _criteria_ready() -> bool:
    try:
        with repository.connection() as conn:
            criteria = repository.list_criteria(conn, active_only=True)
    except (sqlite3.Error, OSError):
        return False
    return bool(criteria) and abs(float(sum(c.weight for c in criteria)) - 100.0) < 1e-6


def show(st: Any) -> None:
    from ui.tab_new_evaluation import SHOWN_RUN_KEY       # imported here so that this module stays easy to test
    states = step_states(_criteria_ready(), bool(st.session_state.get(SHOWN_RUN_KEY)))
    st.markdown(banner_html(states), unsafe_allow_html=True)
