"""Tab 3: Overview, the manager's view (feature F10, spec sections 10.2 and 13.3; made readable by change pack R1a).

Shows the run that was just evaluated (or, later, one opened from the history), answer first: a verdict banner (who is
recommended and the catches, in a sentence and up to four pills), a grid of the must-haves each supplier met, the
Strong / Average / Weak table, and a card for each of the top three with what is good and what to watch. Every number
that used to be on this tab (run line, the three scores, the full leaderboard, tie-break notes, the runner-up and the
industry warning) is one click away under "Show details". Everything is read back from the saved run, so what is
shown is exactly what was stored. Which supplier is recommended, and which pros and cons are chosen, is decided in
`tools/insights.py`; this file only reads, words the verdict and draws.

Not on this tab yet: the LLM wording of pros and cons (F16) and the price vs budget bar (F17).

The tables, the banner and the cards are small pieces of HTML, because Streamlit's own widgets cannot show a coloured
badge with a hover text. Every piece of text that comes from a proposal or from the buyer goes through `html.escape`.
"""
from __future__ import annotations

import html
import math
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
HOVER_NOTE = "Hover over a Not eligible badge to see which must-have was not met."
STRENGTH_NOTE = ("Strong: 80% or more of the maximum mark. Average: 50% to under 80%. Weak: under 50%. "
                 "The score for each criterion is in brackets.")
EVALUATION_FAILED_TEXT = "The evaluation failed (see the warnings on the Scorecards tab)."
CHECK_FAILED_TEXT = "A must-have was not met."
NOT_ELIGIBLE, ELIGIBLE = "Not eligible", "Eligible"      # ELIGIBLE is still what the Scorecards header badge prints
MEETS_MUST_HAVES = "Meets must-haves"                     # what the badge on this tab prints for an eligible supplier

DETAILS_LABEL = "Show details (full leaderboard, scores out of 100, tie-breaks)"
MUSTHAVES_NOTE = "Hover over a \u2713 or \u2717 to see the reason."
NO_MUSTHAVES_TEXT = "No must-haves were checked in this run."
PPI_NAME = "Score vs other bids"
EXPLAIN_OVERALL = f"Overall /100: {OVERALL_HELP[0].lower()}{OVERALL_HELP[1:]}"
EXPLAIN_PPI = f"{PPI_NAME} (PPI): {PPI_HELP[0].lower()}{PPI_HELP[1:]} This decides the ranking."
EXPLAIN_INDUSTRY = f"Industry score: {INDUSTRY_HELP[0].lower()}{INDUSTRY_HELP[1:]}"
NOTHING_STANDS_OUT = "Nothing stands out against the other bids."
NOTHING_TO_SHOW = "No strengths or weaknesses to show because the evaluation failed."
EVALUATION_FAILED_PROBLEM = "the evaluation failed"
MUST_HAVE_NOT_MET_PROBLEM = "a must-have was not met"
CARD_PROS, CARD_CONS = 2, 1           # what each "Why the top three" card shows
TOP_CARDS = 3
MAX_PILLS = 4
MAX_ATTRIBUTES_IN_PILL = 2

ELIGIBLE_COLOURS = ("#2E7D32", "#E8F5E9")      # text, background (same pair as a Strong cell); the word is always printed
NOT_ELIGIBLE_COLOURS = ("#C62828", "#FFEBEE")
MUTED = "#4B5563"

GOOD, WARN, BAD = "good", "warn", "bad"          # the tone of a pill or of the banner
SYMBOLS = {GOOD: "\u2713", WARN: "\u26a0", BAD: "\u2717"}
TONE_COLOURS = {GOOD: theme.PILL_GOOD, WARN: theme.PILL_WARN, BAD: theme.PILL_BAD}
TONE_EDGES = {GOOD: theme.EDGE_GOOD, WARN: theme.EDGE_WARN, BAD: theme.EDGE_BAD}

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
    f".ov-why{{margin-left:0.4rem;font-size:0.85rem;color:{MUTED}}}"
    # verdict banner and its pills
    f".ov-banner{{background:{theme.CARD_BACKGROUND};border:1px solid {theme.BORDER};border-left-width:8px;border-radius:0.75rem;"
    "padding:0.9rem 1.2rem;margin:0.25rem 0 0.5rem 0;box-shadow:0 1px 3px rgba(11,79,74,0.08)}"
    f".ov-banner-head{{color:{theme.HEADING};font-size:1.55rem;font-weight:400;line-height:1.3}}"
    ".ov-banner-name{font-weight:700}"
    f".ov-banner-text{{color:{theme.TEXT};font-size:1.05rem;margin:0.3rem 0 0.6rem 0}}"
    ".ov-chip{display:inline-block;margin:0 0.4rem 0.3rem 0;padding:0.15rem 0.7rem;border-radius:999px;font-weight:600;font-size:0.9rem}"
    # the must-haves grid
    ".ov-mark{text-align:center;font-weight:700;font-size:1.05rem;cursor:help}"
    # the "Why the top three" cards
    ".ov-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));gap:0.75rem;margin:0.25rem 0 0.5rem 0}"
    f".ov-card{{background:{theme.CARD_BACKGROUND};border:1px solid {theme.BORDER};border-radius:0.75rem;padding:0.8rem 1rem}}"
    f".ov-card-name{{color:{theme.HEADING};font-size:1.1rem;font-weight:700}}"
    f".ov-card-sub{{color:{theme.TEXT};font-size:0.9rem;margin:0.1rem 0 0.45rem 0}}"
    ".ov-card-sub [title]{cursor:help}"
    f".ov-card-flag{{display:inline-block;color:{theme.PILL_BAD[0]};background:{theme.PILL_BAD[1]};font-weight:600;font-size:0.85rem;"
    "border-radius:0.4rem;padding:0.1rem 0.5rem;margin-bottom:0.4rem}"
    ".ov-card ul{list-style:none;margin:0;padding:0}"
    f".ov-card li{{color:{theme.TEXT};font-size:0.92rem;margin:0.25rem 0}}"
    ".ov-ico{display:inline-block;width:1.25rem;font-weight:700}"
    f".ov-basis{{color:{MUTED};font-size:0.8rem}}"
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
class GateCell:
    """One must-have for one supplier, as saved with the run."""

    gate_id: str
    name: str
    label: str          # the short column name ("3-yr warranty")
    passed: bool        # True only when the saved value is exactly True
    reason: str


@dataclass(frozen=True)
class MustHave:
    """A column of the must-haves grid."""

    gate_id: str
    label: str
    name: str


@dataclass(frozen=True)
class Row:
    """One supplier, as the verdict, the grid, the cards and the leaderboard need it."""

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
    evaluation_failed: bool = False
    problems: tuple[str, ...] = ()      # empty when eligible; otherwise what is wrong in a few plain words ("no price list")
    gates: tuple[GateCell, ...] = ()
    price: insights.PriceFacts = insights.PriceFacts(None, None, None, None)
    pros: tuple[insights.Reason, ...] = ()
    cons: tuple[insights.Reason, ...] = ()

    @property
    def short_reason(self) -> str:
        return ", ".join(self.problems)


@dataclass(frozen=True)
class Pill:
    text: str
    tone: str           # GOOD, WARN or BAD: sets the colour and the symbol in front of the text


@dataclass(frozen=True)
class Verdict:
    """The banner at the top: the answer in a heading, one sentence and up to four pills."""

    tone: str
    lead: str                           # the heading up to the supplier's name
    name: str | None                    # the supplier's name (shown bold after the lead), or None
    sentence: str
    pills: tuple[Pill, ...]


@dataclass(frozen=True)
class Overview:
    run_line: str
    rows: tuple[Row, ...]
    recommendation: insights.Recommendation
    recommended: Row | None
    runner_up: Row | None
    top_ranked: Row | None
    industry_warning: str | None
    criteria: tuple[tuple[int, str, float | None], ...]    # (criterion_id, name, weight), in marking-scheme order
    verdict: Verdict
    budget_line: str | None             # "Budget for this run: Rs 60 lakh for 100 laptops", or None for a run saved without one
    musthaves: tuple[MustHave, ...]     # the columns of the must-haves grid


def _number(value: Any) -> float | None:
    """A real, finite number, or None. True/False, text, NaN and infinity are not numbers."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _flat(text: Any) -> str:
    """Text on one line: line breaks and runs of spaces become single spaces."""
    return " ".join(str(text).split())


def _whole(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


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


def _items(value: Any) -> list[Any]:
    """The entries of a saved list; anything that is not a list (hand-edited data) counts as empty."""
    return list(value) if isinstance(value, (list, tuple)) else []


def _gate_cells(result: dict[str, Any]) -> tuple[GateCell, ...]:
    cells: list[GateCell] = []
    for item in _items(result.get("gate_details")):
        if not isinstance(item, dict):
            continue
        name = _flat(item.get("name") or "Check")
        gate_id = str(item.get("gate_id") or name)
        cells.append(GateCell(gate_id, name, insights.gate_label(gate_id, name), item.get("passed") is True,
                              _flat(item.get("reason") or "")))
    return tuple(cells)


def _problems(stored: repository.StoredSupplier, cells: tuple[GateCell, ...]) -> tuple[str, ...]:
    """What is wrong with a supplier that is not eligible, in a few plain words each."""
    if stored.gates_passed:
        return ()
    if stored.result.get("evaluation_failed"):
        return (EVALUATION_FAILED_PROBLEM,)
    failed = tuple(insights.gate_problem(c.gate_id, c.name, c.reason) for c in cells if not c.passed)
    return failed or (MUST_HAVE_NOT_MET_PROBLEM,)


def _criterion_results(result: dict[str, Any]) -> tuple[insights.CriterionResult, ...]:
    """The criteria that have a score, a maximum and a weight. Anything else in the saved run is left out, never guessed."""
    found: list[insights.CriterionResult] = []
    for item in _items(result.get("criteria")):
        if not isinstance(item, dict):
            continue
        score, maximum, weight = _number(item.get("score")), _number(item.get("max_score")), _number(item.get("weight"))
        if score is None or maximum is None or weight is None:
            continue
        criterion_id = _whole(item.get("criterion_id"))
        evidence_found = item.get("evidence_found")
        found.append(insights.CriterionResult(
            criterion_id, _flat(item.get("name") or f"Criterion {criterion_id}"), score, maximum, weight,
            benchmark=_number(item.get("benchmark")), gap=_number(item.get("gap")), relative_pct=_number(item.get("relative_pct")),
            filled_by_validator=item.get("filled_by_validator") is True,
            evidence_found=evidence_found if isinstance(evidence_found, bool) else None))
    return tuple(found)


def _row(stored: repository.StoredSupplier, budget_rs: float | None = None) -> Row:
    result = stored.result
    cells = _gate_cells(result)
    failed = bool(result.get("evaluation_failed"))
    if failed:
        chosen = insights.ProsCons((), ())     # a failed evaluation has no marks worth praising or criticising
    else:
        chosen = insights.select_pros_cons(
            _criterion_results(result), tuple(insights.GateOutcome(c.gate_id, c.name, c.passed, c.reason) for c in cells))
    return Row(
        rank=stored.final_rank, name=stored.supplier_name, overall=stored.absolute_score, ppi=stored.ppi,
        industry=stored.industry_score, submitted=orchestrator.format_submitted(stored.submission_date),
        experience=orchestrator.format_experience(stored.experience_rating), eligible=stored.gates_passed,
        why_not=_why_not(stored), weak_attributes=_weak_attributes(result),
        tie_break=result.get("tie_break_explanation") or None, strengths=_strengths(result),
        evaluation_failed=failed, problems=_problems(stored, cells), gates=cells,
        price=insights.price_facts(result.get("extracted_facts"), budget_rs), pros=chosen.pros, cons=chosen.cons)


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


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _budget_line(run: repository.RunRecord) -> str | None:
    budget = _positive(run.budget_rs)
    if budget is None:
        return None
    line = f"Budget for this run: {insights.format_lakh(budget)}"
    if run.quantity:
        line += f" for {run.quantity} laptop{'' if run.quantity == 1 else 's'}"
    return line


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _price_clause(price: insights.PriceFacts) -> tuple[str, Pill | None]:
    """How the price ends the sentence, and the pill for it. Nothing to say when the run has no budget and the price is stated."""
    if price.total_rs is None:
        return ", but its price is not stated", Pill("Price not stated", WARN)
    if price.within_budget is None or price.budget_rs is None:
        return "", None
    if price.within_budget:
        return f" and fits the {insights.format_lakh(price.budget_rs)} budget", Pill("Within budget", GOOD)
    over = insights.format_lakh(price.over_by_rs or 0)
    return f", but is {over} over the {insights.format_lakh(price.budget_rs)} budget", Pill(f"Over budget by {over}", WARN)


def _industry_pill(attributes: tuple[str, ...] | None) -> Pill | None:
    """The short form of the industry warning (the full one is under Show details). None means there is no warning."""
    if attributes is None:
        return None
    if not attributes:
        return Pill("Below typical deals on several attributes", WARN)
    shown = attributes[:MAX_ATTRIBUTES_IN_PILL]
    more = len(attributes) - len(shown)
    return Pill(f"Below typical deals on: {', '.join(shown)}" + (f" (+{more} more)" if more else ""), WARN)


def build_verdict(rows: tuple[Row, ...], recommended: Row | None, top: Row | None,
                  warning_attributes: tuple[str, ...] | None) -> Verdict:
    """The banner at the top, worded from facts that are true of the saved run and nothing else.

    `warning_attributes` are the weak industry attributes of the recommended supplier when it gets the industry warning,
    and None when it does not.
    """
    count = len(rows)
    if recommended is not None:
        higher = [r.name for r in rows if r.rank < recommended.rank]
        lead = f"Ranked {recommended.rank} of {count}"
        if higher:
            verb = "ranks higher but is" if len(higher) == 1 else "rank higher but are"
            lead += f" ({_join_names(higher)} {verb} not eligible)"
        clause, price_pill = _price_clause(recommended.price)
        sentence = f"{lead}, meets every must-have{clause}." if clause else f"{lead} and meets every must-have."
        pills = [Pill(f"Ranked {recommended.rank} of {count}", GOOD if not higher else WARN), Pill("Meets every must-have", GOOD)]
        pills += [pill for pill in (price_pill, _industry_pill(warning_attributes)) if pill is not None]
        tone = GOOD if all(pill.tone == GOOD for pill in pills) else WARN
        return Verdict(tone, "We recommend ", recommended.name, sentence, tuple(pills[:MAX_PILLS]))
    if top is not None:
        sentence = f"{top.name} is ranked {top.rank} but is not eligible: {top.short_reason}."
        pills = tuple(Pill(_capital(problem), BAD) for problem in top.problems[:MAX_PILLS])
        return Verdict(BAD, "No supplier meets every must-have", None, sentence, pills)
    return Verdict(WARN, "Nothing to recommend", None, "No supplier results were found in this run.", ())


def build_overview(run: repository.RunRecord, suppliers: list[repository.StoredSupplier]) -> Overview:
    """Work out everything the tab shows from a saved run. No Streamlit and no database access here."""
    budget = _positive(run.budget_rs)
    rows = tuple(sorted((_row(s, budget) for s in suppliers), key=lambda r: r.rank))
    recommendation = insights.choose_recommendation([insights.Contender(r.name, r.rank, r.eligible) for r in rows])
    by_name = {r.name: r for r in rows}
    recommended = by_name.get(recommendation.recommended or "")
    runner_up = by_name.get(recommendation.runner_up or "")
    top = by_name.get(recommendation.top_ranked or "")
    warning = insights.industry_warning(recommended.industry, recommended.weak_attributes) if recommended else None
    criteria = tuple((int(c.get("criterion_id", 0)), str(c.get("name", "")), _number(c.get("weight"))) for c in run.criteria_snapshot)
    if not criteria and rows:
        criteria = tuple((s.criterion_id, s.name, s.weight) for s in rows[0].strengths)   # a run saved without its snapshot
    columns: dict[str, MustHave] = {}
    for row in rows:
        for cell in row.gates:
            columns.setdefault(cell.gate_id, MustHave(cell.gate_id, cell.label, cell.name))
    verdict = build_verdict(rows, recommended, top, (recommended.weak_attributes if warning else None) if recommended else None)
    return Overview(_run_line(run, len(rows)), rows, recommendation, recommended, runner_up, top, warning, criteria,
                    verdict, _budget_line(run), tuple(columns.values()))


# ---------------------------------------------------------------- the two small HTML tables

def _e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def _title(text: str) -> str:
    """A hover text for an HTML attribute, on one line of HTML (line breaks become &#10;)."""
    return _e(text).replace("\n", "&#10;")


def _badge(row: Row) -> str:
    """"Meets must-haves", or "Not eligible" with the failed checks on hover and the short reason beside it."""
    text, (colour, background) = (MEETS_MUST_HAVES, ELIGIBLE_COLOURS) if row.eligible else (NOT_ELIGIBLE, NOT_ELIGIBLE_COLOURS)
    hover = "" if row.eligible else f' title="{_title(chr(10).join(row.why_not))}"'
    reason = "" if row.eligible or not row.short_reason else f'<span class="ov-why">{_e(row.short_reason)}</span>'
    return f'<span class="ov-badge" style="color:{colour};background:{background}"{hover}>{text}</span>{reason}'


def leaderboard_html(rows: tuple[Row, ...], recommended: str | None) -> str:
    """The leaderboard: Rank, Supplier, Overall /100, Score vs other bids (PPI), Industry score, Submitted, Experience, Eligible."""
    headers = (("Rank", None, False), ("Supplier", None, False), ("Overall /100", OVERALL_HELP, True),
               (f"{PPI_NAME} (PPI)", PPI_HELP, True), ("Industry score", INDUSTRY_HELP, True),
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
        f'<span style="color:{theme.TEXT};font-size:1rem">Ranked {row.rank} \u00b7 {PPI_NAME} {row.ppi:.2f}</span></div>')


def verdict_html(verdict: Verdict) -> str:
    """The banner: a coloured edge, the answer as a heading, one sentence and the pills (symbol + words, never colour alone)."""
    if verdict.name is None:
        lead = f'<span class="ov-banner-name">{_e(verdict.lead)}</span>'
    else:
        lead = f'{_e(verdict.lead)}<span class="ov-banner-name">{_e(verdict.name)}</span>'
    chips = "".join(
        f'<span class="ov-chip" style="color:{TONE_COLOURS[p.tone][0]};background:{TONE_COLOURS[p.tone][1]}">'
        f'{SYMBOLS[p.tone]} {_e(p.text)}</span>' for p in verdict.pills)
    sentence = f'<div class="ov-banner-text">{_e(verdict.sentence)}</div>' if verdict.sentence else ""
    return (f'<style>{_CSS}</style><div class="ov-banner" style="border-left-color:{TONE_EDGES[verdict.tone]}">'
            f'<div class="ov-banner-head">{lead}</div>{sentence}<div class="ov-pills">{chips}</div></div>')


def _mark_cell(passed: bool | None, reason: str) -> str:
    if passed is None:
        return f'<td class="ov-mark" title="{_title("Not checked")}" style="color:{MUTED}">\u2013</td>'
    colour, background = theme.PILL_GOOD if passed else theme.PILL_BAD
    symbol = SYMBOLS[GOOD] if passed else SYMBOLS[BAD]
    return f'<td class="ov-mark" title="{_title(reason)}" style="color:{colour};background:{background}">{symbol}</td>'


def musthaves_html(rows: tuple[Row, ...], columns: tuple[MustHave, ...], recommended: str | None) -> str:
    """One row per supplier (best rank first), one column per must-have: a green tick or a red cross, the reason on hover."""
    head = "<th>Supplier</th>" + "".join(
        f'<th class="ov-strength" title="{_title(c.name)}">{_e(c.label)}</th>' for c in columns)
    body: list[str] = []
    for row in rows:
        by_id = {cell.gate_id: cell for cell in row.gates}
        cells: list[str] = []
        for column in columns:
            if row.evaluation_failed:
                cells.append(_mark_cell(False, "Evaluation failed"))
                continue
            cell = by_id.get(column.gate_id)
            cells.append(_mark_cell(None, "") if cell is None else _mark_cell(cell.passed, cell.reason or ("Passed" if cell.passed else "Not met")))
        pill = '<span class="ov-pill">Recommended</span>' if row.name == recommended else ""
        body.append(f"<tr><td><strong>{_e(row.name)}</strong>{pill}</td>{''.join(cells)}</tr>")
    return (f"<style>{_CSS}</style><div class=\"ov-wrap\"><table class=\"ov-table\"><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table></div>")


def _card_items(row: Row) -> list[tuple[str, insights.Reason]]:
    """Up to two good points, then one thing to watch. A failed must-have is not repeated here: the card's red flag says it."""
    watch = [c for c in row.cons if c.kind != insights.KIND_GATE][:CARD_CONS]
    return [(GOOD, r) for r in row.pros[:CARD_PROS]] + [(WARN, r) for r in watch]


def card_html(row: Row, recommended: bool) -> str:
    pill = '<span class="ov-pill">Recommended</span>' if recommended else ""
    flag = "" if row.eligible else f'<div class="ov-card-flag">Not eligible: {_e(row.short_reason)}</div>'
    items = "".join(
        f'<li><span class="ov-ico" style="color:{TONE_COLOURS[tone][0]}">{SYMBOLS[tone]}</span>{_e(reason.text)}'
        f'<span class="ov-basis"> \u00b7 {_e(reason.basis)}</span></li>' for tone, reason in _card_items(row))
    if not items:
        items = f'<li class="ov-basis">{_e(NOTHING_TO_SHOW if row.evaluation_failed else NOTHING_STANDS_OUT)}</li>'
    return (f'<div class="ov-card"><div class="ov-card-name">{_e(row.name)}{pill}</div>'
            f'<div class="ov-card-sub">Ranked {row.rank} \u00b7 <span title="{_title(PPI_HELP)}">{PPI_NAME}</span> {row.ppi:.2f}</div>'
            f'{flag}<ul>{items}</ul></div>')


def cards_html(rows: tuple[Row, ...], recommended: str | None) -> str:
    """A card for each of ranks 1 to 3 (fewer when there are fewer suppliers)."""
    cards = "".join(card_html(row, row.name == recommended) for row in rows[:TOP_CARDS])
    return f'<style>{_CSS}</style><div class="ov-cards">{cards}</div>'


# ---------------------------------------------------------------- drawing

def _small(text: str) -> None:
    """A line of small grey text. `text` is plain text (it is escaped here), never HTML."""
    st.markdown(f'<p style="color:{MUTED};font-size:0.9rem;margin:0.25rem 0">{_e(text)}</p>', unsafe_allow_html=True)


def _details(overview: Overview) -> None:
    """Everything the Overview used to show on the page, unchanged apart from the wording of 3.1."""
    _small(overview.run_line)
    _small(EXPLAIN_OVERALL)
    _small(EXPLAIN_PPI)
    _small(EXPLAIN_INDUSTRY)

    st.markdown("#### Leaderboard")
    recommended_name = overview.recommended.name if overview.recommended else None
    st.markdown(leaderboard_html(overview.rows, recommended_name), unsafe_allow_html=True)
    for row in overview.rows:
        if row.tie_break:
            _small(row.tie_break)
    if any(not row.eligible for row in overview.rows):
        _small(HOVER_NOTE)

    if overview.runner_up is not None:
        st.markdown(runner_up_html(overview.runner_up), unsafe_allow_html=True)
    elif overview.recommended is not None and len(overview.rows) > 1:
        _small("No other supplier meets every must-have.")
    if overview.industry_warning:
        st.warning(overview.industry_warning)


def _show(overview: Overview) -> None:
    recommended_name = overview.recommended.name if overview.recommended else None
    st.markdown(verdict_html(overview.verdict), unsafe_allow_html=True)
    if overview.budget_line:
        _small(overview.budget_line)

    st.markdown("#### Must-haves")
    if overview.musthaves:
        st.markdown(musthaves_html(overview.rows, overview.musthaves, recommended_name), unsafe_allow_html=True)
        _small(MUSTHAVES_NOTE)
    else:
        _small(NO_MUSTHAVES_TEXT)

    st.markdown("#### Strength by criterion")
    st.markdown(strength_html(overview.rows, overview.criteria), unsafe_allow_html=True)
    _small(STRENGTH_NOTE)

    st.markdown("#### Why the top three")
    st.markdown(cards_html(overview.rows, recommended_name), unsafe_allow_html=True)

    with st.expander(DETAILS_LABEL, expanded=False):
        _details(overview)


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
