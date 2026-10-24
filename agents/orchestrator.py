"""Orchestrator (feature F09, spec section 8): one batch, from the uploaded proposals to a saved ranking.

The LLM judges (inside `agents.evaluator`); Python decides (gates, scores, ranks and the industry score are
pure functions in `tools/`). This module only runs the steps in order, keeps the step log, and saves everything
under one run id, so nothing is lost if a run stops half way.

Steps run here (spec 8): input checks, create the run, read the documents, evaluate each supplier, gates, rank,
industry score, strength labels, save. Not here yet: the evidence check (F15) and the pros/cons wording (F16).
The last part of the file answers questions about the proposals (F14, spec 12): a keyword search picks the best excerpts and the AI
answers from them only. Scoring never uses that search.

The second half of the file is the human review (F13, spec section 11): the person says how satisfied they are with the
recommendation (1 to 5). Satisfied: accept it. Not satisfied: they say what they are not satisfied with, the matching criterion
counts for more in this run and every supplier is ranked again from the AI's saved marks (three rounds at most). Every step adds rows to the
history tables; nothing is ever overwritten (the current result is updated, the decisions and audit rows stay).

Rules kept here:
* The proposals come from the upload box only; nothing is read from `data/proposals/` (DECISIONS 82).
* Budget and quantity are inputs of the run (`PurchaseTerms`), stored with it (DECISIONS 69).
* The API key is used for the calls and is never written anywhere: not to the database, the audit log or a message.
* A bad reply twice from the model gives that supplier all zeros and the run goes on (spec 7.1). A provider problem
  (bad key, no network, timeout after its retry) stops the run: ranking a supplier that was never really evaluated
  would be misleading. Work done so far stays saved, with status "Failed".
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import sqlite3
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import PurePath
from typing import Any

from agents import evaluator, feedback, negotiator, qa_agent, summarizer
from db import audit, repository
from llm import client
from tools import gates as gates_tool
from tools import evidence_checker, industry, insights, pdf_tool, ranking, retrieval, review, reweight
from tools import leaning as leaning_tool

MAX_PROPOSALS = 10
MIN_PROPOSALS = 2          # a ranking compares suppliers, so one proposal is not evaluated
RECOMMENDED_MINIMUM = 4
EXPERIENCE_RATINGS = (1, 2, 3, 4, 5)
BLOCKED = "Blocked"        # the inputs were not acceptable; nothing was created
ATTEMPT_NO = 1             # the first evaluation of a supplier in a run (re-evaluations, F13, count up from here)
REVIEWER = "reviewer"       # who an action is recorded under when the sidebar's reviewer name is empty


# ---------------------------------------------------------------- the inputs

@dataclass(frozen=True)
class Proposal:
    """One uploaded proposal and what the user typed about it."""

    file_name: str
    supplier_name: str
    submission_date: date | None
    experience_rating: int | None          # optional: None means the buyer did not rate this supplier
    pdf: pdf_tool.ExtractedPdf | None      # None when the file could not be read
    read_error: str | None = None          # the friendly reason, when `pdf` is None


@dataclass(frozen=True)
class InputCheck:
    errors: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class TypedPriority:
    """One sentence the buyer wrote about what matters to them, and what the AI made of it (confirmed by the buyer before the run)."""

    text: str
    criterion_id: int
    reading: str
    tokens: int | None = None
    cost_usd: float | None = None
    attempts: int = 1
    leaning: leaning_tool.Leaning | None = None     # F21b: what the sentence leans towards (price end, areas that count more or less)


@dataclass(frozen=True)
class PriorityRequest:
    """"Your priorities" for one run: the criteria the buyer ticked and the sentences the AI read. Applies to this run only."""

    ticked: tuple[int, ...] = ()
    typed: tuple[TypedPriority, ...] = ()


@dataclass(frozen=True)
class PriorityReading:
    """What the AI made of a sentence typed before a run. `ok` means it proposes to raise `criterion_id`; nothing is changed by reading."""

    ok: bool
    message: str
    typed: TypedPriority | None = None


PRIORITY_UNCLEAR = ("I could not match that to a criterion that matters more. Tick the criteria that matter more to you, "
                    "or write it a different way.")


def understand_priority(
    text: str, settings: client.LLMSettings, *,
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> PriorityReading:
    """The buyer wrote what matters to them before a run. The AI proposes ONE criterion that should count for more (spec 7.5); the
    buyer confirms it before anything changes, and the call's tokens and cost are added to the run when it starts. Nothing is saved here."""
    try:
        need = reweight.clean_need(text)
        if not need:
            return PriorityReading(False, "Write what matters to you first.")
        if settings.key_is_in(need):
            return PriorityReading(False, "What you wrote contains your API key. Remove it and try again. Nothing was sent.")
        with repository.connection() as conn:
            criteria = repository.list_criteria(conn, active_only=True)
        try:
            reading = feedback.interpret(need, criteria, settings, backend=backend, sleep=sleep)
        except client.LLMError as exc:
            return PriorityReading(False, f"The AI could not be reached: {exc.user_message} Nothing was changed.")
        except feedback.FeedbackError as exc:
            return PriorityReading(False, f"{exc.user_message} Nothing was changed.")
    except (sqlite3.Error, OSError):
        return PriorityReading(False, "The criteria could not be read because the database was busy. Wait a few seconds and try again.")
    if reading.action == "reweight" and reading.criterion_id is not None:
        return PriorityReading(True, "", TypedPriority(need, reading.criterion_id, reading.reading, reading.tokens, reading.cost_usd,
                                                       reading.attempts, reading.leaning))
    return PriorityReading(False, PRIORITY_UNCLEAR if reading.readable else f"The AI's reply could not be read. {PRIORITY_UNCLEAR}")


def read_pdf(data: bytes) -> tuple[pdf_tool.ExtractedPdf | None, str | None]:
    """Read an uploaded PDF. Returns (pdf, None), or (None, a friendly reason)."""
    try:
        return pdf_tool.extract_pdf(data), None
    except pdf_tool.PdfError as exc:
        return None, str(exc)


def check_inputs(proposals: Sequence[Proposal], criteria: Sequence[repository.Criterion]) -> InputCheck:
    """The input checks of spec step 1. Every problem is a plain sentence with a hint on what to do."""
    errors: list[str] = []
    notes: list[str] = []
    count = len(proposals)
    if count < MIN_PROPOSALS:
        errors.append(f"Upload at least {MIN_PROPOSALS} supplier proposal PDFs. A ranking compares suppliers, so one alone is not evaluated.")
    elif count > MAX_PROPOSALS:
        errors.append(f"You uploaded {count} files, but at most {MAX_PROPOSALS} can be evaluated at once. Remove {count - MAX_PROPOSALS}.")
    elif count < RECOMMENDED_MINIMUM:
        notes.append("The brief expects at least 4 for a meaningful peer comparison.")

    by_name: dict[str, list[Proposal]] = {}
    for proposal in proposals:
        name = proposal.supplier_name.strip()
        label = name or proposal.file_name
        if not name:
            errors.append(f"Enter a supplier name for {proposal.file_name}.")
        else:
            by_name.setdefault(name.lower(), []).append(proposal)
        if proposal.pdf is None:
            errors.append(f"{proposal.file_name}: {proposal.read_error or 'this file could not be read.'}")
        elif not proposal.pdf.has_text:
            errors.append(f"{proposal.file_name} has no readable text (it may be a scanned image). Upload a text-based PDF.")
        if proposal.submission_date is None:
            errors.append(f"Pick a submission date for {label}.")
        rating = proposal.experience_rating   # optional, but when given it must be a whole number from 1 to 5
        if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or rating not in EXPERIENCE_RATINGS):
            errors.append(f"The experience rating for {label} must be a whole number from 1 to 5, or left empty.")
    for group in by_name.values():
        if len(group) > 1:
            files = ", ".join(p.file_name for p in group)
            errors.append(f"The supplier name '{group[0].supplier_name.strip()}' is used for {len(group)} files ({files}). Each supplier needs its own name.")

    if not criteria:
        errors.append("There are no active criteria. Turn at least one on on the Criteria tab before evaluating.")
    else:
        total = repository.weights_total(criteria)
        if not repository.weights_are_valid(total):
            errors.append(f"The active criteria weights total {total:g}, not {repository.WEIGHT_TOTAL:g}. Fix them on the Criteria tab before evaluating.")
    return InputCheck(tuple(errors), tuple(notes))


# ---------------------------------------------------------------- the result of a run

@dataclass(frozen=True)
class SupplierSummary:
    """One supplier's result, as the New evaluation tab shows it (the full detail is saved in `supplier_results.result_json`)."""

    rank: int
    name: str
    absolute_score: float
    ppi: float
    industry_score: float
    submission_date: date
    experience_rating: int | None
    eligible: bool
    failed_gates: tuple[str, ...]       # e.g. "Warranty: 1 year (at least 3 required)"
    evaluation_failed: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RunResult:
    run_id: str                         # empty when the inputs were blocked
    status: str                         # Evaluated, Failed or Blocked (Interrupted is raised, not returned)
    message: str                        # one friendly sentence for the screen
    suppliers: tuple[SupplierSummary, ...] = ()
    tie_break_explanations: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    tokens: int | None = None
    cost_usd: float | None = None
    duration_seconds: float = 0.0


_MONTHS = {name: number for number, names in enumerate((
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",), ("june", "jun"),
    ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
    ("december", "dec")), start=1) for name in names}

# Every usual way of writing a date. Each pattern is tried on its own, so a word that is not a month never hides a real date.
_DATE_PATTERNS = (
    (re.compile(r"(?<![\d/.\-])(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)"), "year-month-day"),                      # 2026-10-05
    (re.compile(r"(?<![\d/.\-])(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})(?!\d)"), "day-month-year"),            # 05/10/2026, 5.10.2026 (day first, as in India)
    (re.compile(r"(?<![\d/.\-])(\d{1,2})(?:st|nd|rd|th)?(?:\s+of)?[\s/.\-]+([A-Za-z]{3,9})\.?,?[\s/.\-]*(\d{4})(?!\d)"),
     "day-name-year"),                                                                                       # 5 October 2026, 5th Oct, 2026, 5-Oct-2026
    (re.compile(r"(?<![A-Za-z])([A-Za-z]{3,9})\.?[\s/.\-]+(\d{1,2})(?:st|nd|rd|th)?,?[\s/.\-]+(\d{4})(?!\d)"),
     "name-day-year"),                                                                                       # October 5, 2026
)
FIRST_YEAR, LAST_YEAR = 1990, 2100   # a "date" outside these years in a proposal is a number, not a date

# A label that says "this is the date of the proposal" (any wording below), then the date itself.
_STRONG_LABEL = re.compile(
    r"\b(?:submission\s+date|date\s+of\s+submission|submitted\s+on|date\s+submitted|submitted|proposal\s+date|"
    r"date\s+of\s+(?:proposal|response|offer|quotation)|response\s+date|quotation\s+date|quote\s+date|offer\s+date|dated)"
    r"\s*[:\-–]?\s*(?=(.{0,40}))", re.IGNORECASE | re.DOTALL)
_PLAIN_LABEL = re.compile(r"^[ \t]*date[ \t]*[:\-–][ \t]*(?=(.{0,40}))", re.IGNORECASE | re.MULTILINE | re.DOTALL)
# Words just before a date that show it is some other date: a validity period, a deadline, a delivery date...
_OTHER_DATE_CONTEXT = re.compile(
    r"\b(?:valid\w*|until|expir\w*|deadline|due|closing|before|issued|issue\s+date|opening|within|deliver\w*)\b|\bby\s*$",
    re.IGNORECASE)

LABELLED, FIRST_ON_PAGE_ONE, TODAY = "labelled", "first date on page 1", "today"


@dataclass(frozen=True)
class DateSuggestion:
    """The submission date to pre-fill for a proposal, and where it came from (one of the three constants above)."""

    date: date
    source: str


def _build_date(kind: str, first: str, second: str, third: str) -> date | None:
    try:
        if kind == "year-month-day":
            year, month, day = int(first), int(second), int(third)
        elif kind == "day-month-year":
            day, month, year = int(first), int(second), int(third)
        elif kind == "day-name-year":
            day, month, year = int(first), _MONTHS.get(second.lower()), int(third)
        else:
            month, day, year = _MONTHS.get(first.lower()), int(second), int(third)
        if month is None or not FIRST_YEAR <= year <= LAST_YEAR:
            return None
        return date(year, month, day)
    except ValueError:   # something like 31 February
        return None


def _date_at_start(text: str) -> date | None:
    """A date written at the very start of `text` in one of the usual ways, or None."""
    text = text.lstrip()
    for pattern, kind in _DATE_PATTERNS:
        match = pattern.match(text)
        if match and (found := _build_date(kind, *match.groups())) is not None:
            return found
    return None


def _dates_in(text: str) -> list[tuple[int, date]]:
    """Every real date written in `text`, with the position where it starts, in reading order."""
    found = [(match.start(), built) for pattern, kind in _DATE_PATTERNS for match in pattern.finditer(text)
             if (built := _build_date(kind, *match.groups())) is not None]
    return sorted(found)


def find_submission_date(text: str) -> date | None:
    """The date a proposal gives for itself: the first real date written right after a label such as "Submission
    date:", "Dated", "Proposal date" or a line that starts with "Date:". Plain text matching, no model. None when there
    is no such label or no usable date after it."""
    for label in (*_STRONG_LABEL.finditer(text), *_PLAIN_LABEL.finditer(text)):
        found = _date_at_start(label.group(1))
        if found is not None:
            return found
    return None


def suggest_submission_date(pdf: pdf_tool.ExtractedPdf, today: date) -> DateSuggestion:
    """A submission date for any proposal, so the box is never empty. In order of trust: a labelled date anywhere in
    the text; else the first date on page 1 that is not a validity period, deadline or delivery date; else `today`
    (the day it is uploaded). The caller says which one it was, and the visitor can always change it."""
    found = find_submission_date("\n".join(pdf.pages))
    if found is not None:
        return DateSuggestion(found, LABELLED)
    first_page = pdf.pages[0] if pdf.pages else ""
    for start, found in _dates_in(first_page):
        before = first_page[max(0, start - 40):start].rsplit("\n", 1)[-1]
        if not _OTHER_DATE_CONTEXT.search(before):
            return DateSuggestion(found, FIRST_ON_PAGE_ONE)
    return DateSuggestion(today, TODAY)


def default_supplier_name(file_name: str) -> str:
    """The name suggested for an uploaded file: 'apex_systems.pdf' -> 'Apex Systems'. The visitor can change it."""
    return evaluator.title_from_filename(PurePath(file_name))


def format_submitted(moment: date) -> str:
    """'5 Oct 2026', the way every leaderboard shows a submission date."""
    return f"{moment.day} {moment:%b %Y}"


def format_experience(rating: int | None) -> str:
    """The experience rating as shown on screen: the number, or 'Not rated' when the buyer left it empty."""
    return "Not rated" if rating is None else str(rating)


def make_run_id(moment: datetime, token: str) -> str:
    """RUN-YYYYMMDD-HHMMSS-<4 hex>, in UTC like every stored timestamp."""
    return f"RUN-{moment.astimezone(timezone.utc):%Y%m%d-%H%M%S}-{token}"


def chosen_points(r: ranking.RankedSupplier, marks: Sequence[Any], criteria: dict[int, RunCriterion],
                  gates: Sequence[gates_tool.GateResult], evidence: dict[int, bool | None]) -> tuple[insights.ProsCons, dict[str, tuple[str, str]]]:
    """The points Python chooses for one supplier (spec 10.1), plus the justification and evidence that go with each, for the wording."""
    by_id = {m.criterion_id: m for m in marks}
    results = [insights.CriterionResult(
        cr.criterion_id, criteria[cr.criterion_id].name, cr.score, cr.max_score, cr.weight, cr.benchmark, cr.gap, cr.relative_pct,
        by_id[cr.criterion_id].filled_by_validator, evidence.get(cr.criterion_id)) for cr in r.criteria]
    chosen = insights.select_pros_cons(results, [insights.GateOutcome(g.gate_id, g.name, g.passed, g.reason) for g in gates])
    support: dict[str, tuple[str, str]] = {}
    for reason in (*chosen.pros, *chosen.cons):
        if reason.key.split(":")[0] in ("criterion", "evidence", "not_stated"):
            mark = by_id.get(int(reason.key.split(":")[1]))
            if mark is not None:
                support[reason.key] = (mark.justification, mark.evidence)
    return chosen, support


def apply_price_marks(outcomes: dict[str, evaluator.EvaluationOutcome], criteria: Sequence[RunCriterion],
                      facts: dict[str, dict[str, Any]], direction: str = insights.PRICE_LOW) -> tuple[dict[str, evaluator.EvaluationOutcome], insights.PriceMarks | None, RunCriterion | None]:
    """Replace the AI's Commercial Value mark by the one worked out from the totals (insights.price_marks). Only suppliers whose
    evaluation did not fail and whose total is stated count. Returns the new outcomes, the marks and the criterion used
    (None, None when nothing was changed: no price criterion, or fewer than two totals)."""
    criterion = next((c for c in criteria if insights.is_price_criterion(c.name)), None)
    if criterion is None:
        return outcomes, None, None
    totals = {n: insights.price_facts(facts[n], None).total_rs for n, o in outcomes.items() if not o.failed}
    worked = insights.price_marks(totals, criterion.max_score, direction)     # type: ignore[arg-type]
    if worked is None:
        return outcomes, None, criterion
    changed = dict(outcomes)
    for name, mark in worked.marks.items():
        outcome = outcomes[name]
        reason = insights.price_reason(worked.totals_rs[name], worked.reference_rs, mark, criterion.max_score, direction)
        marks = [c.model_copy(update={"score": mark, "justification": reason, "evidence": "", "filled_by_validator": False})
                 if c.criterion_id == criterion.criterion_id else c for c in outcome.scorecard.criteria]
        changed[name] = replace(outcome, scorecard=outcome.scorecard.model_copy(update={"criteria": marks}))
    return changed, worked, criterion


def _total(values: Sequence[float | int | None]) -> float | int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _where(error: BaseException) -> str:
    """Where an unexpected error happened ('file.py:123 in function'), for the audit log. The error's own text is left
    out on purpose: it could contain anything a library put into it, and a key must never reach the database."""
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return "unknown"
    last = frames[-1]
    return f"{os.path.basename(last.filename)}:{last.lineno} in {last.name}"


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _rupees(value: float) -> str:
    return evaluator.format_rupees(value)


# ---------------------------------------------------------------- the run

def run_evaluation(
    proposals: Sequence[Proposal],
    terms: industry.PurchaseTerms,
    settings: client.LLMSettings,
    reviewer_name: str = "",
    priorities: PriorityRequest | None = None,
    *,
    progress: Callable[[str], None] | None = None,
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    token: Callable[[], str] = lambda: secrets.token_hex(2),
) -> RunResult:
    """Run the whole pipeline for one batch and save it. Never raises for a problem the user can fix; a stop
    from outside (for example the page being closed) marks the run "Interrupted" and is re-raised."""
    steps: list[str] = []
    started = clock()

    def log(message: str) -> None:
        steps.append(message)
        if progress:
            progress(message)

    with repository.connection() as conn:
        criteria = repository.list_criteria(conn, active_only=True)
        gate_list = repository.list_gates(conn, active_only=True)
        benchmarks = repository.list_benchmarks(conn)
    check = check_inputs(proposals, criteria)
    if not check.ok:
        return RunResult("", BLOCKED, "The run was not started: " + check.errors[0], errors=check.errors)

    # "Your priorities": the ticked criteria count for more in THIS run; the marking scheme in the Criteria tab is not changed (the AI's
    # marks do not depend on the weights, so the same marks are simply weighted differently).
    request = priorities or PriorityRequest()
    plan = reweight.plan_priorities(criteria, request.ticked)
    kept_typed = tuple(t for t in request.typed if t.criterion_id in plan.ticked)    # a sentence whose tick was removed is dropped
    lean = next((t.leaning for t in reversed(kept_typed) if t.leaning is not None and t.leaning.changes_anything), None)
    direction = lean.price if lean is not None and lean.price else None      # None: the AI's own price mark, as the spec says
    if lean is not None:                  # F21b: what the buyer leans towards sets the weights, and which end of the price range wins
        try:
            lean_plan = leaning_tool.plan(criteria, lean)
            new_weights = lean_plan.new_weights()
            criteria = [replace(c, weight=new_weights[c.criterion_id]) for c in criteria]
        except ValueError:
            lean = None
    elif plan.changed:
        new_weights = plan.new_weights()
        criteria = [replace(c, weight=new_weights[c.criterion_id]) for c in criteria]

    moment = now()
    run_id = make_run_id(moment, token())
    names = [p.supplier_name.strip() for p in proposals]
    total_weight = repository.weights_total(criteria)

    def record(actor: str, action: str, details: dict[str, Any]) -> None:
        with repository.connection() as conn:
            audit.log_event(conn, run_id, actor, action, details)

    def stop(status: str, message: str, details: dict[str, Any]) -> RunResult:
        with repository.connection() as conn:
            repository.finish_run(conn, run_id, status=status, total_tokens=_total(token_counts),
                                  total_cost_usd=_total(costs), duration_seconds=round(clock() - started, 2))
            audit.log_event(conn, run_id, "system", f"run_{status.lower()}", {**details, "message": message})
        log(message)
        return RunResult(run_id, status, message, steps=tuple(steps), tokens=_total(token_counts),
                         cost_usd=_total(costs), duration_seconds=round(clock() - started, 2))

    token_counts: list[int | None] = [t.tokens for t in kept_typed]    # the calls that read the typed sentences are part of this run's cost
    costs: list[float | None] = [t.cost_usd for t in kept_typed]
    created = False
    try:
        log(f"Checked inputs: {len(proposals)} proposal(s), criteria weights total {total_weight:g}, "
            f"budget {_rupees(terms.budget_rs)} for {terms.quantity} laptop(s).")
        snapshot = [{"criterion_id": c.criterion_id, "name": c.name, "weight": c.weight, "max_score": c.max_score,
                     **({"direction": direction} if direction and insights.is_price_criterion(c.name) else {})}
                    for c in criteria]
        temperature = client.temperature_for("scoring")
        with repository.connection() as conn:
            repository.create_run(
                conn, run_id=run_id, created_at=_iso(moment), provider=settings.provider, model=settings.model,
                temperature=temperature, template_id=repository.DEFAULT_TEMPLATE_ID,
                criteria_snapshot_json=json.dumps(snapshot), reviewer_name=reviewer_name.strip(),
                budget_rs=terms.budget_rs, quantity=terms.quantity)
            audit.log_event(conn, run_id, "system", "run_created", {
                "provider": settings.provider, "model": settings.model, "temperature": temperature,
                "template_id": repository.DEFAULT_TEMPLATE_ID, "budget_rs": terms.budget_rs, "quantity": terms.quantity,
                "reviewer_name": reviewer_name.strip(), "suppliers": names})
        created = True   # only once the row is really saved
        log(f"Created run {run_id}.")
        if plan.ticked or request.typed:
            with repository.connection() as conn:
                for t in kept_typed:
                    audit.log_event(conn, run_id, "AI", "need_interpreted", {
                        "text": t.text, "action": "reweight", "criterion_id": t.criterion_id, "reading": t.reading, "readable": True,
                        "llm_calls": t.attempts, "model": settings.model, "provider": settings.provider, "tokens": t.tokens,
                        "cost_usd": t.cost_usd, "before_run": True})
                audit.log_event(conn, run_id, reviewer_name.strip() or REVIEWER, "priorities_set", {
                    "ticked": list(plan.ticked), "skipped": list(plan.skipped), "everything_ticked": plan.everything_ticked,
                    "typed": [{"text": t.text, "criterion_id": t.criterion_id, "reading": t.reading} for t in kept_typed],
                    "leaning": _leaning_data(lean), "price_direction": direction,
                    "weights": [{"criterion_id": l.criterion_id, "name": l.name, "was": l.was, "now": l.now} for l in plan.lines]})
            log(describe_weights(lean_plan) if lean is not None else reweight.describe_priorities(plan))

        # --- read the documents
        with repository.connection() as conn:
            for proposal, name in zip(proposals, names):
                assert proposal.pdf is not None
                repository.add_document(conn, run_id, name, proposal.file_name, proposal.pdf.sha256, proposal.pdf.page_count)
            audit.log_event(conn, run_id, "system", "documents_read", {"documents": [
                {"supplier": n, "file": p.file_name, "pages": p.pdf.page_count, "sha256": p.pdf.sha256}
                for p, n in zip(proposals, names) if p.pdf is not None]})
        for proposal in proposals:
            assert proposal.pdf is not None
            log(f"Read {proposal.file_name}: {proposal.pdf.page_count} page(s), fingerprint {proposal.pdf.sha256[:12]}.")

        # --- evaluate each supplier (the model judges; nothing is calculated by it)
        outcomes: dict[str, evaluator.EvaluationOutcome] = {}
        evidence_flags: dict[str, dict[int, bool | None]] = {}
        for index, (proposal, name) in enumerate(zip(proposals, names), start=1):
            assert proposal.pdf is not None
            log(f"Evaluating {name} ({index} of {len(proposals)}) with {settings.litellm_model}...")
            try:
                outcome = evaluator.evaluate_supplier(name, proposal.pdf.text, criteria, terms, settings,
                                                      backend=backend, sleep=sleep)
            except client.LLMError as exc:
                return stop(repository.RUN_FAILED,
                            f"Stopped while evaluating {name}: {exc.user_message} Nothing was ranked. Work done so far is saved.",
                            {"supplier": name, "kind": exc.kind})
            except evaluator.EvaluatorError as exc:
                return stop(repository.RUN_FAILED, f"Stopped while evaluating {name}: {exc.user_message}", {"supplier": name})
            outcomes[name] = outcome
            token_counts.append(outcome.tokens)
            costs.append(outcome.cost_usd)
            found = {c.criterion_id: evidence_checker.evidence_found(c.evidence, proposal.pdf.pages)
                     for c in outcome.scorecard.criteria}                     # spec 9.2: is each quote in the proposal?
            evidence_flags[name] = found
            rows = [repository.AttemptRow(c.criterion_id, c.score, c.justification, c.evidence, found[c.criterion_id],
                                          outcome.raw_reply if i == 0 else None)
                    for i, c in enumerate(outcome.scorecard.criteria)]
            missing = [c.criterion_id for c in outcome.scorecard.criteria if found[c.criterion_id] is False]
            with repository.connection() as conn:
                repository.add_attempt(conn, run_id, name, ATTEMPT_NO, rows, _iso(now()))
                audit.log_event(conn, run_id, "AI", "supplier_evaluated", {
                    "supplier": name, "attempt_no": ATTEMPT_NO, "llm_calls": outcome.attempts, "model": settings.model,
                    "tokens": outcome.tokens, "cost_usd": outcome.cost_usd, "evaluation_failed": outcome.failed,
                    "name_hidden_times": outcome.name_replacements})
                if outcome.warnings:
                    audit.log_event(conn, run_id, "AI", "validation_warnings",
                                    {"supplier": name, "warnings": list(outcome.warnings)})
                if missing:
                    audit.log_event(conn, run_id, "system", "evidence_not_found",
                                    {"supplier": name, "criterion_ids": missing, "checked": sum(v is not None for v in found.values())})
            detail = "FAILED, all scores set to 0" if outcome.failed else ("2 tries" if outcome.attempts == 2 else "ok")
            used = "tokens not reported" if outcome.tokens is None else f"{outcome.tokens} tokens"
            log(f"Evaluated {name}: {detail}, {used}, {len(outcome.warnings)} warning(s).")
            if missing:
                log(f"Evidence check for {name}: {len(missing)} quote(s) not found in the proposal (flagged, scores unchanged).")

        # --- gates, ranking, industry score and strength labels (all plain Python)
        facts = {n: o.scorecard.extracted_facts.model_dump() for n, o in outcomes.items()}
        gate_results = {n: gates_tool.evaluate_gates(gate_list, facts[n]) for n in names}
        not_eligible = [n for n in names if not gates_tool.is_eligible(gate_results[n])]
        for n in names:
            record("system", "gate_results", {
                "supplier": n, "gates": gates_tool.gate_status(gate_results[n]),
                "failed": [f"{g.name}: {g.reason}" for g in gates_tool.failed_gates(gate_results[n])]})
        log(f"Checked {len(gate_list)} gate(s) for {len(names)} supplier(s): "
            + (f"{len(not_eligible)} not eligible ({', '.join(not_eligible)})." if not_eligible else "all eligible."))

        # --- the price mark is worked out from the totals (the AI's own mark is kept in the attempts table)
        ai_price_marks = {}
        price_criterion = next((c for c in criteria if insights.is_price_criterion(c.name)), None)
        if price_criterion is not None:
            ai_price_marks = {n: next((c.score for c in o.scorecard.criteria if c.criterion_id == price_criterion.criterion_id), None)
                              for n, o in outcomes.items()}
        outcomes, worked, _used = apply_price_marks(outcomes, criteria, facts, direction) if direction else (outcomes, None, price_criterion)
        for n in outcomes:
            if worked is not None and n in worked.marks:
                evidence_flags[n][price_criterion.criterion_id] = None     # type: ignore[union-attr]
        if worked is not None:
            record("system", "price_marks_set", {
                "direction": direction,
                "criterion_id": price_criterion.criterion_id,       # type: ignore[union-attr]
                "lowest_total_rs": worked.reference_rs, "marks": worked.marks, "totals_rs": worked.totals_rs,
                "ai_marks": ai_price_marks, "without_total": list(worked.without_total)})
            log(f"Price marks worked out from the totals (lowest {insights.format_lakh(worked.reference_rs)} gets full marks)"
                + (f"; no total for {', '.join(worked.without_total)}, so the AI's mark is kept." if worked.without_total else "."))
        elif price_criterion is not None and direction:
            log("Price marks kept as the AI gave them: fewer than two suppliers state a total price.")
        elif price_criterion is not None:
            log("Price marks are the AI's own: no price wish was given.")

        weights = [ranking.CriterionWeight(c.criterion_id, c.weight, c.max_score) for c in criteria]
        inputs = [ranking.SupplierInput(n, p.submission_date, p.experience_rating,  # type: ignore[arg-type]
                                        {c.criterion_id: c.score for c in outcomes[n].scorecard.criteria})
                  for p, n in zip(proposals, names)]
        ranked = ranking.rank_suppliers(inputs, weights)
        top = ", ".join(f"{r.rank}. {r.name} (peer score {r.ppi:.2f})" for r in ranked.suppliers[:3])
        record("system", "ranking_computed", {
            "top3": [{"rank": r.rank, "supplier": r.name, "ppi": r.ppi, "absolute_score": r.absolute_score}
                     for r in ranked.suppliers[:3]],
            "tie_break_explanations": list(ranked.tie_break_explanations), "warnings": list(ranked.warnings)})
        log(f"Ranked {len(ranked.suppliers)} supplier(s). {top}")

        industry_results = {n: industry.industry_score(facts[n], benchmarks, criteria, terms) for n in names}
        log("Graded every supplier against the industry baseline.")

        # --- the AI words the points Python chose (spec 10.1 / 7.3); a problem here never stops the run
        wordings: dict[str, summarizer.Wording | None] = {}
        chosen_of: dict[str, insights.ProsCons] = {}
        for r in ranked.suppliers:
            outcome = outcomes[r.name]
            if outcome.failed:                                  # nothing worth praising or criticising in a failed evaluation
                chosen_of[r.name], wordings[r.name] = insights.ProsCons((), ()), None
                continue
            chosen, support = chosen_points(r, outcome.scorecard.criteria, {c.criterion_id: c for c in criteria}, gate_results[r.name],
                                            evidence_flags.get(r.name, {}))
            chosen_of[r.name] = chosen
            if not chosen.pros and not chosen.cons:
                wordings[r.name] = None
                continue
            others = [n for n in names if n != r.name]
            try:
                wording = summarizer.word_points(chosen, support, others, settings, backend=backend, sleep=sleep)
            except client.LLMError as exc:
                wordings[r.name] = None
                record("AI", "wording_failed", {"supplier": r.name, "kind": exc.kind})
                log(f"Could not word the points for {r.name} ({exc.user_message}). The plain wording is used.")
                continue
            except summarizer.SummarizerError as exc:
                wordings[r.name] = None
                token_counts.append(exc.tokens)
                costs.append(exc.cost_usd)
                record("AI", "wording_failed", {"supplier": r.name, "kind": "unusable_reply", "tokens": exc.tokens})
                log(f"The AI's wording for {r.name} could not be used. The plain wording is used.")
                continue
            wordings[r.name] = wording
            token_counts.append(wording.tokens)
            costs.append(wording.cost_usd)
            record("AI", "points_worded", {"supplier": r.name, "pros": len(wording.pros), "cons": len(wording.cons),
                                           "kept_plain": wording.kept_plain, "reason": wording.reason is not None,
                                           "tokens": wording.tokens, "cost_usd": wording.cost_usd, "model": settings.model})
        log(f"Worded the good and bad points for {sum(w is not None for w in wordings.values())} supplier(s).")

        # --- save the results
        by_name = {n: (p, o) for n, p, o in zip(names, proposals, (outcomes[n] for n in names))}
        criterion_info = {c.criterion_id: c for c in criteria}
        winners_of_ties = {e.split(" above ", 1)[0]: e for e in ranked.tie_break_explanations}
        summaries: list[SupplierSummary] = []
        with repository.connection() as conn:
            for r in ranked.suppliers:
                proposal, outcome = by_name[r.name]
                assert proposal.pdf is not None and proposal.submission_date is not None
                gates_for = gate_results[r.name]
                eligible = gates_tool.is_eligible(gates_for)
                failed = tuple(f"{g.name}: {g.reason}" for g in gates_tool.failed_gates(gates_for))
                industry_for = industry_results[r.name]
                card = outcome.scorecard
                by_id = {c.criterion_id: c for c in card.criteria}
                warnings = (*outcome.warnings, *industry_for.warnings, *ranked.warnings)
                saved_wording = wordings[r.name].as_saved() if wordings[r.name] else None
                shown = insights.apply_wording(chosen_of[r.name], saved_wording)
                result = {
                    "rank": r.rank, "supplier_name": r.name, "file_name": proposal.file_name,
                    "submission_date": proposal.submission_date.isoformat(), "experience_rating": proposal.experience_rating,
                    "absolute_score": r.absolute_score, "ppi": r.ppi, "industry_score": industry_for.score,
                    "eligible": eligible, "gates": gates_tool.gate_status(gates_for),
                    "gate_details": [{"gate_id": g.gate_id, "name": g.name, "passed": g.passed, "reason": g.reason}
                                     for g in gates_for],
                    "criteria": [{
                        "criterion_id": cr.criterion_id, "name": criterion_info[cr.criterion_id].name, "score": cr.score,
                        "max_score": cr.max_score, "benchmark": cr.benchmark, "gap": cr.gap, "relative_pct": cr.relative_pct,
                        "weight": cr.weight, "strength": insights.strength_label(cr.score, cr.max_score),
                        "justification": by_id[cr.criterion_id].justification, "evidence": by_id[cr.criterion_id].evidence,
                        "evidence_found": evidence_flags[r.name].get(cr.criterion_id),
                        "filled_by_validator": by_id[cr.criterion_id].filled_by_validator,
                    } for cr in r.criteria],
                    "extracted_facts": facts[r.name],
                    "industry_breakdown": {
                        "score": industry_for.score,
                        "attributes": [{"attribute_key": a.attribute_key, "label": a.label, "grade": a.grade,
                                        "points": a.points, "weight": a.weight, "reason": a.reason}
                                       for a in industry_for.attributes]},
                    "pros": [reason.text for reason in shown.pros], "cons": [reason.text for reason in shown.cons],
                    "wording": saved_wording,
                    "risks": list(card.risks), "overall_summary": card.overall_summary,
                    "warnings": list(warnings), "evaluation_failed": outcome.failed,
                    "tie_break_explanation": winners_of_ties.get(r.name),
                    "document_sha256": proposal.pdf.sha256, "pages": proposal.pdf.page_count,
                }
                repository.save_supplier_result(
                    conn, run_id=run_id, supplier_name=r.name, submission_date=proposal.submission_date.isoformat(),
                    experience_rating=proposal.experience_rating, absolute_score=r.absolute_score, ppi=r.ppi,
                    industry_score=industry_for.score, final_rank=r.rank, gates_passed=eligible,
                    result_json=json.dumps(result))
                summaries.append(SupplierSummary(r.rank, r.name, r.absolute_score, r.ppi, industry_for.score,
                                                 proposal.submission_date, proposal.experience_rating, eligible, failed,
                                                 outcome.failed, tuple(warnings)))
            duration = round(clock() - started, 2)
            tokens, cost = _total(token_counts), _total(costs)
            repository.finish_run(conn, run_id, status=repository.RUN_EVALUATED, total_tokens=tokens,
                                  total_cost_usd=cost, duration_seconds=duration)
            audit.log_event(conn, run_id, "system", "run_evaluated",
                            {"suppliers": len(summaries), "tokens": tokens, "cost_usd": cost, "duration_seconds": duration})
        spent = "" if cost is None else f", about USD {cost:.4f}"
        message = f"Run {run_id} evaluated {len(summaries)} supplier(s) in {duration:.1f} s{spent}."
        log(f"Saved the results. {message}")
        return RunResult(run_id, repository.RUN_EVALUATED, message, tuple(summaries), ranked.tie_break_explanations,
                         steps=tuple(steps), tokens=tokens, cost_usd=cost, duration_seconds=duration)
    except Exception as exc:   # a bug or a database problem: say so plainly, keep what was saved, never show a stack trace
        if not created:
            return RunResult("", repository.RUN_FAILED,
                             f"The run could not be started ({type(exc).__name__}). Close other programs using the database, wait a few seconds and try again.",
                             steps=tuple(steps))
        try:
            return stop(repository.RUN_FAILED,
                        f"The run stopped because of an unexpected problem ({type(exc).__name__}). Nothing was ranked. Try again; the audit log has the details.",
                        {"error_type": type(exc).__name__, "where": _where(exc)})
        except Exception:
            return RunResult(run_id, repository.RUN_FAILED, f"The run stopped because of an unexpected problem ({type(exc).__name__}).",
                             steps=tuple(steps))
    except BaseException:   # the page was closed or the script was stopped: leave a clear mark, then let it stop
        if created:
            try:
                with repository.connection() as conn:
                    repository.set_run_status(conn, run_id, repository.RUN_INTERRUPTED)
                    audit.log_event(conn, run_id, "system", "run_interrupted", {"steps_done": len(steps)})
            except Exception:
                pass
        raise


# ================================================================ human review (F13, spec section 11)

@dataclass(frozen=True)
class RunCriterion:
    """A criterion as this RUN used it: its name, weight and maximum come from the run's snapshot, so a later change on the Criteria tab
    does not alter an old run. Only the description (for the AI's prompt) is read from the marking scheme."""

    criterion_id: int
    name: str
    description: str
    weight: float
    max_score: float


@dataclass(frozen=True)
class ReviewOutcome:
    """What a review action did, as one friendly sentence for the screen. `ok` is False when nothing was changed."""

    ok: bool
    message: str


@dataclass(frozen=True)
class _ReviewState:
    run: repository.RunRecord
    suppliers: dict[str, repository.StoredSupplier]
    criteria: tuple[RunCriterion, ...]
    feedback: tuple[repository.FeedbackRow, ...]

    @property
    def weights(self) -> list[ranking.CriterionWeight]:
        return [ranking.CriterionWeight(c.criterion_id, c.weight, c.max_score) for c in self.criteria]

    def criterion(self, criterion_id: int) -> RunCriterion:
        for c in self.criteria:
            if c.criterion_id == criterion_id:
                return c
        raise review.ReviewError("That criterion is not part of this run.")

    def supplier(self, name: str) -> repository.StoredSupplier:
        if name not in self.suppliers:
            raise review.ReviewError(f"{name} is not part of this run.")
        return self.suppliers[name]


def _load_state(conn: sqlite3.Connection, run_id: str, *, open_only: bool = True) -> _ReviewState:
    run = repository.get_run(conn, run_id)
    if run is None:
        raise review.ReviewError("This run was not found.")
    if open_only and run.status != repository.RUN_EVALUATED:
        raise review.ReviewError(f"This run is {run.status.lower()}, so it can no longer be changed.")
    stored = repository.list_supplier_results(conn, run_id)
    if not stored:
        raise review.ReviewError("This run has no saved results to review.")
    descriptions = {c.criterion_id: c.description for c in repository.list_criteria(conn, run.template_id)}
    criteria: list[RunCriterion] = []
    for item in run.criteria_snapshot:
        try:
            criteria.append(RunCriterion(int(item["criterion_id"]), str(item["name"]), descriptions.get(int(item["criterion_id"]), ""),
                                         float(item["weight"]), float(item["max_score"])))
        except (KeyError, TypeError, ValueError):
            raise review.ReviewError("This run's marking scheme was not saved properly, so it cannot be reviewed.") from None
    if not criteria:
        raise review.ReviewError("This run has no saved marking scheme, so it cannot be reviewed.")
    return _ReviewState(run, {s.supplier_name: s for s in stored}, tuple(criteria), tuple(repository.list_feedback(conn, run_id)))


def _terms_of(run: repository.RunRecord, consequence: str = "the AI cannot look at it again") -> industry.PurchaseTerms:
    try:
        return industry.PurchaseTerms(budget_rs=run.budget_rs, quantity=run.quantity)   # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise review.ReviewError(f"This run did not save its budget and quantity, so {consequence}.") from None


def _board(stored: dict[str, repository.StoredSupplier]) -> list[dict[str, Any]]:
    """The saved leaderboard, best first, as plain data."""
    return [{"rank": s.final_rank, "supplier": s.supplier_name, "ppi": s.ppi, "absolute_score": s.absolute_score,
             "eligible": s.gates_passed} for s in sorted(stored.values(), key=lambda s: (s.final_rank, s.supplier_name))]


def _rank_inputs(state: _ReviewState, results: dict[str, dict[str, Any]],
                 weights: Sequence[ranking.CriterionWeight] | None = None) -> ranking.RankingResult:
    return review.rank_results(results, state.weights if weights is None else weights,
                               {n: s.submission_date for n, s in state.suppliers.items()},
                               {n: s.experience_rating for n, s in state.suppliers.items()})


def _save_ranking(conn: sqlite3.Connection, run_id: str, state: _ReviewState, ranked: ranking.RankingResult,
                  results: dict[str, dict[str, Any]], eligible: dict[str, bool], industry_scores: dict[str, float]) -> None:
    for r in ranked.suppliers:
        repository.update_supplier_result(
            conn, run_id=run_id, supplier_name=r.name, absolute_score=r.absolute_score, ppi=r.ppi,
            industry_score=industry_scores.get(r.name, state.suppliers[r.name].industry_score), final_rank=r.rank,
            gates_passed=eligible[r.name], result_json=json.dumps(results[r.name]))


def _log_ranking(conn: sqlite3.Connection, run_id: str, ranked: ranking.RankingResult, why: str) -> None:
    audit.log_event(conn, run_id, "system", "ranking_computed", {
        "top3": [{"rank": r.rank, "supplier": r.name, "ppi": r.ppi, "absolute_score": r.absolute_score}
                 for r in ranked.suppliers[:3]],
        "tie_break_explanations": list(ranked.tie_break_explanations), "warnings": list(ranked.warnings), "because": why})


def _failed_outcome(error: review.ReviewError | sqlite3.Error | OSError) -> ReviewOutcome:
    if isinstance(error, review.ReviewError):
        return ReviewOutcome(False, str(error))
    return ReviewOutcome(False, "The change could not be saved because the database was busy or unavailable. Nothing was changed. "
                                "Wait a few seconds and try again.")


def _breakdown(industry_for: industry.IndustryResult) -> dict[str, Any]:
    """The industry grade as it is saved in a supplier's result."""
    return {"score": industry_for.score,
            "attributes": [{"attribute_key": a.attribute_key, "label": a.label, "grade": a.grade, "points": a.points,
                            "weight": a.weight, "reason": a.reason} for a in industry_for.attributes]}


# ---------------------------------------------------------------- satisfaction: accept, or change the priorities (spec 11.2)

ROUNDS_USED_TEXT = (f"All {reweight.MAX_ROUNDS} rounds of changing the priorities have been used. "
                    "The ranking stays as it is; you can still accept the recommendation.")
ANSWERED_TEXT = "This question was already answered, here or in another window. Reload the page to see the latest result."
PICK_AN_OPTION = "Pick one of the options, or write it a different way."


@dataclass(frozen=True)
class Understanding:
    """What the AI made of a typed need. `ok` means it proposes to raise `criterion_id`, and `reading` is the sentence the person
    confirms; otherwise `message` says why nothing was proposed. Nothing is ever changed by this step."""

    ok: bool
    message: str
    criterion_id: int | None = None
    reading: str = ""
    leaning: leaning_tool.Leaning | None = None   # what the person leans towards (F21b), confirmed with the reading


def _check_round(state: _ReviewState, round_no: int) -> None:
    """The question being answered must be the one the run is on (not an old page, not a second click)."""
    if round_no != reweight.rounds_used(state.feedback) + 1:
        raise review.ReviewError(ANSWERED_TEXT)


def _weights_as_data(criteria: Sequence[RunCriterion]) -> list[dict[str, Any]]:
    return [{"criterion_id": c.criterion_id, "name": c.name, "weight": c.weight} for c in criteria]


def understand_need(
    run_id: str, text: str, settings: client.LLMSettings, actor: str = REVIEWER,
    *,
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Understanding:
    """The person chose "None of the above" and wrote what they need. The AI reads it and PROPOSES one action (spec 7.5); the
    person confirms the reading before anything changes, so nothing is changed here. Only a "reweight" that points at a criterion
    of this run can be carried out in this version; anything else is explained and the person picks an option instead. The
    tokens and cost of the call are added to the run, and the call is written to the audit log."""
    who = actor or REVIEWER

    def failure(reason: str, kind: str) -> Understanding:
        try:
            with repository.connection() as conn:
                audit.log_event(conn, run_id, "AI", "need_interpretation_failed",
                                {"kind": kind, "model": settings.model, "provider": settings.provider, "requested_by": who})
        except (sqlite3.Error, OSError):
            pass
        return Understanding(False, reason)

    try:
        need = reweight.clean_need(text)
        if not need:
            raise review.ReviewError("Write what you are not satisfied with.")
        if settings.key_is_in(need):
            raise review.ReviewError("What you wrote contains your API key. Remove it and try again. Nothing was sent.")
        with repository.connection() as conn:
            state = _load_state(conn, run_id)
        if reweight.rounds_left(state.feedback) <= 0:
            raise review.ReviewError(ROUNDS_USED_TEXT)
        try:
            reading = feedback.interpret(need, state.criteria, settings, backend=backend, sleep=sleep)
        except client.LLMError as exc:
            return failure(f"The AI could not be reached: {exc.user_message} Nothing was changed.", exc.kind)
        except feedback.FeedbackError as exc:
            return failure(f"{exc.user_message} Nothing was changed.", "feedback")
        with repository.connection() as conn:
            repository.add_to_run_totals(conn, run_id, reading.tokens, reading.cost_usd)
            audit.log_event(conn, run_id, "AI", "need_interpreted", {
                "text": need, "action": reading.action, "criterion_id": reading.criterion_id, "reading": reading.reading,
                "readable": reading.readable, "llm_calls": reading.attempts, "model": settings.model, "provider": settings.provider,
                "tokens": reading.tokens, "cost_usd": reading.cost_usd, "requested_by": who,
                "leaning": _leaning_data(reading.leaning)})
        if reading.action == "reweight" and reading.criterion_id is not None:
            return Understanding(True, "", reading.criterion_id, reading.reading, reading.leaning)
        if not reading.readable:
            return Understanding(False, f"The AI's reply could not be read. {PICK_AN_OPTION} Nothing was changed.")
        said = reweight.UNSUPPORTED_ACTIONS.get(reading.action, "I could not match that to anything this evaluation looks at.")
        return Understanding(False, f"{said} {PICK_AN_OPTION} Nothing was changed.")
    except (review.ReviewError, sqlite3.Error, OSError) as error:
        return Understanding(False, _failed_outcome(error).message)


def _leaning_data(lean: leaning_tool.Leaning | None) -> dict[str, Any] | None:
    if lean is None:
        return None
    return {"price": lean.price, "only_id": lean.only_id, "emphasis": {str(i): level for i, level in lean.emphasis}}


def price_direction_of(snapshot: Sequence[Mapping[str, Any]]) -> str | None:
    """The end of the price range the buyer asked for in this run ("low" or "high"), or None when they asked for neither. With None the
    Commercial Value mark is the AI's own, as the spec says; the price formula is used only on the buyer's request."""
    for item in snapshot:
        if insights.is_price_criterion(item.get("name")) and item.get("direction") in (insights.PRICE_LOW, insights.PRICE_HIGH):
            return str(item["direction"])
    return None


def ai_price_source(conn: sqlite3.Connection, run_id: str, results: dict[str, dict[str, Any]],
                    criteria: Sequence[RunCriterion]) -> dict[str, dict[str, Any]]:
    """The saved results with the Commercial Value mark put back to the AI's own (the latest stored attempt of each supplier)."""
    criterion = next((c for c in criteria if insights.is_price_criterion(c.name)), None)
    if criterion is None:
        return results
    latest: dict[str, repository.AttemptRecord] = {}
    for a in repository.list_attempts(conn, run_id):
        if a.criterion_id == criterion.criterion_id and (a.supplier_name not in latest or a.attempt_no >= latest[a.supplier_name].attempt_no):
            latest[a.supplier_name] = a
    changed = copy.deepcopy(results)
    for name, attempt in latest.items():
        for item in changed.get(name, {}).get("criteria") or []:
            if isinstance(item, dict) and item.get("criterion_id") == criterion.criterion_id:
                item.update(score=attempt.score, justification=attempt.justification, evidence=attempt.evidence)
    return changed


def set_price_direction(results: dict[str, dict[str, Any]], criteria: Sequence[RunCriterion], direction: str) -> dict[str, dict[str, Any]]:
    """The saved Commercial Value marks worked out again from the saved totals, for the wanted end of the price range. Suppliers whose
    evaluation failed or that state no total keep the mark they have. Needs at least two stated totals; otherwise nothing changes."""
    criterion = next((c for c in criteria if insights.is_price_criterion(c.name)), None)
    if criterion is None:
        return results
    totals = {}
    for name, result in results.items():
        if result.get("evaluation_failed"):
            continue
        facts = result.get("extracted_facts")
        totals[name] = insights.price_facts(facts if isinstance(facts, dict) else None, None).total_rs
    worked = insights.price_marks(totals, criterion.max_score, direction)       # type: ignore[arg-type]
    if worked is None:
        return results
    changed = copy.deepcopy(results)
    for name, mark in worked.marks.items():
        reason = insights.price_reason(worked.totals_rs[name], worked.reference_rs, mark, criterion.max_score, direction)
        for item in changed[name].get("criteria") or []:
            if isinstance(item, dict) and item.get("criterion_id") == criterion.criterion_id:
                item.update(score=mark, justification=reason, evidence="", filled_by_validator=False)
    return changed


def describe_weights(plan: reweight.Reweighting) -> str:
    parts = [f"{line.name} {reweight.percent(line.was)}% to {reweight.percent(line.now)}%" for line in plan.lines if line.was != line.now]
    return "Counting for this run: " + "; ".join(parts) + "." if parts else "The weights stay as they are."


def change_priorities(
    run_id: str, satisfaction: int, criterion_id: int, round_no: int, typed_need: str = "", actor: str = REVIEWER,
    *, reading: str = "", proposed_id: int | None = None, leaning: leaning_tool.Leaning | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> ReviewOutcome:
    """The person is not satisfied (1 to 3) and said what with: this criterion counts for more in THIS run (spec 11.2: +10 points,
    at most 60, the others scaled so the total stays 100), every supplier is ranked again from the AI's saved marks, and the
    industry score is worked out again with the new weights. Nothing is sent to the AI.

    `typed_need` is what the person wrote after "None of the above" (empty when they picked an option); `reading` and `proposed_id`
    are what the AI proposed and the person confirmed or changed. `round_no` is the question being answered (1 or 2): a second click or
    an old page is refused. Everything is written in one go: the run's new weights, the new ranking, the decision (with the old and
    new weights and winner) and the audit rows. The marks, the attempts and the original ranking are never touched."""
    who = actor or REVIEWER
    try:
        score = reweight.check_satisfaction(satisfaction)
        if reweight.is_satisfied(score):
            raise review.ReviewError(f"A satisfaction of {reweight.SATISFIED_AT} or higher means you are satisfied, so the priorities "
                                     "stay as they are. Accept the recommendation instead.")
        with repository.connection() as conn:
            state = _load_state(conn, run_id)
            benchmarks = repository.list_benchmarks(conn, state.run.template_id)
            if reweight.rounds_left(state.feedback) <= 0:
                raise review.ReviewError(ROUNDS_USED_TEXT)
            _check_round(state, round_no)
            criterion = state.criterion(criterion_id)
            lean = leaning if leaning is not None and leaning.changes_anything else None
            if lean is not None:           # F21b: what the person leans towards sets the weights (and maybe the end of the price range)
                try:
                    plan = leaning_tool.plan(state.criteria, lean, criterion_id)
                except ValueError as exc:
                    raise review.ReviewError(str(exc)) from None
            else:
                plan = reweight.plan_reweight(state.criteria, criterion_id)
            old_direction = price_direction_of(state.run.criteria_snapshot)
            direction = lean.price if lean is not None and lean.price else old_direction     # None: no price wish, the AI's own marks
            terms = _terms_of(state.run, "the industry score cannot be worked out again with the new priorities")
            new_weights = plan.new_weights()
            new_criteria = tuple(replace(c, weight=new_weights[c.criterion_id]) for c in state.criteria)
            ranking_weights = [ranking.CriterionWeight(c.criterion_id, c.weight, c.max_score) for c in new_criteria]

            old_results = {n: s.result for n, s in state.suppliers.items()}
            old_ranked = _rank_inputs(state, old_results)
            results: dict[str, dict[str, Any]] = {}
            industry_scores: dict[str, float] = {}
            source = {n: copy.deepcopy(dict(s.result)) for n, s in state.suppliers.items()}
            if direction != old_direction and direction is not None:
                source = set_price_direction(source, state.criteria, direction)
            for name, stored in state.suppliers.items():
                result = source[name]
                facts = result.get("extracted_facts")
                graded = industry.industry_score(facts if isinstance(facts, dict) else None, benchmarks, new_criteria, terms)
                result.update(industry_score=graded.score, industry_breakdown=_breakdown(graded))
                results[name], industry_scores[name] = result, graded.score
            ranked = _rank_inputs(state, results, ranking_weights)
            new_results = review.apply_ranking(results, ranked, old_ranked.warnings)
            eligible = {n: s.gates_passed for n, s in state.suppliers.items()}
            before, after = _board(state.suppliers), review.standings(ranked, eligible)
            recommended_before, recommended_after = review.recommended_of(before), review.recommended_of(after)

            need = reweight.clean_need(typed_need)
            option = next(o.label for o in reweight.options_for(state.criteria) if o.criterion_id == criterion_id)
            weights = [{"criterion_id": line.criterion_id, "name": line.name, "was": line.was, "now": line.now} for line in plan.lines]
            snapshot = [{**item, "weight": new_weights[int(item["criterion_id"])],
                         **({"direction": direction} if direction and insights.is_price_criterion(item.get("name")) else {})}
                        for item in state.run.criteria_snapshot]
            created = _iso(now())
            repository.update_run_weights(conn, run_id, json.dumps(snapshot))
            repository.add_feedback(
                conn, run_id, action=repository.ACTION_REWEIGHT, created_at=created, criterion_id=criterion_id, rating=score,
                comment=need or option,
                details={"round": round_no, "satisfaction": score, "how": "typed" if need else "option", "option": option,
                         "typed": need, "reading": reweight.clean_need(reading), "proposed_criterion_id": proposed_id,
                         "criterion": criterion.name, "weights": weights, "before": before, "after": after,
                         "recommended_before": recommended_before, "recommended_after": recommended_after, "by": who,
                         "leaning": _leaning_data(lean), "price_direction": direction})
            _save_ranking(conn, run_id, state, ranked, new_results, eligible, industry_scores)
            audit.log_event(conn, run_id, who, "priorities_changed", {
                "round": round_no, "satisfaction": score, "criterion_id": criterion_id, "criterion": criterion.name,
                "reason": need or option, "how": "typed" if need else "option", "weights": weights,
                "recommended_before": recommended_before, "recommended_after": recommended_after})
            _log_ranking(conn, run_id, ranked, f"{criterion.name} counts for more (round {round_no})")
        said = describe_weights(plan) if lean is not None else reweight.describe_reweighting(plan)
        return ReviewOutcome(True, f"{said} {review.describe_recommendation(recommended_before, recommended_after)}")
    except (review.ReviewError, sqlite3.Error, OSError) as error:
        return _failed_outcome(error)


def accept_recommendation(
    run_id: str, satisfaction: int, round_no: int, actor: str = REVIEWER,
    *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> ReviewOutcome:
    """The person accepts the recommendation and the run becomes final (spec 11.2). Satisfied (4 or 5) is the normal way. A person
    who is not satisfied can accept only after all the rounds of changing the priorities have been used ("accept it anyway"): until
    then they are asked what they are not satisfied with. With no eligible supplier there is nothing to accept."""
    who = actor or REVIEWER
    try:
        score = reweight.check_satisfaction(satisfaction)
        with repository.connection() as conn:
            state = _load_state(conn, run_id)
            satisfied = reweight.is_satisfied(score)
            if not satisfied and reweight.rounds_left(state.feedback) > 0:
                raise review.ReviewError("You are not satisfied yet, so say what you are not satisfied with first.")
            _check_round(state, round_no)
            returned = False
            if not satisfied:                      # F21b: every round used and still not satisfied: back to the original best fit
                returned = restore_original(run_id, who, now=now)
                if returned:
                    state = _load_state(conn, run_id)
            board = _board(state.suppliers)
            chosen = review.recommended_of(board)
            if chosen is None:
                raise review.ReviewError("No supplier meets every must-have, so there is no recommendation to accept.")
            details = {"satisfaction": score, "recommended": chosen, "board": board, "weights": _weights_as_data(state.criteria),
                       "rounds_used": reweight.rounds_used(state.feedback), "accepted_anyway": not satisfied, "by": who}
            repository.add_feedback(conn, run_id, action=repository.ACTION_ACCEPT, created_at=_iso(now()), supplier_name=chosen,
                                    rating=score, details=details)
            repository.set_run_status(conn, run_id, repository.RUN_FINAL)
            audit.log_event(conn, run_id, who, "recommendation_accepted", {
                "supplier": chosen, "satisfaction": score, "accepted_anyway": not satisfied,
                "rounds_used": details["rounds_used"]})
        return ReviewOutcome(True, (RETURNED_TEXT.format(n=reweight.MAX_ROUNDS) + " " if returned else "")
                             + f"You accepted {chosen}. This evaluation is now final.")
    except (review.ReviewError, sqlite3.Error, OSError) as error:
        return _failed_outcome(error)


# ---------------------------------------------------------------- asking about the proposals (F14, spec 12)

NO_TEXT_TEXT = ("The proposal text is not available here any more: it is kept in this browser session only and never saved. "
                "Upload the PDFs again on New evaluation and evaluate them to ask questions about them.")
KEY_IN_QUESTION_TEXT = "What you wrote contains your API key. Remove it and try again. Nothing was sent."
DATABASE_BUSY_ASK_TEXT = "The question could not be answered because the database was busy or unavailable. Wait a few seconds and try again."


@dataclass(frozen=True)
class AskOutcome:
    """The result of one question. `ok` with an `answer` means there is something to show; otherwise `message` says why not.
    `hits` are the excerpts the answer was written from (best first), `searched` is the supplier whose proposal was searched (None: all
    of them), `narrowed` says the question named that supplier by itself, and `asked_ai` is False when no excerpt shared a word with
    the question, so the AI was not asked at all."""

    ok: bool
    message: str = ""
    answer: qa_agent.Answer | None = None
    hits: tuple[retrieval.Hit, ...] = ()
    searched: str | None = None
    narrowed: bool = False
    asked_ai: bool = False


def chunks_of(proposals: Sequence[Proposal]) -> tuple[pdf_tool.Chunk, ...]:
    """Every readable proposal cut into chunks of about 300 words (spec 12), tagged with the supplier's name as it is saved in the run and
    with the page. A file that could not be read, or a proposal with no name, adds nothing. Kept in the visitor's session, never saved."""
    chunks: list[pdf_tool.Chunk] = []
    for proposal in proposals:
        name = proposal.supplier_name.strip()
        if proposal.pdf is not None and name:
            chunks.extend(pdf_tool.build_chunks(proposal.pdf.pages, name))
    return tuple(chunks)


def ask_question(
    run_id: str, question: str, chunks: Sequence[pdf_tool.Chunk], settings: client.LLMSettings, supplier: str | None = None,
    actor: str = REVIEWER,
    *,
    backend: client.Backend | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> AskOutcome:
    """Answer one question about the proposals of a finished run. The search picks the best excerpts (`supplier` limits it to one
    proposal; with None, a question that names exactly one supplier is narrowed to it), the AI answers from them only, and Python
    checks the page references afterwards. A question that shares no word with any excerpt is answered "Not stated in the proposal."
    without asking the AI. Nothing about the evaluation changes: only the question, the sources and the answer are written to the
    audit log, and the tokens and cost of the call are added to the run. The proposal text itself is never written anywhere."""
    who = actor or REVIEWER
    try:
        text = qa_agent.clean_question(question)
        if not text:
            return AskOutcome(False, "Type a question first.")
        if settings.key_is_in(text):
            return AskOutcome(False, KEY_IN_QUESTION_TEXT)
        if not chunks:
            return AskOutcome(False, NO_TEXT_TEXT)
        with repository.connection() as conn:
            run = repository.get_run(conn, run_id)
        if run is None:
            return AskOutcome(False, "This run was not found.")
        if run.status not in (repository.RUN_EVALUATED, repository.RUN_FINAL):
            return AskOutcome(False, f"This run is {run.status.lower()}, so there is nothing to ask about.")
        index = retrieval.Index(chunks)
        if supplier is not None and supplier not in index.suppliers:
            return AskOutcome(False, f"There is no proposal text for {supplier} in this session. Choose another supplier or All suppliers.")
        named = index.supplier_named(text) if supplier is None else None
        searched = supplier or named
        hits = index.search(text, searched)

        base = {"question": text, "supplier": searched, "narrowed": named is not None, "requested_by": who,
                "sources": [{"supplier": h.supplier, "page": h.page, "chunk": h.number, "score": h.score} for h in hits]}
        if not hits:
            answer = qa_agent.not_stated_answer()
            with repository.connection() as conn:
                audit.log_event(conn, run_id, "AI", "question_answered", {**base, "answer": answer.text, "not_stated": True,
                                                                        "llm_calls": 0, "model": settings.model, "provider": settings.provider})
            return AskOutcome(True, "", answer, (), searched, named is not None, False)

        try:
            answer = qa_agent.answer_question(text, hits, settings, backend=backend, sleep=sleep)
        except client.LLMError as exc:
            _log_question_failure(run_id, base, exc.kind, settings)
            return AskOutcome(False, f"The AI could not be reached: {exc.user_message}", hits=hits, searched=searched, narrowed=named is not None)
        except qa_agent.QAError as exc:
            _log_question_failure(run_id, base, "answer", settings)
            return AskOutcome(False, exc.user_message, hits=hits, searched=searched, narrowed=named is not None)
        with repository.connection() as conn:
            repository.add_to_run_totals(conn, run_id, answer.tokens, answer.cost_usd)
            audit.log_event(conn, run_id, "AI", "question_answered", {
                **base, "answer": answer.text, "not_stated": answer.not_stated, "llm_calls": 1,
                "citations": [{"supplier": c.supplier, "pages": list(c.pages), "verified": c.verified} for c in answer.citations],
                "model": settings.model, "provider": settings.provider, "tokens": answer.tokens, "cost_usd": answer.cost_usd})
        return AskOutcome(True, "", answer, hits, searched, named is not None, True)
    except (sqlite3.Error, OSError):
        return AskOutcome(False, DATABASE_BUSY_ASK_TEXT)


def _log_question_failure(run_id: str, base: dict[str, Any], kind: str, settings: client.LLMSettings) -> None:
    """A question the AI could not answer is still written to the audit log (best effort: a locked database never hides the reason)."""
    try:
        with repository.connection() as conn:
            audit.log_event(conn, run_id, "AI", "question_failed", {**base, "kind": kind, "model": settings.model, "provider": settings.provider})
    except (sqlite3.Error, OSError):
        pass


# ---------------------------------------------------------------- negotiation suggestions (F20, spec 10.4)

NEGOTIATION_LOCKED_TEXT = (f"Negotiation points are made once all {reweight.MAX_ROUNDS} rounds of changing your priorities are used "
                           "and you are still not satisfied.")


@dataclass(frozen=True)
class NegotiationLine:
    key: str
    topic: str
    text: str            # what is shown: the AI's wording when it passed the checks, otherwise the plain text
    plain: str           # the plain text Python chose
    worded: bool         # True when `text` is the AI's wording


@dataclass(frozen=True)
class FitNote:
    """Why the final supplier suits what the person asked for in the rounds. `text` is the AI's wording when it passed the checks."""

    text: str
    plain: str
    worded: bool
    priorities: tuple[str, ...]      # what the person asked to count for more, in the order of the rounds, without repeats


@dataclass(frozen=True)
class NegotiationOutcome:
    ok: bool
    message: str = ""
    supplier: str = ""
    lines: tuple[NegotiationLine, ...] = ()
    used_ai: bool = False
    fit: FitNote | None = None


def _asked_for(feedback: Sequence[repository.FeedbackRow]) -> tuple[str, ...]:
    """The criteria the person asked to count for more, one per round, in order, each named once."""
    names: list[str] = []
    for row in feedback:
        if row.action == repository.ACTION_REWEIGHT:
            name = str(row.details.get("criterion") or "").strip()
            if name and name not in names:
                names.append(name)
    return tuple(names)


def _list_of(names: Sequence[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def fit_plain(supplier: str, priorities: Sequence[str], ppi: object) -> str:
    """The plain fit note, written by Python from the person's own choices."""
    asked = f"You asked for more weight on {_list_of(priorities)}. " if priorities else ""
    score = f" with a score of {ppi:.2f} against the other bids" if isinstance(ppi, (int, float)) and not isinstance(ppi, bool) else ""
    return f"{asked}{supplier} ranks first among the suppliers that meet every must-have{score}, so it is the best fit for what you asked for."


def _negotiation_inputs(run: repository.RunRecord, result: dict[str, Any]) -> tuple[tuple[insights.NegotiationPoint, ...], int]:
    gates = [insights.GateOutcome(str(g.get("gate_id", "")), str(g.get("name", "")), bool(g.get("passed")), str(g.get("reason", "")))
             for g in result.get("gate_details", []) if isinstance(g, dict)]
    rows, details = [], {}
    for c in result.get("criteria", []):
        if not isinstance(c, dict):
            continue
        rows.append(insights.CriterionResult(
            int(c["criterion_id"]), str(c["name"]), float(c["score"]), float(c["max_score"]), float(c["weight"]),
            c.get("benchmark"), c.get("gap"), c.get("relative_pct"), bool(c.get("filled_by_validator")), c.get("evidence_found")))
        said = " ".join(f"{c.get('justification', '')} {('Evidence: ' + str(c['evidence'])) if c.get('evidence') else ''}".split())
        if said:
            details[int(c["criterion_id"])] = said
    facts = result.get("extracted_facts") if isinstance(result.get("extracted_facts"), dict) else {}
    risks = [str(r) for r in result.get("risks", []) if isinstance(r, str)]
    points = insights.negotiation_points(gates, facts.get("grand_total_rs"), run.budget_rs, rows, risks, details=details)
    return points, len(rows)


# ---------------------------------------------------------------- the original best fit and the price warning (F21b)

RETURNED_TEXT = ("You were not satisfied after all {n} rounds, so the final recommendation is the best fit with the original weights "
                 "(your changes stay in the audit trail).")


def _original_weights(conn: sqlite3.Connection, state: _ReviewState) -> list[ranking.CriterionWeight]:
    """The weights before any of the person's changes: the marking scheme on the Criteria tab (the run's own weight for a criterion the Criteria tab lost)."""
    template = {c.criterion_id: c.weight for c in repository.list_criteria(conn, state.run.template_id)}
    return [ranking.CriterionWeight(c.criterion_id, template.get(c.criterion_id, c.weight), c.max_score) for c in state.criteria]


def _original_ranking(conn: sqlite3.Connection, state: _ReviewState) -> tuple[dict[str, dict[str, Any]], ranking.RankingResult, list[ranking.CriterionWeight]]:
    """Everyone ranked with the original weights and the lowest price winning, from the saved marks. Saves nothing."""
    weights = _original_weights(conn, state)
    source = {n: copy.deepcopy(dict(s.result)) for n, s in state.suppliers.items()}
    source = ai_price_source(conn, state.run.rfp_run_id, source, state.criteria)       # the AI's own Commercial Value marks
    return source, _rank_inputs(state, source, weights), weights


def original_best_fit(run_id: str) -> str | None:
    """The supplier the run would recommend with the original weights and the lowest price winning (None when nobody is eligible)."""
    try:
        with repository.connection() as conn:
            state = _load_state(conn, run_id, open_only=False)
            _source, ranked, _w = _original_ranking(conn, state)
    except (review.ReviewError, sqlite3.Error, OSError):
        return None
    eligible = {n: s.gates_passed for n, s in state.suppliers.items()}
    return review.recommended_of(review.standings(ranked, eligible))


def restore_original(run_id: str, actor: str = REVIEWER, *, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> bool:
    """The person used every round and is still not satisfied: the run goes back to the original weights and the lowest price winning,
    and everyone is ranked again from the saved marks. Returns True when something changed. Idempotent. The feedback rounds, the
    old weights and the old ranking stay in the audit trail; nothing is sent to the AI."""
    with repository.connection() as conn:
        state = _load_state(conn, run_id)
        weights = _original_weights(conn, state)
        direction_now = price_direction_of(state.run.criteria_snapshot)
        same_weights = all(abs(w.weight - c.weight) < 1e-9 for w, c in zip(weights, state.criteria))
        if same_weights and direction_now is None:
            return False
        benchmarks = repository.list_benchmarks(conn, state.run.template_id)
        terms = _terms_of(state.run, "the industry score cannot be worked out again")
        results, ranked, weights = _original_ranking(conn, state)
        new_criteria = tuple(replace(c, weight=w.weight) for c, w in zip(state.criteria, weights))
        industry_scores: dict[str, float] = {}
        for name, result in results.items():
            facts = result.get("extracted_facts")
            graded = industry.industry_score(facts if isinstance(facts, dict) else None, benchmarks, new_criteria, terms)
            result.update(industry_score=graded.score, industry_breakdown=_breakdown(graded))
            industry_scores[name] = graded.score
        old_ranked = _rank_inputs(state, {n: s.result for n, s in state.suppliers.items()})
        new_results = review.apply_ranking(results, ranked, old_ranked.warnings)
        eligible = {n: s.gates_passed for n, s in state.suppliers.items()}
        before, after = _board(state.suppliers), review.standings(ranked, eligible)
        by_id = {c.criterion_id: c.weight for c in new_criteria}
        snapshot = [{k: v for k, v in item.items() if k != "direction"} | {"weight": by_id[int(item["criterion_id"])]}
                    for item in state.run.criteria_snapshot]
        repository.update_run_weights(conn, run_id, json.dumps(snapshot))
        _save_ranking(conn, run_id, state, ranked, new_results, eligible, industry_scores)
        audit.log_event(conn, run_id, actor or REVIEWER, "returned_to_best_fit", {
            "rounds_used": reweight.rounds_used(state.feedback), "recommended_before": review.recommended_of(before),
            "recommended_after": review.recommended_of(after), "weights": [{"criterion_id": c.criterion_id, "name": c.name, "was": c.weight,
                                                                           "now": by_id[c.criterion_id]} for c in state.criteria]})
        _log_ranking(conn, run_id, ranked, "Back to the original weights (all rounds used, not satisfied)")
    return True


def overpay_warning(run_id: str) -> str | None:
    """A red warning when the buyer asked for the most expensive option and a better fit exists at a lower price: they would be paying
    more than the original best fit. None when the run does not want the dearest, or the dearest is also the best fit."""
    with repository.connection() as conn:
        run = repository.get_run(conn, run_id)
        if run is None or price_direction_of(run.criteria_snapshot) != insights.PRICE_HIGH:
            return None
        stored = {s.supplier_name: s for s in repository.list_supplier_results(conn, run_id)}
        if not stored:
            return None
    chosen = review.recommended_of(_board(stored))
    best = original_best_fit(run_id)
    if chosen is None or best is None or chosen == best:
        return None

    def total(name: str) -> float | None:
        facts = stored[name].result.get("extracted_facts")
        return insights.price_facts(facts if isinstance(facts, dict) else None, None).total_rs

    a, b = total(chosen), total(best)
    price = (f" ({evaluator.format_rupees(a)} against {evaluator.format_rupees(b)})" if a is not None and b is not None else "")
    return (f"You asked for the most expensive option, so {chosen} is recommended. You may be overpaying: {best} is the better fit "
            f"for what you need{price}, and it costs less." if a is not None and b is not None and a > b else
            f"You asked for the most expensive option, so {chosen} is recommended. {best} is the better fit for what you need{price}.")


@dataclass(frozen=True)
class Picks:
    """The two picks of a run where the buyer asked for something (a priority ticked or written, or a price wish)."""

    request: str                  # the best-ranked eligible supplier with the buyer's weights and price wish
    ideal: str                    # the best-ranked eligible supplier with the original weights and the AI's own marks
    same: bool
    overpay: str | None = None    # the red warning, when the buyer asked for the dearest and the ideal fit costs less
    request_rank_ideal: int | None = None   # the rank of the request's pick when everyone is ranked with the original weights
    ideal_rank_request: int | None = None   # the rank of the ideal fit when everyone is ranked with the buyer's weights
    request_rank: int | None = None         # the rank of the request's pick in the saved (request) ranking
    ideal_rank: int | None = None           # the rank of the ideal fit in the original ranking
    total: int = 0                          # how many suppliers were ranked


def picks_of(run_id: str) -> Picks | None:
    """Both picks of a run, or None when the buyer asked for nothing (the ranking then is the ideal fit) or nobody is eligible."""
    try:
        with repository.connection() as conn:
            state = _load_state(conn, run_id, open_only=False)
            default = {w.criterion_id: w.weight for w in _original_weights(conn, state)}
    except (review.ReviewError, sqlite3.Error, OSError):
        return None
    asked = price_direction_of(state.run.criteria_snapshot) is not None or any(
        abs(c.weight - default.get(c.criterion_id, c.weight)) > 1e-9 for c in state.criteria)
    if not asked:
        return None
    request = review.recommended_of(_board(state.suppliers))
    try:
        with repository.connection() as conn:
            _src, ranked, _w = _original_ranking(conn, state)
    except (sqlite3.Error, OSError, review.ReviewError):
        return None
    ideal_rank_of = {r.name: r.rank for r in ranked.suppliers}
    eligible = {n: s.gates_passed for n, s in state.suppliers.items()}
    ideal = review.recommended_of(review.standings(ranked, eligible))
    if request is None or ideal is None:
        return None
    saved_rank = {n: s.final_rank for n, s in state.suppliers.items()}
    return Picks(request, ideal, request == ideal, overpay_warning(run_id), ideal_rank_of.get(request), saved_rank.get(ideal),
                 saved_rank.get(request), ideal_rank_of.get(ideal), len(saved_rank))


def suggest_negotiation(
    run_id: str, supplier: str, settings: client.LLMSettings | None = None, actor: str = REVIEWER,
    *, backend: client.Backend | None = None, sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> NegotiationOutcome:
    """The final recommendation and negotiation points (spec 10.4 and 11.2), only once all rounds of changing the priorities were used,
    and only for the supplier Python recommends (the best-ranked one that meets every must-have, with the weights of the person's
    rounds). Python chooses the supplier and at most five points; the AI, when `settings` is given, only words them and a short note on
    why the supplier fits what the person asked for. A problem with the AI never stops this: the plain text is used. The points are saved with the run and the audit log. Nothing
    here changes a score or a rank, and no other supplier's name or price is sent to the AI."""
    who = actor or REVIEWER
    try:
        with repository.connection() as conn:
            run = repository.get_run(conn, run_id)
            stored = {s.supplier_name: s for s in repository.list_supplier_results(conn, run_id)} if run is not None else {}
            feedback = repository.list_feedback(conn, run_id) if run is not None else []
        if run is None or supplier not in stored:
            return NegotiationOutcome(False, "That supplier was not found in this run.", supplier)
        if reweight.rounds_left(feedback) > 0:         # the person's own rule: only after every round was used (spec 11.2)
            return NegotiationOutcome(False, NEGOTIATION_LOCKED_TEXT, supplier)
        returned = False
        if run.status == repository.RUN_EVALUATED:     # F21b: every round used and still not satisfied: back to the original best fit
            was_final = review.recommended_of(_board(stored))
            returned = restore_original(run_id, who, now=now)
            if returned:
                with repository.connection() as conn:
                    stored = {s.supplier_name: s for s in repository.list_supplier_results(conn, run_id)}
                if supplier == was_final:          # the page asked for the pick it showed; the pick is now the original best fit
                    supplier = review.recommended_of(_board(stored)) or supplier
        final = review.recommended_of(_board(stored))  # Python chooses: the best-ranked supplier that meets every must-have
        if final is None:
            return NegotiationOutcome(False, "No supplier meets every must-have, so there is no supplier to recommend or to negotiate with.", supplier)
        if supplier != final:
            return NegotiationOutcome(False, f"Negotiation points are made for the final recommendation, {final}.", supplier)
        result = stored[supplier].result
        if result.get("evaluation_failed"):
            return NegotiationOutcome(False, f"{supplier}'s evaluation failed, so there is nothing to base negotiation points on.", supplier)
        points, _count = _negotiation_inputs(run, result)
        if not points:
            return NegotiationOutcome(False, f"No negotiation points came up for {supplier}: every requirement is met and no criterion is behind.", supplier)
        asked = _asked_for(feedback)
        plain_fit = fit_plain(supplier, asked, stored[supplier].ppi)
        worded, ai_fit, tokens, cost, note = {}, None, None, None, None
        if settings is not None:
            others = [n for n in stored if n != supplier]
            try:
                reply = negotiator.word_points(points, others, settings, backend=backend, sleep=sleep, priorities=asked,
                                               strengths=[str(t) for t in result.get("pros", []) if isinstance(t, str)][:3])
                worded, ai_fit, tokens, cost = reply.lines, reply.fit, reply.tokens, reply.cost_usd
            except client.LLMError as exc:
                note = f"The AI could not be reached ({exc.user_message}), so the plain wording is shown."
            except negotiator.NegotiatorError as exc:
                tokens, cost, note = exc.tokens, exc.cost_usd, "The AI's wording could not be used, so the plain wording is shown."
        lines = tuple(NegotiationLine(p.key, p.topic, worded.get(p.key, p.text), p.text, p.key in worded) for p in points)
        fit = FitNote(ai_fit or plain_fit, plain_fit, ai_fit is not None, asked)
        saved = [{"key": ln.key, "topic": ln.topic, "text": ln.text, "plain": ln.plain, "worded": ln.worded} for ln in lines]
        with repository.connection() as conn:
            repository.add_negotiation(conn, run_id, supplier, saved, _iso(now()),
                                       {"text": fit.text, "plain": fit.plain, "worded": fit.worded, "priorities": list(asked)})
            if settings is not None:
                repository.add_to_run_totals(conn, run_id, tokens, cost)
            audit.log_event(conn, run_id, who, "negotiation_suggested", {
                "supplier": supplier, "points": [ln.key for ln in lines], "worded": sum(ln.worded for ln in lines),
                "priorities": list(asked), "fit_worded": fit.worded,
                "asked_ai": settings is not None, "problem": note, "model": settings.model if settings else None,
                "tokens": tokens, "cost_usd": cost})
        said = RETURNED_TEXT.format(n=reweight.MAX_ROUNDS) + (" " if returned and note else "") if returned else ""
        return NegotiationOutcome(True, said + (note or ""), supplier, lines, settings is not None and note is None, fit)
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError):
        return NegotiationOutcome(False, "The negotiation points could not be made because the saved results could not be read or the database was busy. Try again in a few seconds.", supplier)
