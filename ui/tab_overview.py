"""Tab 3: Overview, the manager's view (feature F10, spec sections 10.2 and 13.3).

Shows the run that was just evaluated (or, later, one opened from the history): the recommendation card with the
runner-up and the industry warning, the leaderboard with its three scores and an eligibility badge, the tie-break
notes, and the Strong / Average / Weak table. Everything is read back from the saved run, so what is shown is exactly
what was stored. Which supplier is recommended is decided in `tools/insights.py`; this file only reads and draws.

Not on this tab yet: the pros and cons and the worded reason (F16) and the price vs budget bar (F17).

The leaderboard and the strength table are small HTML tables, because a Streamlit data table cannot show a coloured
badge with a hover text. Every piece of text that comes from a proposal or from the buyer goes through `html.escape`.
"""
from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from typing import Any

import streamlit as st

from agents import evaluator, orchestrator
from db import repository
from tools import industry, insights
from ui import theme
from ui.tab_new_evaluation import INDUSTRY_HELP, PPI_HELP, SHOWN_RUN_KEY

OVERALL_HELP = "The weighted total of the marks for each criterion, out of 100."
HOVER_NOTE = "Hover over a Not eligible badge to see which mandatory check was not passed."
STRENGTH_NOTE = ("Strong: 80% or more of the maximum mark. Average: 50% to under 80%. Weak: under 50%. "
                 "The score for each criterion is in brackets.")
EVALUATION_FAILED_TEXT = "The evaluation failed (see the warnings on the Scorecards tab)."
CHECK_FAILED_TEXT = "A mandatory check was not passed."
NOT_ELIGIBLE, ELIGIBLE = "Not eligible", "Eligible"

ELIGIBLE_COLOURS = ("#2E7D32", "#E8F5E9")      # text, background (same pair as a Strong cell); the word is always printed
NOT_ELIGIBLE_COLOURS = ("#C62828", "#FFEBEE")
MUTED = "#4B5563"

# One line on purpose: Streamlit's markdown ends an HTML block at a blank line, so no line breaks inside the HTML.
_CSS = (
    ".ov-wrap{overflow-x:auto;margin:0.25rem 0 0.5rem 0}"
    f".ov-table{{width:100%;border-collapse:collapse;font-size:0.95rem;color:{theme.TEXT}}}"
    f".ov-table th{{background:{theme.TAB_SELECTED_BACKGROUND};color:{theme.HEADING};text-align:left;"
    f"padding:0.5rem 0.65rem;border-bottom:2px solid {theme.TEAL};white-space:nowrap;font-weight:600}}"
    f".ov-table td{{padding:0.5rem 0.65rem;border-bottom:1px solid {theme.BORDER};vertical-align:middle}}"
    ".ov-table th[title]{cursor:help}"
    ".ov-num{text-align:right;font-variant-numeric:tabular-nums}"
    f".ov-best td{{background:{theme.TAB_HOVER_BACKGROUND}}}"
    ".ov-badge{display:inline-block;padding:0.1rem 0.6rem;border-radius:999px;font-weight:600;font-size:0.85rem;white-space:nowrap}"
    ".ov-badge[title]{cursor:help}"
    f".ov-pill{{display:inline-block;margin-left:0.5rem;padding:0.05rem 0.5rem;border-radius:999px;font-size:0.75rem;"
    f"font-weight:600;color:#FFFFFF;background:{theme.PRIMARY}}}"
    ".ov-strength{text-align:center;font-weight:600;white-space:nowrap}"
    ".ov-weight{display:block;font-weight:400;font-size:0.75rem}"
)


# ---------------------------------------------------------------- what the tab shows, worked out from the saved run

@dataclass(frozen=True)
class Strength:
    criterion_id: int
    name: str
    weight: float | None
    label: str | None      # Strong / Average / Weak, or None when the stored marks cannot be graded
    score: float | None
    max_score: float | None


@dataclass(frozen=True)
class Row:
    """One supplier, as the leaderboard and the recommendation card need it."""

    rank: int
    name: str
    overall: float
    ppi: float
    industry: float
    submitted: str
    experience: str
    eligible: bool
    why_not: tuple[str, ...]            # empty when eligible; otherwise the failed checks, in words
    weak_attributes: tuple[str, ...]    # industry attributes graded Below or Not stated
    tie_break: str | None               # "X above Y: reason", on the supplier that won the tie
    strengths: tuple[Strength, ...]


@dataclass(frozen=True)
class Overview:
    run_line: str
    rows: tuple[Row, ...]
    recommendation: insights.Recommendation
    recommended: Row | None
    runner_up: Row | None
    top_ranked: Row | None
    reason: str                         # one plain sentence under the recommended supplier (or under the "none eligible" notice)
    industry_warning: str | None
    criteria: tuple[tuple[int, str, float | None], ...]    # (criterion_id, name, weight), in marking-scheme order


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _strengths(result: dict[str, Any]) -> tuple[Strength, ...]:
    found: list[Strength] = []
    for item in result.get("criteria") or []:
        score, maximum = _number(item.get("score")), _number(item.get("max_score"))
        try:
            label = insights.strength_label(score, maximum) if score is not None and maximum is not None else None
        except ValueError:      # a maximum of 0 or less cannot be graded
            label = None
        criterion_id = int(item.get("criterion_id", 0))
        found.append(Strength(criterion_id, str(item.get("name") or f"Criterion {criterion_id}"), _number(item.get("weight")),
                              label, score, maximum))
    return tuple(found)


def _why_not(stored: repository.StoredSupplier) -> tuple[str, ...]:
    if stored.gates_passed:
        return ()
    result = stored.result
    if result.get("evaluation_failed"):
        return (EVALUATION_FAILED_TEXT,)
    failed = tuple(f"{g.get('name')}: {g.get('reason')}" for g in result.get("gate_details") or [] if not g.get("passed", True))
    return failed or (CHECK_FAILED_TEXT,)


def _weak_attributes(result: dict[str, Any]) -> tuple[str, ...]:
    attributes = (result.get("industry_breakdown") or {}).get("attributes") or []
    return tuple(str(a.get("label")) for a in attributes if a.get("grade") in (industry.BELOW, industry.NOT_STATED))


def _row(stored: repository.StoredSupplier) -> Row:
    return Row(
        rank=stored.final_rank, name=stored.supplier_name, overall=stored.absolute_score, ppi=stored.ppi,
        industry=stored.industry_score, submitted=orchestrator.format_submitted(stored.submission_date),
        experience=orchestrator.format_experience(stored.experience_rating), eligible=stored.gates_passed,
        why_not=_why_not(stored), weak_attributes=_weak_attributes(stored.result),
        tie_break=stored.result.get("tie_break_explanation") or None, strengths=_strengths(stored.result))


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _run_line(run: repository.RunRecord, count: int) -> str:
    parts = [f"Run {run.rfp_run_id}"]
    if run.model:
        parts.append(f"{run.model} ({run.provider})" if run.provider else run.model)
    parts.append(f"{count} supplier(s)")
    if run.budget_rs is not None and run.quantity is not None:
        parts.append(f"budget {evaluator.format_rupees(run.budget_rs)} for {run.quantity} laptop(s)")
    return " · ".join(parts)


def _reason(recommended: Row | None, top: Row | None, rows: tuple[Row, ...]) -> str:
    if recommended is not None:
        higher = [r.name for r in rows if r.rank < recommended.rank]
        if not higher:
            return f"Ranked {recommended.rank} of {len(rows)} and passes every mandatory check."
        verb = "ranks higher but does not pass" if len(higher) == 1 else "rank higher but do not pass"
        return (f"Ranked {recommended.rank} of {len(rows)}. {_join_names(higher)} {verb} every mandatory check "
                "(hover over the Not eligible badge in the table to see why).")
    if top is not None:
        return f"{top.name} is ranked 1 (peer score {top.ppi:.2f}) but does not pass: {'; '.join(top.why_not)}"
    return ""


def build_overview(run: repository.RunRecord, suppliers: list[repository.StoredSupplier]) -> Overview:
    """Work out everything the tab shows from a saved run. No Streamlit and no database access here."""
    rows = tuple(sorted((_row(s) for s in suppliers), key=lambda r: r.rank))
    recommendation = insights.choose_recommendation([insights.Contender(r.name, r.rank, r.eligible) for r in rows])
    by_name = {r.name: r for r in rows}
    recommended = by_name.get(recommendation.recommended or "")
    runner_up = by_name.get(recommendation.runner_up or "")
    top = by_name.get(recommendation.top_ranked or "")
    warning = insights.industry_warning(recommended.industry, recommended.weak_attributes) if recommended else None
    criteria = tuple((int(c.get("criterion_id", 0)), str(c.get("name", "")), _number(c.get("weight"))) for c in run.criteria_snapshot)
    if not criteria and rows:
        criteria = tuple((s.criterion_id, s.name, s.weight) for s in rows[0].strengths)   # a run saved without its snapshot
    return Overview(_run_line(run, len(rows)), rows, recommendation, recommended, runner_up, top,
                    _reason(recommended, top, rows), warning, criteria)


# ---------------------------------------------------------------- the two small HTML tables

def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _title(text: str) -> str:
    """A hover text for an HTML attribute, on one line of HTML (line breaks become &#10;)."""
    return _e(text).replace("\n", "&#10;")


def _badge(row: Row) -> str:
    text, (colour, background) = (ELIGIBLE, ELIGIBLE_COLOURS) if row.eligible else (NOT_ELIGIBLE, NOT_ELIGIBLE_COLOURS)
    hover = "" if row.eligible else f' title="{_title(chr(10).join(row.why_not))}"'
    return f'<span class="ov-badge" style="color:{colour};background:{background}"{hover}>{text}</span>'


def leaderboard_html(rows: tuple[Row, ...], recommended: str | None) -> str:
    """The leaderboard: Rank, Supplier, Overall /100, Peer score (PPI), Industry score, Submitted, Experience, Eligible."""
    headers = (("Rank", None, False), ("Supplier", None, False), ("Overall /100", OVERALL_HELP, True),
               ("Peer score (PPI)", PPI_HELP, True), ("Industry score", INDUSTRY_HELP, True),
               ("Submitted", None, False), ("Experience", None, False), ("Eligible", None, False))
    head = ""
    for name, help_text, number in headers:
        alignment = ' class="ov-num"' if number else ""
        hover = f' title="{_title(help_text)}"' if help_text else ""
        head += f"<th{alignment}{hover}>{_e(name)}</th>"
    body: list[str] = []
    for row in rows:
        is_best = row.name == recommended
        pill = '<span class="ov-pill">Recommended</span>' if is_best else ""
        highlight = ' class="ov-best"' if is_best else ""
        body.append(
            f'<tr{highlight}>'
            f'<td>{row.rank}</td><td><strong>{_e(row.name)}</strong>{pill}</td>'
            f'<td class="ov-num">{row.overall:.2f}</td><td class="ov-num">{row.ppi:.2f}</td><td class="ov-num">{row.industry:.2f}</td>'
            f'<td>{_e(row.submitted)}</td><td>{_e(row.experience)}</td><td>{_badge(row)}</td></tr>')
    return (f"<style>{_CSS}</style><div class=\"ov-wrap\"><table class=\"ov-table\"><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def strength_html(rows: tuple[Row, ...], criteria: tuple[tuple[int, str, float | None], ...]) -> str:
    """One row per supplier, one column per criterion. Each cell prints the word (Strong / Average / Weak) and the score."""
    head = "<th>Supplier</th>" + "".join(
        f'<th class="ov-strength">{_e(name)}' + (f'<span class="ov-weight">weight {weight:g}</span>' if weight is not None else "") + "</th>"
        for _id, name, weight in criteria)
    body: list[str] = []
    for row in rows:
        by_id = {s.criterion_id: s for s in row.strengths}
        cells: list[str] = []
        for criterion_id, _name, _weight in criteria:
            strength = by_id.get(criterion_id)
            if strength is None or strength.label is None:
                cells.append('<td class="ov-strength" style="color:#4B5563">Not available</td>')
                continue
            colour, background = theme.STRENGTH_COLOURS[strength.label]
            marks = f" ({strength.score:g}/{strength.max_score:g})" if strength.score is not None and strength.max_score is not None else ""
            cells.append(f'<td class="ov-strength" style="color:{colour};background:{background}">{strength.label}{marks}</td>')
        body.append(f"<tr><td><strong>{_e(row.name)}</strong></td>{''.join(cells)}</tr>")
    return (f"<style>{_CSS}</style><div class=\"ov-wrap\"><table class=\"ov-table\"><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def runner_up_html(row: Row) -> str:
    """The runner-up as a tinted strip under the recommended supplier: dark teal and slate text, not the small grey used for notes."""
    return (
        f'<div style="border-left:4px solid {theme.TEAL};background:{theme.TAB_HOVER_BACKGROUND};border-radius:6px;'
        f'padding:0.55rem 0.9rem;margin:0.6rem 0 0.25rem 0">'
        f'<span style="color:{theme.TEAL_TEXT};font-size:0.75rem;font-weight:700;letter-spacing:0.06em;text-transform:uppercase">Runner-up</span>'
        f'<span style="color:{theme.HEADING};font-size:1.15rem;font-weight:700;margin:0 0.6rem">{_e(row.name)}</span>'
        f'<span style="color:{theme.TEXT};font-size:1rem">Ranked {row.rank} \u00b7 Peer score {row.ppi:.2f}</span></div>')


# ---------------------------------------------------------------- drawing

def _small(text: str) -> None:
    """A line of small grey text. `text` is plain text (it is escaped here), never HTML."""
    st.markdown(f'<p style="color:{MUTED};font-size:0.9rem;margin:0.25rem 0">{_e(text)}</p>', unsafe_allow_html=True)


def _recommendation_card(overview: Overview) -> None:
    with st.container(border=True):
        recommended = overview.recommended
        if recommended is None:
            st.error("No supplier meets every mandatory requirement.")
            if overview.reason:
                _small(overview.reason)
            return
        st.markdown(
            f'<div style="color:{theme.TEAL_TEXT};font-size:0.8rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase">'
            f'Recommended supplier</div>'
            f'<div style="color:{theme.HEADING};font-size:1.7rem;font-weight:700;line-height:1.3">{_e(recommended.name)}</div>',
            unsafe_allow_html=True)
        overall, ppi, industry_score = st.columns(3)
        overall.metric("Overall /100", f"{recommended.overall:.2f}", help=OVERALL_HELP)
        ppi.metric("Peer score (PPI)", f"{recommended.ppi:.2f}", help=PPI_HELP)
        industry_score.metric("Industry score", f"{recommended.industry:.2f}", help=INDUSTRY_HELP)
        _small(overview.reason)
        runner_up = overview.runner_up
        if runner_up is not None:
            st.markdown(runner_up_html(runner_up), unsafe_allow_html=True)
        elif len(overview.rows) > 1:
            _small("No other supplier passes every mandatory check.")
        if overview.industry_warning:
            st.warning(overview.industry_warning)


def _show(overview: Overview) -> None:
    _small(overview.run_line)
    _recommendation_card(overview)

    st.markdown("#### Leaderboard")
    recommended_name = overview.recommended.name if overview.recommended else None
    st.markdown(leaderboard_html(overview.rows, recommended_name), unsafe_allow_html=True)
    for row in overview.rows:
        if row.tie_break:
            _small(row.tie_break)
    if any(not row.eligible for row in overview.rows):
        _small(HOVER_NOTE)

    st.markdown("#### Strength by criterion")
    st.markdown(strength_html(overview.rows, overview.criteria), unsafe_allow_html=True)
    _small(STRENGTH_NOTE)


def _empty_state() -> None:
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. The recommendation, leaderboard and strengths will appear here."
    )


def render() -> None:
    run_id = st.session_state.get(SHOWN_RUN_KEY)
    if not run_id:
        _empty_state()
        return
    try:
        with repository.connection() as conn:
            run = repository.get_run(conn, run_id)
            suppliers = repository.list_supplier_results(conn, run_id) if run is not None else []
    except (sqlite3.Error, OSError):
        st.error("The saved results could not be read. Close any other program using `rfp_evaluation.db`, "
                 "wait a few seconds, then reload the page.")
        return
    if run is None or not suppliers:
        st.info("The results of this run could not be found. Run a new evaluation on the New evaluation tab.")
        return
    _show(build_overview(run, suppliers))
