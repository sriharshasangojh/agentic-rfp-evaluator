"""Tab 2: New evaluation (feature F09, spec sections 8 and 13.3).

The visitor uploads 1 to 10 supplier proposal PDFs and checks one small form per PDF: the supplier name (suggested from
the file name), the submission date (read from the PDF; see `orchestrator.suggest_submission_date`) and an optional
experience rating. They also
set the buyer's budget and quantity, then click **Evaluate suppliers**. The checks, the run and
everything saved live in `agents/orchestrator.py`; this file only draws the controls and shows what came back.

The PDFs come from the upload box only (DECISIONS 82). Nothing is read from `data/proposals/` here or anywhere else
at run time, and the proposal text is kept in this browser session only, never in the database.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from agents import evaluator, orchestrator
from db import repository
from llm import client
from tools import industry, pdf_tool
from ui import sidebar

DEFAULT_BUDGET_RS = 6_000_000      # Meridian's RFP; the visitor can change it for any other purchase
DEFAULT_QUANTITY = 100

BUDGET_KEY = "ne_budget"
QUANTITY_KEY = "ne_quantity"
FILES_KEY = "ne_files"
PDF_CACHE_KEY = "ne_pdf_cache"     # {file id: _Upload}, so a file is read once
RESULT_KEY = "ne_result"           # the RunResult of the latest run, shown until the next run starts
BUTTON_KEY = "ne_evaluate"
REQUEST_KEY = "ne_run_requested"   # set by the button; the next redraw of this tab runs the batch (and clears it at once)

MIN_DATE = date(orchestrator.FIRST_YEAR, 1, 1)   # the range the date box accepts
MAX_DATE = date(orchestrator.LAST_YEAR, 12, 31)
DATE_NOTES = {
    orchestrator.LABELLED: "Submission date read from the PDF; change it if it is wrong.",
    orchestrator.FIRST_ON_PAGE_ONE: "No submission date label found, so the first date on page 1 is filled in; change it if it is wrong.",
    orchestrator.TODAY: "No date found in the PDF, so today's date is filled in; change it if it is wrong.",
}
RATING_HELP = ("Optional. Your own view of this supplier, not read from the PDF. 1 = new, no references. 3 = some relevant "
               "projects and a few references. 5 = many years, many similar projects and repeat clients. Only used to "
               "separate suppliers that tie on score and submission date; a rated supplier comes before an unrated one.")

PPI_HELP = "How close this supplier came to the best offer on each criterion, weighted by importance."
INDUSTRY_HELP = ("How this offer compares with standard business-laptop terms: price, warranty, repair time, battery, memory, "
                 "delivery, certifications and data wiping. Baseline values are planning assumptions.")


# ---------------------------------------------------------------- the pieces

def _start_run() -> None:
    """Button callback. It runs before the page is redrawn: it asks this tab to run the batch in that redraw, and it
    sets the sidebar's "run in progress" flag so the model box shows as locked while the batch runs."""
    st.session_state[REQUEST_KEY] = True
    st.session_state[sidebar.RUN_KEY] = True
    st.session_state.pop(RESULT_KEY, None)


def _purchase_terms(disabled: bool) -> industry.PurchaseTerms | None:
    """The budget and quantity of this purchase. They go to the model, the price grade and the saved run."""
    budget_column, quantity_column = st.columns(2)
    budget = budget_column.number_input(
        "Budget (Rs, including GST and services)", min_value=1, value=DEFAULT_BUDGET_RS, step=100_000, format="%d",
        key=BUDGET_KEY, disabled=disabled, help="The most the buyer plans to spend in total. Used for the price check.")
    quantity = quantity_column.number_input(
        "Quantity (laptops)", min_value=1, max_value=100_000, value=DEFAULT_QUANTITY, step=1, format="%d",
        key=QUANTITY_KEY, disabled=disabled, help="How many laptops the buyer wants.")
    try:
        terms = industry.PurchaseTerms(budget_rs=int(budget), quantity=int(quantity))
    except (TypeError, ValueError) as error:
        st.error(str(error))
        return None
    st.caption(f"{evaluator.format_rupees(terms.budget_rs)} for {terms.quantity} laptop(s), "
               f"about {evaluator.format_rupees(terms.budget_per_unit)} per laptop.")
    return terms


def _uploaded_files(disabled: bool) -> list[Any]:
    files = st.file_uploader(
        "Supplier proposals (PDF, 1 to 10 files)", type=["pdf"], accept_multiple_files=True, key=FILES_KEY,
        disabled=disabled, help="Text-based PDFs only. Each file is one supplier's proposal.")
    return list(files or [])


def _file_id(file: Any) -> str:
    return str(getattr(file, "file_id", None) or file.name)


@dataclass(frozen=True)
class _Upload:
    """What was learned from one uploaded file the first time it was seen."""

    pdf: pdf_tool.ExtractedPdf | None
    error: str | None
    date_suggestion: orchestrator.DateSuggestion | None


def _today() -> date:
    """Today's date where the visitor is (their browser's time zone when it is known), else on this computer."""
    try:
        return datetime.now(ZoneInfo(st.context.timezone)).date()
    except Exception:   # no time zone known, or no time-zone data on this computer
        return date.today()


def _read_uploads(files: Sequence[Any]) -> dict[str, _Upload]:
    """Read each uploaded PDF once and keep the result for this session. Files that were removed are forgotten."""
    cache: dict[str, _Upload] = st.session_state.setdefault(PDF_CACHE_KEY, {})
    current: dict[str, _Upload] = {}
    for file in files:
        identifier = _file_id(file)
        if identifier not in cache:
            pdf, error = orchestrator.read_pdf(file.getvalue())
            suggestion = orchestrator.suggest_submission_date(pdf, _today()) if pdf is not None else None
            cache[identifier] = _Upload(pdf, error, suggestion)
        current[identifier] = cache[identifier]
    for stale in set(cache) - set(current):
        del cache[stale]
    return current


def _proposal_forms(files: Sequence[Any], reads: dict[str, _Upload], disabled: bool) -> list[orchestrator.Proposal]:
    """One small form per uploaded PDF. Widget keys use the file's id, so each form keeps its values while the page redraws."""
    proposals: list[orchestrator.Proposal] = []
    for number, file in enumerate(files, start=1):
        identifier = _file_id(file)
        pdf, error, suggestion = reads[identifier].pdf, reads[identifier].error, reads[identifier].date_suggestion
        with st.container(border=True):
            st.text(f"{number}. {file.name}")
            name_column, date_column, rating_column = st.columns([3, 2, 2])
            found = suggestion.date if suggestion is not None else None
            name = name_column.text_input(
                "Supplier name", value=orchestrator.default_supplier_name(file.name), key=f"ne_name_{identifier}", disabled=disabled)
            when = date_column.date_input(
                "Submission date", value=found, min_value=MIN_DATE, max_value=MAX_DATE, format="DD/MM/YYYY",
                key=f"ne_date_{identifier}", disabled=disabled)
            rating = rating_column.selectbox(
                "Experience rating (1 to 5, optional)", orchestrator.EXPERIENCE_RATINGS, index=None, placeholder="Not rated",
                key=f"ne_rating_{identifier}", disabled=disabled, help=RATING_HELP)
            if pdf is not None and suggestion is not None:
                st.caption(f"{pdf.page_count} page(s) read. {DATE_NOTES[suggestion.source]}")
        if isinstance(when, (list, tuple)):
            when = when[0] if when else None
        proposals.append(orchestrator.Proposal(file.name, name, when if isinstance(when, date) else None, rating, pdf, error))
    return proposals


def _show_checks(check: orchestrator.InputCheck) -> None:
    for message in check.errors:
        st.error(message)
    for message in check.notes:
        st.info(message)


# ---------------------------------------------------------------- running and showing a run

def _run(proposals: Sequence[orchestrator.Proposal], terms: industry.PurchaseTerms, settings: client.LLMSettings) -> None:
    """Run the batch inside a live step log, keep the result, then redraw so the sidebar unlocks."""
    reviewer = str(st.session_state.get("reviewer_name") or "")
    result: orchestrator.RunResult | None = None
    try:
        with st.status("Evaluating suppliers...", expanded=True) as status:
            result = orchestrator.run_evaluation(proposals, terms, settings, reviewer, progress=st.text)
            if result.status == repository.RUN_EVALUATED:
                status.update(label="Evaluation complete", state="complete", expanded=False)
            else:
                status.update(label="The evaluation did not finish", state="error", expanded=True)
    finally:
        st.session_state[sidebar.RUN_KEY] = False   # also when the script is stopped half way, so nothing stays locked
    st.session_state[RESULT_KEY] = result
    st.rerun()


def _leaderboard_table(result: orchestrator.RunResult) -> pd.DataFrame:
    return pd.DataFrame(orchestrator.leaderboard_rows(result.suppliers), columns=list(orchestrator.LEADERBOARD_COLUMNS))


def _show_result(result: orchestrator.RunResult) -> None:
    st.markdown("#### Result")
    if result.status != repository.RUN_EVALUATED:
        st.error(result.message)
    else:
        st.success(result.message)
    if result.run_id:
        st.caption("Run ID")
        st.code(result.run_id, language=None)

    if result.suppliers:
        tokens, cost = st.columns(2)
        tokens.metric("Tokens used", "n/a" if result.tokens is None else f"{result.tokens:,}")
        cost.metric("Estimated cost (USD)", "n/a" if result.cost_usd is None else f"{result.cost_usd:.4f}")
        st.dataframe(
            _leaderboard_table(result), hide_index=True, width="stretch",
            column_config={
                "Overall /100": st.column_config.NumberColumn(format="%.2f"),
                "Peer score (PPI)": st.column_config.NumberColumn(format="%.2f", help=PPI_HELP),
                "Industry score": st.column_config.NumberColumn(format="%.2f", help=INDUSTRY_HELP),
            })
        for note in result.tie_break_explanations:
            st.caption(note)
        flagged = [(s.name, s.warnings) for s in result.suppliers if s.warnings]
        if flagged:
            with st.expander(f"Warnings ({sum(len(w) for _, w in flagged)})"):
                for name, warnings in flagged:
                    for warning in warnings:
                        st.text(f"{name}: {warning}")

    if result.steps:
        with st.expander("Step log", expanded=result.status != repository.RUN_EVALUATED):
            st.code("\n".join(result.steps), language=None, wrap_lines=True)


# ---------------------------------------------------------------- the tab

def render() -> None:
    running = bool(st.session_state.pop(REQUEST_KEY, False))   # True only in the redraw that the button started
    settings = sidebar.get_llm_settings()

    st.subheader("New evaluation")
    st.caption("Upload the supplier proposals, say what you are buying, then evaluate. Each file is read for this session only.")
    terms = _purchase_terms(running)
    files = _uploaded_files(running)

    proposals: list[orchestrator.Proposal] = []
    check = orchestrator.InputCheck(errors=(), notes=())
    if files:
        proposals = _proposal_forms(files, _read_uploads(files), running)
        with repository.connection() as conn:
            criteria = repository.list_criteria(conn, active_only=True)
        check = orchestrator.check_inputs(proposals, criteria)
        _show_checks(check)
    else:
        st.session_state.pop(PDF_CACHE_KEY, None)
        st.info("Upload 1 to 10 supplier proposal PDFs. The submission date is filled in from each PDF (check it), and the "
                "experience rating is optional. The brief expects at least 4 proposals for a meaningful peer comparison.")
    if settings is None:
        st.info("Before evaluating, choose a provider, validate your API key and pick a model in the sidebar.")

    ready = bool(files) and check.ok and settings is not None and terms is not None
    st.button("Evaluate suppliers", key=BUTTON_KEY, type="primary", disabled=running or not ready, on_click=_start_run)

    if running:
        if ready:
            _run(proposals, terms, settings)   # ends with st.rerun()
        else:
            st.session_state[sidebar.RUN_KEY] = False   # the inputs are no longer valid; never leave the sidebar locked
            st.rerun()
    result = st.session_state.get(RESULT_KEY)
    if result is not None:
        _show_result(result)
