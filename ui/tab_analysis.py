"""Tab 5, Analysis (feature F18, spec 13.3): the technical view of one run, with three charts.

* A numeric heatmap: every supplier's mark on every criterion.
* Price against quality: each supplier's total price against its overall score, with the budget as a dashed line.
* What if price mattered more or less: the Peer score of every supplier when the price criterion counts for 10% to 40% of the
  decision (the other weights keep their proportions, the total stays 100) and a table that shows where the leader changes.

Nothing here marks, ranks or changes anything: the charts only read the saved marks, and the what-if asks `tools/insights.py` to rank
the same marks again with other weights. All text from a proposal (supplier names) is escaped before it goes into HTML.
"""
from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from db import repository
from tools import insights, ranking
from ui import tab_overview, theme
from ui.tab_new_evaluation import SHOWN_RUN_KEY

PRICE_CRITERION = "commercial value"          # the criterion that stands for price in the seeded marking scheme (compared in lower case)
LAKH = 100000.0

HEATMAP_HEADING = "Marks by supplier and criterion"
HEATMAP_NOTE = "Each cell is the mark the AI gave. A darker cell is a higher share of the marks available for that criterion."
SCATTER_HEADING = "Price against quality"
SCATTER_NOTE = ("Further right costs more, higher is better overall. A cross marks a supplier that does not meet every must-have. "
                "The dashed line is the budget.")
WHAT_IF_HEADING = "What if price mattered more or less?"
WHAT_IF_NOTE = ("Every supplier's Peer score when the price criterion counts for 10% to 40% of the decision. The other criteria keep "
                "their proportions, the marks are the saved ones, and nothing is marked again. The dashed line is the weight this run used. Suppliers with identical marks draw on top of each other; the table shows each one.")
NO_HEATMAP_TEXT = "No chart: no supplier has marks to show."
NO_SCATTER_TEXT = "No chart: this run has no budget, or no supplier states a total price."
NO_WHAT_IF_TEXT = ("No chart: the what-if needs a criterion called Commercial Value and at least one other criterion with a weight, "
                   "and marks from at least one supplier.")
NO_LEADER_TEXT = "No supplier meets every must-have, so there is no leader to compare."
CHANGE_TAG = "Leader changes here"


# ---------------------------------------------------------------- what the tab shows, worked out from the saved run

@dataclass(frozen=True)
class Entry:
    """One supplier of the run."""

    name: str
    rank: int
    eligible: bool
    failed: bool                                   # the evaluation failed: there are no marks to draw
    overall: float
    total_rs: float | None
    marks: tuple[insights.CriterionResult, ...]
    colour: str


@dataclass(frozen=True)
class Heatmap:
    suppliers: tuple[str, ...]
    criteria: tuple[str, ...]
    marks: tuple[tuple[float | None, ...], ...]    # one row per supplier, one column per criterion (None: no mark)
    maxima: tuple[tuple[float | None, ...], ...]


@dataclass(frozen=True)
class Scatter:
    entries: tuple[Entry, ...]                     # the suppliers that state a total
    budget_rs: float
    not_stated: tuple[str, ...]


@dataclass(frozen=True)
class Analysis:
    entries: tuple[Entry, ...]
    heatmap: Heatmap | None
    scatter: Scatter | None
    what_if: insights.WhatIf | None
    failed: tuple[str, ...]                        # suppliers whose evaluation failed (no marks)
    left_out: tuple[str, ...]                      # suppliers left out of the what-if because their marks do not match the others'


def _colour_of(names: list[str]) -> dict[str, str]:
    """One fixed colour per supplier: by name, so it does not change when a re-ranking moves the supplier."""
    palette = theme.SUPPLIER_COLOURS
    return {name: palette[i % len(palette)] for i, name in enumerate(sorted(names, key=str.casefold))}


def _entries(run: repository.RunRecord, suppliers: list[repository.StoredSupplier]) -> tuple[Entry, ...]:
    colours = _colour_of([s.supplier_name for s in suppliers])
    budget = tab_overview._positive(run.budget_rs)
    out = []
    for s in suppliers:
        failed = bool(s.result.get("evaluation_failed"))
        marks = () if failed else tab_overview._criterion_results(s.result)
        out.append(Entry(s.supplier_name, s.final_rank, bool(s.gates_passed), failed, s.absolute_score,
                         insights.price_facts(s.result.get("extracted_facts"), budget).total_rs, marks, colours[s.supplier_name]))
    return tuple(out)


def build_heatmap(entries: tuple[Entry, ...]) -> Heatmap | None:
    shown = [e for e in entries if e.marks]
    if not shown:
        return None
    columns: dict[int, str] = {}
    for e in shown:
        for m in e.marks:
            columns.setdefault(m.criterion_id, m.name)
    ids = sorted(columns)
    marks, maxima = [], []
    for e in shown:
        by_id = {m.criterion_id: m for m in e.marks}
        marks.append(tuple(by_id[i].score if i in by_id else None for i in ids))
        maxima.append(tuple(by_id[i].max_score if i in by_id else None for i in ids))
    return Heatmap(tuple(e.name for e in shown), tuple(columns[i] for i in ids), tuple(marks), tuple(maxima))


def build_scatter(entries: tuple[Entry, ...], budget_rs: float | None) -> Scatter | None:
    if budget_rs is None:
        return None
    drawn = tuple(e for e in entries if e.total_rs is not None)
    if not drawn:
        return None
    return Scatter(drawn, budget_rs, tuple(e.name for e in entries if e.total_rs is None and not e.failed))


def build_what_if(run: repository.RunRecord, suppliers: list[repository.StoredSupplier],
                  entries: tuple[Entry, ...]) -> tuple[insights.WhatIf | None, tuple[str, ...]]:
    """The what-if for the suppliers whose marks cover the same criteria as the first one's, and the names that had to be left out."""
    marked = [(s, e) for s, e in zip(suppliers, entries) if e.marks]
    if not marked:
        return None, ()
    reference = marked[0][1].marks
    ids = {m.criterion_id for m in reference}
    price = next((m.criterion_id for m in reference if m.name.casefold() == PRICE_CRITERION), None)
    if price is None:
        return None, ()
    criteria = [ranking.CriterionWeight(m.criterion_id, m.weight, m.max_score) for m in reference]
    inputs, left_out = [], []
    for s, e in marked:
        if {m.criterion_id for m in e.marks} != ids:
            left_out.append(e.name)
            continue
        inputs.append(ranking.SupplierInput(s.supplier_name, s.submission_date, s.experience_rating,
                                            {m.criterion_id: m.score for m in e.marks}))
    try:
        return insights.what_if(inputs, [e.name for _s, e in marked if e.eligible], criteria, price), tuple(left_out)
    except ValueError:               # a hand-edited saved result with a mark outside its range, or twin names: no chart rather than a crash
        return None, tuple(left_out)


def build_analysis(run: repository.RunRecord, suppliers: list[repository.StoredSupplier]) -> Analysis:
    entries = _entries(run, suppliers)
    what_if, left_out = build_what_if(run, suppliers, entries)
    return Analysis(entries, build_heatmap(entries), build_scatter(entries, tab_overview._positive(run.budget_rs)), what_if,
                    tuple(e.name for e in entries if e.failed), left_out)


# ---------------------------------------------------------------- words

def _plain(value: float) -> str:
    return f"{value:g}"


def what_if_sentence(result: insights.WhatIf) -> str:
    """Where the leader changes, in plain words."""
    first, last = result.weights[0], result.weights[-1]
    if all(leader is None for leader in result.leaders):
        return NO_LEADER_TEXT
    if not result.changes:
        return f"{result.leaders[0]} leads at every price weight from {first}% to {last}%."
    parts = []
    for c in result.changes:
        if c.from_leader is None:
            parts.append(f"From {c.to_weight}% on, {c.to_leader} is the leader.")
        elif c.to_leader is None:
            parts.append(f"From {c.to_weight}% on, no supplier that meets every must-have is left to lead.")
        else:
            parts.append(f"The leader changes from {c.from_leader} to {c.to_leader} when the price weight goes from {c.from_weight}% to {c.to_weight}%.")
    return " ".join(parts)


# ---------------------------------------------------------------- the what-if table

_CSS = (
    ".an-wrap{overflow-x:auto;margin:0.25rem 0 0.5rem 0}"
    f".an-table{{width:100%;border-collapse:collapse;font-size:0.95rem;color:{theme.TEXT}}}"
    f".an-table th{{background:{theme.TAB_SELECTED_BACKGROUND};color:{theme.HEADING};text-align:left;"
    f"padding:0.5rem 0.65rem;border-bottom:2px solid {theme.TEAL};font-weight:600}}"
    f".an-table td{{padding:0.5rem 0.65rem;border-bottom:1px solid {theme.BORDER};vertical-align:middle}}"
    ".an-num{text-align:right;font-variant-numeric:tabular-nums}"
    f".an-change td{{background:{theme.TAB_HOVER_BACKGROUND}}}"
    f".an-tag{{display:inline-block;margin-left:0.5rem;padding:0.05rem 0.5rem;border-radius:999px;font-size:0.75rem;font-weight:600;"
    f"color:#FFFFFF;background:{theme.PRIMARY}}}"
    ".an-lead{font-weight:700}"
)


def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def what_if_table_html(result: insights.WhatIf) -> str:
    """One row per price weight: every supplier's Peer score, the leader, and a tag on each row where the leader differs from the row before."""
    head = "<th>Price weight</th>" + "".join(f"<th>{_e(line.name)}</th>" for line in result.lines) + "<th>Leader</th>"
    changed = {c.to_weight for c in result.changes}
    rows = []
    for i, weight in enumerate(result.weights):
        leader = result.leaders[i]
        cells = "".join(
            f'<td class="an-num{" an-lead" if line.name == leader else ""}">{line.ppis[i]:.2f}</td>' for line in result.lines)
        tag = f'<span class="an-tag">{CHANGE_TAG}</span>' if weight in changed else ""
        name = _e(leader) if leader is not None else "None"
        tr = '<tr class="an-change">' if weight in changed else "<tr>"
        rows.append(f'{tr}<td>{weight}%</td>{cells}<td class="an-lead">{name}{tag}</td></tr>')
    return (f'<style>{_CSS}</style><div class="an-wrap"><table class="an-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


# ---------------------------------------------------------------- the three charts

def _layout(figure: go.Figure, height: int, **axes: Any) -> go.Figure:
    figure.update_layout(height=height, margin={"l": 10, "r": 20, "t": 40, "b": 10}, paper_bgcolor="rgba(0,0,0,0)",
                         plot_bgcolor="rgba(0,0,0,0)", font={"color": theme.TEXT}, **axes)
    return figure


def heatmap_figure(data: Heatmap) -> go.Figure:
    """Rows are suppliers (best rank first), columns are criteria; the colour is the share of the marks available, the number is the mark."""
    shares = [[None if m is None or mx is None or mx <= 0 else round(m / mx * 100, 2) for m, mx in zip(row, maxes)]
              for row, maxes in zip(data.marks, data.maxima)]
    hover = [[None if m is None else f"{_plain(m)} of {_plain(mx)}" for m, mx in zip(row, maxes)] for row, maxes in zip(data.marks, data.maxima)]
    figure = go.Figure(go.Heatmap(
        z=shares, x=list(data.criteria), y=list(data.suppliers), zmin=0, zmax=100, showscale=False, xgap=2, ygap=2,
        colorscale=[[0, theme.HEATMAP_LOW], [1, theme.HEATMAP_HIGH]], customdata=hover,
        hovertemplate="%{y}<br>%{x}<br>%{customdata}<extra></extra>"))
    notes = []
    for y, (row, share_row) in enumerate(zip(data.marks, shares)):
        for x, (mark, share) in enumerate(zip(row, share_row)):
            if mark is None:
                continue
            notes.append({"x": data.criteria[x], "y": data.suppliers[y], "text": _plain(mark), "showarrow": False,
                          "font": {"size": 15, "color": "#FFFFFF" if (share or 0) >= 60 else theme.TEXT}})
    figure = _layout(figure, 120 + 52 * len(data.suppliers), xaxis={"side": "top"}, yaxis={"autorange": "reversed", "automargin": True})
    figure.update_layout(annotations=notes)
    return figure


def scatter_figure(data: Scatter) -> go.Figure:
    """One point per supplier that states a total: x is the total in lakh, y the overall score; a dashed line marks the budget."""
    figure = go.Figure()
    for e in data.entries:
        figure.add_trace(go.Scatter(
            x=[e.total_rs / LAKH], y=[e.overall], mode="markers+text", name=e.name, text=[e.name], textposition="top center",
            marker={"color": e.colour, "size": 15, "symbol": "circle" if e.eligible else "x"},
            customdata=[[insights.format_lakh(e.total_rs), "meets every must-have" if e.eligible else "does not meet every must-have"]],
            hovertemplate="%{text}<br>%{customdata[0]}, overall %{y:.2f}<br>%{customdata[1]}<extra></extra>"))
    figure.add_vline(x=data.budget_rs / LAKH, line_dash="dash", line_color=theme.BUDGET_LINE, line_width=2,
                     annotation_text=f"Budget {insights.format_lakh(data.budget_rs)}", annotation_position="top")
    lowest = min(min(e.total_rs for e in data.entries), data.budget_rs) / LAKH
    highest = max(max(e.total_rs for e in data.entries), data.budget_rs) / LAKH
    pad = max((highest - lowest) * 0.15, 1.0)
    return _layout(figure, 420, showlegend=True, legend={"orientation": "h", "x": 0, "y": -0.2},
                   xaxis={"title": {"text": "Total price (Rs lakh)"}, "range": [lowest - pad, highest + pad], "gridcolor": theme.BORDER, "zeroline": False},
                   yaxis={"title": {"text": "Overall score (out of 100)"}, "range": [0, 100], "gridcolor": theme.BORDER, "zeroline": False})


def what_if_figure(result: insights.WhatIf, colours: dict[str, str]) -> go.Figure:
    """One line per supplier: its Peer score at each price weight; a dashed vertical line marks the weight this run used."""
    figure = go.Figure()
    for line in result.lines:
        figure.add_trace(go.Scatter(x=list(result.weights), y=list(line.ppis), mode="lines+markers", name=line.name,
                                    line={"color": colours.get(line.name, theme.TEAL), "width": 3}, marker={"size": 8},
                                    hovertemplate="%{x}% on price<br>Peer score %{y:.2f}<extra>" + line.name.replace("<", "&lt;") + "</extra>"))
    figure.add_vline(x=result.current_weight, line_dash="dash", line_color=theme.BUDGET_LINE, line_width=2,
                     annotation_text=f"This run: {_plain(round(result.current_weight, 2))}%", annotation_position="top")
    return _layout(figure, 420, showlegend=True, legend={"orientation": "h", "x": 0, "y": -0.25},
                   xaxis={"title": {"text": "Weight given to price (%)"}, "tickvals": list(result.weights), "gridcolor": theme.BORDER, "zeroline": False},
                   yaxis={"title": {"text": "Peer score (PPI)"}, "gridcolor": theme.BORDER, "zeroline": False})


# ---------------------------------------------------------------- drawing

def _small(text: str) -> None:
    st.markdown(f'<p style="color:{tab_overview.MUTED};font-size:0.9rem;margin:0.25rem 0">{_e(text)}</p>', unsafe_allow_html=True)


def _show(analysis: Analysis) -> None:
    st.caption("Technical view. Nothing here changes a mark or a rank.")

    st.markdown(f"#### {HEATMAP_HEADING}")
    if analysis.heatmap is None:
        _small(NO_HEATMAP_TEXT)
    else:
        st.plotly_chart(heatmap_figure(analysis.heatmap), width="stretch")
        _small(HEATMAP_NOTE)
    if analysis.failed:
        _small("No marks to show for: " + ", ".join(analysis.failed) + " (the evaluation failed).")

    st.markdown(f"#### {SCATTER_HEADING}")
    if analysis.scatter is None:
        _small(NO_SCATTER_TEXT)
    else:
        st.plotly_chart(scatter_figure(analysis.scatter), width="stretch")
        _small(SCATTER_NOTE)
        if analysis.scatter.not_stated:
            _small("No total price stated, so no point: " + ", ".join(analysis.scatter.not_stated) + ".")

    st.markdown(f"#### {WHAT_IF_HEADING}")
    if analysis.what_if is None:
        _small(NO_WHAT_IF_TEXT)
    else:
        colours = {e.name: e.colour for e in analysis.entries}
        st.plotly_chart(what_if_figure(analysis.what_if, colours), width="stretch")
        _small(WHAT_IF_NOTE)
        st.markdown(f"**{_e(what_if_sentence(analysis.what_if))}**")
        st.markdown(what_if_table_html(analysis.what_if), unsafe_allow_html=True)
        if analysis.left_out:
            _small("Left out of this comparison because their marks do not cover the same criteria: " + ", ".join(analysis.left_out) + ".")


def _empty_state() -> None:
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. The score heatmap, price vs quality chart and the "
        "what-if weight chart will appear here."
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
    _show(build_analysis(run, suppliers))
