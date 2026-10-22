"""Tab 4: Scorecards, one supplier in detail (feature F11, spec section 13.3).

The reader picks a supplier and sees: the key facts from the proposal (price, warranty, delivery, battery, RAM and SSD),
the score against the best mark in the batch (a bar chart), every criterion with its score, benchmark, gap, relative %,
weight, evidence and justification, the industry comparison, the mandatory checks, and the warnings. Everything is read
back from the saved run, so what is shown is exactly what was stored. Nothing is calculated here: the benchmark, gap and
relative % were worked out by `tools/ranking.py` when the run was saved.

Not on this tab yet: the "evidence found" tick or cross (F15) and the pros and cons (F16).

The tables are small HTML tables, like the Overview's, because a Streamlit data table cannot colour a word or flag a
missing criterion. Every piece of text that comes from a proposal, from the model or from the buyer goes through
`html.escape`, and line breaks inside it are flattened, because a blank line would end the HTML block.
"""
from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from agents import evaluator
from db import repository
from tools import industry, validator
from ui import tab_overview, theme
from ui.tab_new_evaluation import INDUSTRY_HELP, PPI_HELP, SHOWN_RUN_KEY

PICKER_KEY_PREFIX = "sc_supplier_"      # the picker's widget key; the run id is added, so a new run starts on its own default
NOT_STATED_TEXT = "Not stated"
BEST_LABEL = "Best in this batch"
EVALUATION_FAILED_TEXT = "The evaluation of this supplier failed, so every score is 0. The warnings below say why."
NO_INDUSTRY_TEXT = "The industry comparison is not available for this run."
NO_CHECKS_TEXT = "No mandatory checks were recorded for this run."
NO_WARNINGS_TEXT = "No warnings for this supplier."
NO_CHART_TEXT = "There are no marks to draw for this supplier."
PASSED, NOT_PASSED = "Passed", "Not passed"
FLAG_NO_RESULT, FLAG_NOT_STATED = "No result returned", "Not stated"

BENCHMARK_HELP = "The highest mark any supplier received for this criterion in this run."
GAP_HELP = "This supplier's mark minus the benchmark. 0 means this supplier is the best on this criterion."
RELATIVE_HELP = "This supplier's mark as a percentage of the benchmark. If nobody scored above 0 it shows 100%."
WEIGHT_HELP = "How many of the 100 points this criterion is worth."
EVIDENCE_HELP = "A quote from the proposal that supports the mark, with its page."
POINTS_HELP = "Points for the grade: Above 100, Meets 75, Below 25, Not stated 0."
SHARE_HELP = "How many of the 100 industry points this attribute is worth."
CRITERIA_NOTE = ("A mark of 0 with \"Not stated in the proposal\" means the proposal does not address the criterion. "
                 "\"No result returned\" means the model gave no mark for it, so it was set to 0.")
INDUSTRY_NOTE = ("Above = 100 points, Meets = 75, Below = 25, Not stated = 0. "
                 "Baseline values are planning assumptions for a fictional startup, not market research.")

MUTED = tab_overview.MUTED
GRADE_COLOURS: dict[str, tuple[str, str]] = {   # text, background; the word is always printed too
    industry.ABOVE: theme.STRENGTH_COLOURS["Strong"],
    industry.MEETS: (theme.TEAL_TEXT, theme.TAB_SELECTED_BACKGROUND),
    industry.BELOW: theme.STRENGTH_COLOURS["Weak"],
    industry.NOT_STATED: (MUTED, "#F3F4F6"),
}
FLAG_COLOURS = theme.STRENGTH_COLOURS["Average"]
PASSED_COLOURS, NOT_PASSED_COLOURS = theme.STRENGTH_COLOURS["Strong"], theme.STRENGTH_COLOURS["Weak"]

# One line on purpose: Streamlit's markdown ends an HTML block at a blank line, so no line breaks inside the HTML.
_CSS = (
    ".sc-wrap{overflow-x:auto;margin:0.25rem 0 0.5rem 0}"
    f".sc-table{{width:100%;border-collapse:collapse;font-size:0.92rem;color:{theme.TEXT}}}"
    f".sc-table th{{background:{theme.TAB_SELECTED_BACKGROUND};color:{theme.HEADING};text-align:left;"
    f"padding:0.5rem 0.65rem;border-bottom:2px solid {theme.TEAL};white-space:nowrap;font-weight:600}}"
    f".sc-table td{{padding:0.5rem 0.65rem;border-bottom:1px solid {theme.BORDER};vertical-align:top}}"
    ".sc-table th[title]{cursor:help}"
    ".sc-num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}"
    ".sc-text{min-width:15rem}"
    f".sc-muted{{color:{MUTED};font-style:italic}}"
    ".sc-word{display:inline-block;padding:0.1rem 0.6rem;border-radius:999px;font-weight:600;font-size:0.85rem;white-space:nowrap}"
    ".sc-word[title]{cursor:help}"
    ".sc-flag{display:inline-block;margin-left:0.5rem;padding:0.05rem 0.5rem;border-radius:999px;font-size:0.75rem;font-weight:600;"
    f"color:{FLAG_COLOURS[0]};background:{FLAG_COLOURS[1]};white-space:nowrap}}"
    ".sc-pill{display:inline-block;margin-left:0.6rem;padding:0.05rem 0.6rem;border-radius:999px;font-size:0.8rem;font-weight:600;"
    f"color:#FFFFFF;background:{theme.PRIMARY};vertical-align:middle}}"
    f".sc-kicker{{color:{theme.TEAL_TEXT};font-size:0.8rem;font-weight:600;letter-spacing:0.06em;text-transform:uppercase}}"
    f".sc-name{{color:{theme.HEADING};font-size:1.7rem;font-weight:700;line-height:1.4}}"
    ".sc-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:0.6rem;margin:0.25rem 0 0.5rem 0}"
    f".sc-fact{{background:{theme.CARD_BACKGROUND};border:1px solid {theme.BORDER};border-top:3px solid {theme.TEAL};"
    "border-radius:8px;padding:0.55rem 0.8rem}"
    f".sc-fact-label{{color:{theme.TEAL_TEXT};font-size:0.75rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase}}"
    f".sc-fact-value{{color:{theme.HEADING};font-size:1.2rem;font-weight:700;line-height:1.35}}"
    f".sc-fact-value.sc-unstated{{color:{MUTED};font-size:1.05rem;font-weight:400;font-style:italic}}"
    f".sc-fact-detail{{color:{MUTED};font-size:0.82rem}}"
    f".sc-warn{{border-left:4px solid {FLAG_COLOURS[0]};background:{FLAG_COLOURS[1]};border-radius:6px;padding:0.5rem 1rem;color:{theme.TEXT}}}"
    ".sc-warn ul{margin:0.2rem 0;padding-left:1.1rem}"
)


# ---------------------------------------------------------------- what the tab shows, worked out from the saved run

@dataclass(frozen=True)
class KeyFact:
    label: str
    value: str | None       # None means the proposal does not say
    detail: str | None


@dataclass(frozen=True)
class Line:
    """One criterion of the scorecard."""

    criterion_id: int
    name: str
    score: float | None
    max_score: float | None
    benchmark: float | None
    gap: float | None
    relative_pct: float | None
    weight: float | None
    evidence: str
    justification: str
    flag: str | None        # FLAG_NO_RESULT, FLAG_NOT_STATED or None


@dataclass(frozen=True)
class Attribute:
    label: str
    grade: str
    points: float | None
    weight: float | None
    reason: str


@dataclass(frozen=True)
class Gate:
    name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class ChartData:
    names: tuple[str, ...]
    scores: tuple[float, ...]
    bests: tuple[float, ...]
    maxima: tuple[float, ...]


@dataclass(frozen=True)
class Scorecard:
    row: tab_overview.Row               # rank, name, the three scores, submission date, experience, eligibility
    count: int                          # suppliers in the run
    recommended: bool
    facts: tuple[KeyFact, ...]
    lines: tuple[Line, ...]
    attributes: tuple[Attribute, ...]
    gates: tuple[Gate, ...]
    warnings: tuple[str, ...]
    evaluation_failed: bool


@dataclass(frozen=True)
class Scorecards:
    run_line: str
    cards: tuple[Scorecard, ...]        # best rank first
    default_index: int                  # the recommended supplier, or the top-ranked one when nobody is eligible


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value else None


def _whole(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _flat(text: Any) -> str:
    """Text on one line: line breaks and runs of spaces become single spaces."""
    return " ".join(str(text).split())


def _plural(value: float, unit: str) -> str:
    return f"{value:g} {unit}" + ("" if value == 1 else "s")


def _price(value: float | None) -> str | None:
    return None if value is None else evaluator.format_rupees(value)


def key_facts(facts: dict[str, Any]) -> tuple[KeyFact, ...]:
    """The five facts at the top of a scorecard, from what the model read in the proposal."""
    total, unit = _number(facts.get("grand_total_rs")), _number(facts.get("unit_price_rs"))
    price = KeyFact("Total price", _price(total), None if unit is None else f"{_price(unit)} per laptop")

    years, cover = _number(facts.get("warranty_years")), facts.get("warranty_type")
    cover_text = {"on-site": "On-site cover", "carry-in": "Carry-in only"}.get(cover) if isinstance(cover, str) else None
    warranty = KeyFact("Warranty", None if years is None else _plural(years, "year"),
                       cover_text or ("Type of cover not stated" if years is not None else None))

    weeks = _number(facts.get("delivery_weeks"))
    delivery = KeyFact("Delivery", None if weeks is None else _plural(weeks, "week"), None)

    hours, measured = _number(facts.get("battery_hours")), facts.get("battery_measured")
    how = "Measured" if measured is True else "Claimed, not measured" if measured is False else "Not stated whether measured"
    battery = KeyFact("Battery", None if hours is None else _plural(hours, "hour"), how if hours is not None else None)

    ram, ssd = _number(facts.get("ram_gb")), _number(facts.get("ssd_gb"))
    parts = ([f"{ram:g} GB RAM"] if ram is not None else []) + ([f"{ssd:g} GB SSD"] if ssd is not None else [])
    missing = "SSD not stated" if ssd is None and ram is not None else "RAM not stated" if ram is None and ssd is not None else None
    memory = KeyFact("RAM / SSD", " / ".join(parts) or None, missing)
    return (price, warranty, delivery, battery, memory)


def _line(item: dict[str, Any]) -> Line:
    criterion_id = _whole(item.get("criterion_id"))
    evidence, justification = _flat(item.get("evidence") or ""), _flat(item.get("justification") or "")
    if item.get("filled_by_validator") is True:
        flag: str | None = FLAG_NO_RESULT
    elif evidence.lower() == validator.NOT_STATED.lower():
        flag = FLAG_NOT_STATED
    else:
        flag = None
    return Line(criterion_id, str(item.get("name") or f"Criterion {criterion_id}"), _number(item.get("score")),
                _number(item.get("max_score")), _number(item.get("benchmark")), _number(item.get("gap")),
                _number(item.get("relative_pct")), _number(item.get("weight")), evidence, justification, flag)


def _lines(result: dict[str, Any]) -> tuple[Line, ...]:
    return tuple(_line(item) for item in result.get("criteria") or [] if isinstance(item, dict))


def _attributes(result: dict[str, Any]) -> tuple[Attribute, ...]:
    breakdown = result.get("industry_breakdown")
    items = breakdown.get("attributes") if isinstance(breakdown, dict) else None
    return tuple(Attribute(str(a.get("label") or a.get("attribute_key") or "Attribute"), str(a.get("grade") or industry.NOT_STATED),
                           _number(a.get("points")), _number(a.get("weight")), _flat(a.get("reason") or ""))
                 for a in items or [] if isinstance(a, dict))


def _gates(result: dict[str, Any]) -> tuple[Gate, ...]:
    return tuple(Gate(str(g.get("name") or "Check"), g.get("passed") is True, _flat(g.get("reason") or ""))
                 for g in result.get("gate_details") or [] if isinstance(g, dict))


def _warnings(result: dict[str, Any]) -> tuple[str, ...]:
    items = result.get("warnings")
    return tuple(_flat(w) for w in (items if isinstance(items, (list, tuple)) else []) if isinstance(w, str) and _flat(w))


def chart_data(lines: tuple[Line, ...]) -> ChartData | None:
    """The marks to draw (the supplier's mark and the best mark per criterion), or None when no criterion has both."""
    usable = [line for line in lines if line.score is not None and line.benchmark is not None and line.max_score is not None]
    if not usable:
        return None
    return ChartData(tuple(line.name for line in usable), tuple(line.score for line in usable),   # type: ignore[misc]
                     tuple(line.benchmark for line in usable), tuple(line.max_score for line in usable))   # type: ignore[misc]


def build_scorecards(run: repository.RunRecord, suppliers: list[repository.StoredSupplier]) -> Scorecards:
    """Work out everything the tab shows from a saved run. No Streamlit and no database access here."""
    overview = tab_overview.build_overview(run, suppliers)
    by_name = {s.supplier_name: s for s in suppliers}
    recommended = overview.recommended.name if overview.recommended else None
    cards = tuple(
        Scorecard(row=row, count=len(overview.rows), recommended=row.name == recommended,
                  facts=key_facts(by_name[row.name].result.get("extracted_facts") or {}), lines=_lines(by_name[row.name].result),
                  attributes=_attributes(by_name[row.name].result), gates=_gates(by_name[row.name].result),
                  warnings=_warnings(by_name[row.name].result), evaluation_failed=bool(by_name[row.name].result.get("evaluation_failed")))
        for row in overview.rows)
    default = next((i for i, card in enumerate(cards) if card.recommended), 0)
    return Scorecards(overview.run_line, cards, default)


def picker_label(card: Scorecard) -> str:
    return f"{card.row.rank}. {card.row.name}"


# ---------------------------------------------------------------- the HTML pieces

def _e(text: Any) -> str:
    return html.escape(_flat(text), quote=True)


def _title(text: str) -> str:
    return html.escape(text, quote=True).replace("\n", "&#10;")


def _word(text: str, colours: tuple[str, str], hover: str | None = None) -> str:
    title = f' title="{_title(hover)}"' if hover else ""
    return f'<span class="sc-word" style="color:{colours[0]};background:{colours[1]}"{title}>{_e(text)}</span>'


def _th(name: str, hover: str | None = None, number: bool = False) -> str:
    alignment = ' class="sc-num"' if number else ""
    title = f' title="{_title(hover)}"' if hover else ""
    return f"<th{alignment}{title}>{_e(name)}</th>"


def _table(head: str, body: str) -> str:
    return (f"<style>{_CSS}</style><div class=\"sc-wrap\"><table class=\"sc-table\"><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def header_html(card: Scorecard) -> str:
    """The supplier's name with its rank, a Recommended pill when it is the pick, and the Eligible / Not eligible badge."""
    row = card.row
    pill = '<span class="sc-pill">Recommended</span>' if card.recommended else ""
    colours = tab_overview.ELIGIBLE_COLOURS if row.eligible else tab_overview.NOT_ELIGIBLE_COLOURS
    word = tab_overview.ELIGIBLE if row.eligible else tab_overview.NOT_ELIGIBLE
    hover = None if row.eligible else "\n".join(row.why_not)
    badge = f' <span style="margin-left:0.6rem;vertical-align:middle">{_word(word, colours, hover)}</span>'
    return (f"<style>{_CSS}</style>"
            f'<div class="sc-kicker">Supplier scorecard · Ranked {row.rank} of {card.count}</div>'
            f'<div class="sc-name">{_e(row.name)}{pill}{badge}</div>')


def facts_html(facts: tuple[KeyFact, ...]) -> str:
    """The key-facts strip: one tile per fact, 'Not stated' in grey italics when the proposal does not say."""
    tiles = []
    for fact in facts:
        value = (f'<div class="sc-fact-value">{_e(fact.value)}</div>' if fact.value is not None
                 else f'<div class="sc-fact-value sc-unstated">{NOT_STATED_TEXT}</div>')
        detail = f'<div class="sc-fact-detail">{_e(fact.detail)}</div>' if fact.detail else ""
        tiles.append(f'<div class="sc-fact"><div class="sc-fact-label">{_e(fact.label)}</div>{value}{detail}</div>')
    return f'<style>{_CSS}</style><div class="sc-facts">{"".join(tiles)}</div>'


def _marks(score: float | None, maximum: float | None) -> str:
    if score is None:
        return "n/a"
    return f"{score:g} / {maximum:g}" if maximum is not None else f"{score:g}"


def _gap(gap: float | None) -> str:
    return "n/a" if gap is None else "0" if gap == 0 else f"{gap:g}"


def _plain(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:g}{suffix}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _text_cell(text: str, empty: str) -> str:
    if not text:
        return f'<td class="sc-text"><span class="sc-muted">{empty}</span></td>'
    if text.lower() == validator.NOT_STATED.lower():
        return f'<td class="sc-text"><span class="sc-muted">{_e(text)}</span></td>'
    return f'<td class="sc-text">{_e(text)}</td>'


def criteria_html(lines: tuple[Line, ...]) -> str:
    """One row per criterion: score, benchmark, gap, relative %, weight, evidence, justification."""
    head = (_th("Criterion") + _th("Score", number=True) + _th("Benchmark", BENCHMARK_HELP, True) + _th("Gap", GAP_HELP, True)
            + _th("Relative %", RELATIVE_HELP, True) + _th("Weight", WEIGHT_HELP, True) + _th("Evidence", EVIDENCE_HELP)
            + _th("Justification"))
    body = []
    for line in lines:
        flag = f'<span class="sc-flag">{_e(line.flag)}</span>' if line.flag else ""
        body.append(
            f'<tr><td><strong>{_e(line.name)}</strong>{flag}</td>'
            f'<td class="sc-num">{_marks(line.score, line.max_score)}</td><td class="sc-num">{_plain(line.benchmark)}</td>'
            f'<td class="sc-num">{_gap(line.gap)}</td><td class="sc-num">{_percent(line.relative_pct)}</td>'
            f'<td class="sc-num">{_plain(line.weight)}</td>'
            f'{_text_cell(line.evidence, "None given")}{_text_cell(line.justification, "None given")}</tr>')
    return _table(head, "".join(body))


def industry_html(attributes: tuple[Attribute, ...]) -> str:
    """One row per industry attribute: the grade (word and colour), its points, its share of the industry score, and why."""
    head = _th("Attribute") + _th("Grade") + _th("Points", POINTS_HELP, True) + _th("Weight", SHARE_HELP, True) + _th("Why")
    body = []
    for a in attributes:
        colours = GRADE_COLOURS.get(a.grade, GRADE_COLOURS[industry.NOT_STATED])
        body.append(f'<tr><td><strong>{_e(a.label)}</strong></td><td>{_word(a.grade, colours)}</td>'
                    f'<td class="sc-num">{_plain(a.points)}</td><td class="sc-num">{_plain(a.weight)}</td>{_text_cell(a.reason, "")}</tr>')
    return _table(head, "".join(body))


def gates_html(gates: tuple[Gate, ...]) -> str:
    """One row per mandatory check: Passed or Not passed (always the word), and the reason."""
    head = _th("Check") + _th("Result") + _th("Why")
    body = "".join(
        f'<tr><td><strong>{_e(g.name)}</strong></td><td>{_word(PASSED if g.passed else NOT_PASSED, PASSED_COLOURS if g.passed else NOT_PASSED_COLOURS)}</td>'
        f'{_text_cell(g.reason, "")}</tr>' for g in gates)
    return _table(head, body)


def warnings_html(warnings: tuple[str, ...]) -> str:
    items = "".join(f"<li>{_e(w)}</li>" for w in warnings)
    return f'<style>{_CSS}</style><div class="sc-warn"><ul>{items}</ul></div>'


# ---------------------------------------------------------------- the chart

def score_vs_best_figure(supplier: str, data: ChartData) -> go.Figure:
    """Horizontal bars, one pair per criterion: this supplier's mark (teal) against the best mark in the batch (grey)."""
    figure = go.Figure()
    for name, values, colour in ((supplier, data.scores, theme.SCORE_SUPPLIER), (BEST_LABEL, data.bests, theme.SCORE_BEST)):
        figure.add_trace(go.Bar(
            name=name, y=list(data.names), x=list(values), orientation="h", marker={"color": colour},
            text=[f"{v:g}" for v in values], textposition="outside", cliponaxis=False,
            customdata=list(data.maxima), hovertemplate="%{y}<br>%{x:g} of %{customdata:g}"))
    figure.update_layout(
        barmode="group", height=110 + 64 * len(data.names), margin={"l": 10, "r": 30, "t": 30, "b": 10},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font={"color": theme.TEXT},
        legend={"orientation": "h", "x": 0, "y": 1.1},
        xaxis={"range": [0, max(data.maxima) * 1.08], "title": {"text": "Marks"}, "gridcolor": theme.BORDER, "zeroline": False},
        yaxis={"autorange": "reversed", "automargin": True})
    return figure


def card_figure(card: Scorecard) -> go.Figure | None:
    """The chart for one scorecard, or None when none of its criteria has a mark to draw."""
    data = chart_data(card.lines)
    return None if data is None else score_vs_best_figure(card.row.name, data)


# ---------------------------------------------------------------- drawing

def _note(text: str) -> None:
    """A line of small grey text. `text` is plain text (it is escaped here), never HTML."""
    st.markdown(f'<p style="color:{MUTED};font-size:0.9rem;margin:0.25rem 0">{_e(text)}</p>', unsafe_allow_html=True)


def _show_card(card: Scorecard) -> None:
    row = card.row
    st.markdown(header_html(card), unsafe_allow_html=True)
    _note(f"Submitted {row.submitted} · Experience rating: {row.experience}")
    if card.evaluation_failed:
        st.error(EVALUATION_FAILED_TEXT)
    overall, ppi, industry_score = st.columns(3)
    overall.metric("Overall /100", f"{row.overall:.2f}", help=tab_overview.OVERALL_HELP)
    ppi.metric("Peer score (PPI)", f"{row.ppi:.2f}", help=PPI_HELP)
    industry_score.metric("Industry score", f"{row.industry:.2f}", help=INDUSTRY_HELP)

    st.markdown("#### Key facts")
    st.markdown(facts_html(card.facts), unsafe_allow_html=True)

    st.markdown("#### Score vs best")
    figure = card_figure(card)
    if figure is None:
        st.info(NO_CHART_TEXT)
    else:
        st.plotly_chart(figure, width="stretch")

    st.markdown("#### Criterion by criterion")
    if card.lines:
        st.markdown(criteria_html(card.lines), unsafe_allow_html=True)
        _note(CRITERIA_NOTE)
    else:
        st.info("No criterion results were saved for this supplier.")

    st.markdown("#### Industry comparison")
    if card.attributes:
        st.markdown(industry_html(card.attributes), unsafe_allow_html=True)
        _note(f"Industry score {row.industry:.2f} out of 100. {INDUSTRY_NOTE}")
    else:
        st.info(NO_INDUSTRY_TEXT)

    st.markdown("#### Mandatory checks")
    if card.gates:
        st.markdown(gates_html(card.gates), unsafe_allow_html=True)
    else:
        st.info(NO_CHECKS_TEXT)

    st.markdown("#### Warnings")
    if card.warnings:
        st.markdown(warnings_html(card.warnings), unsafe_allow_html=True)
    else:
        _note(NO_WARNINGS_TEXT)


def _show(run_id: str, scorecards: Scorecards) -> None:
    labels = [picker_label(card) for card in scorecards.cards]
    st.markdown(f'<p style="color:{MUTED};font-size:0.9rem;margin:0.25rem 0">{_e(scorecards.run_line)}</p>', unsafe_allow_html=True)
    choice = st.selectbox("Supplier", labels, index=scorecards.default_index, key=f"{PICKER_KEY_PREFIX}{run_id}")
    index = labels.index(choice) if choice in labels else scorecards.default_index
    _show_card(scorecards.cards[index])


def _empty_state() -> None:
    st.info(
        "No evaluation yet. Open **New evaluation**, upload the supplier PDFs and click "
        "**Evaluate suppliers**. Each supplier's scores, evidence and key facts "
        "will appear here."
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
    _show(run_id, build_scorecards(run, suppliers))
