"""Orchestrator (feature F09, spec section 8): one batch, from the uploaded proposals to a saved ranking.

The LLM judges (inside `agents.evaluator`); Python decides (gates, scores, ranks and the industry score are
pure functions in `tools/`). This module only runs the steps in order, keeps the step log, and saves everything
under one run id, so nothing is lost if a run stops half way.

Steps run here (spec 8): input checks, create the run, read the documents, evaluate each supplier, gates, rank,
industry score, strength labels, save. Not here yet: the evidence check (F15) and the pros/cons wording (F16).

Rules kept here:
* The proposals come from the upload box only; nothing is read from `data/proposals/` (DECISIONS 82).
* Budget and quantity are inputs of the run (`PurchaseTerms`), stored with it (DECISIONS 69).
* The API key is used for the calls and is never written anywhere: not to the database, the audit log or a message.
* A bad reply twice from the model gives that supplier all zeros and the run goes on (spec 7.1). A provider problem
  (bad key, no network, timeout after its retry) stops the run: ranking a supplier that was never really evaluated
  would be misleading. Work done so far stays saved, with status "Failed".
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import PurePath
from typing import Any

from agents import evaluator
from db import audit, repository
from llm import client
from tools import gates as gates_tool
from tools import industry, insights, pdf_tool, ranking

MAX_PROPOSALS = 10
RECOMMENDED_MINIMUM = 4
EXPERIENCE_RATINGS = (1, 2, 3, 4, 5)
BLOCKED = "Blocked"        # the inputs were not acceptable; nothing was created
ATTEMPT_NO = 1             # the first evaluation of a supplier in a run (re-evaluations, F13, count up from here)


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
    if count == 0:
        errors.append("Upload at least one supplier proposal (PDF).")
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
        errors.append("There are no active criteria. Turn at least one on in Setup before evaluating.")
    else:
        total = repository.weights_total(criteria)
        if not repository.weights_are_valid(total):
            errors.append(f"The active criteria weights total {total:g}, not {repository.WEIGHT_TOTAL:g}. Fix them in Setup before evaluating.")
    return InputCheck(tuple(errors), tuple(notes))


# ---------------------------------------------------------------- the result of a run

@dataclass(frozen=True)
class SupplierSummary:
    """One row of the leaderboard (the full detail is saved in `supplier_results.result_json`)."""

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


LEADERBOARD_COLUMNS = ("Rank", "Supplier", "Overall /100", "Peer score (PPI)", "Industry score", "Submitted", "Experience", "Eligible")


def leaderboard_rows(suppliers: Sequence[SupplierSummary]) -> list[dict[str, Any]]:
    """The ranked table the screens show, one dict per supplier in rank order (spec 13.3, Overview columns)."""
    rows: list[dict[str, Any]] = []
    for supplier in suppliers:
        if supplier.evaluation_failed:
            eligible = "No: the evaluation failed (see warnings)"
        elif supplier.eligible:
            eligible = "Yes"
        else:
            eligible = "No: " + "; ".join(supplier.failed_gates)
        rows.append({
            "Rank": supplier.rank, "Supplier": supplier.name, "Overall /100": supplier.absolute_score,
            "Peer score (PPI)": supplier.ppi, "Industry score": supplier.industry_score,
            "Submitted": format_submitted(supplier.submission_date),
            "Experience": format_experience(supplier.experience_rating),
            "Eligible": eligible})
    return rows


def make_run_id(moment: datetime, token: str) -> str:
    """RUN-YYYYMMDD-HHMMSS-<4 hex>, in UTC like every stored timestamp."""
    return f"RUN-{moment.astimezone(timezone.utc):%Y%m%d-%H%M%S}-{token}"


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

    token_counts: list[int | None] = []
    costs: list[float | None] = []
    created = False
    try:
        log(f"Checked inputs: {len(proposals)} proposal(s), criteria weights total {total_weight:g}, "
            f"budget {_rupees(terms.budget_rs)} for {terms.quantity} laptop(s).")
        snapshot = [{"criterion_id": c.criterion_id, "name": c.name, "weight": c.weight, "max_score": c.max_score}
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
            rows = [repository.AttemptRow(c.criterion_id, c.score, c.justification, c.evidence, None,
                                          outcome.raw_reply if i == 0 else None)
                    for i, c in enumerate(outcome.scorecard.criteria)]
            with repository.connection() as conn:
                repository.add_attempt(conn, run_id, name, ATTEMPT_NO, rows, _iso(now()))
                audit.log_event(conn, run_id, "AI", "supplier_evaluated", {
                    "supplier": name, "attempt_no": ATTEMPT_NO, "llm_calls": outcome.attempts, "model": settings.model,
                    "tokens": outcome.tokens, "cost_usd": outcome.cost_usd, "evaluation_failed": outcome.failed,
                    "name_hidden_times": outcome.name_replacements})
                if outcome.warnings:
                    audit.log_event(conn, run_id, "AI", "validation_warnings",
                                    {"supplier": name, "warnings": list(outcome.warnings)})
            detail = "FAILED, all scores set to 0" if outcome.failed else ("2 tries" if outcome.attempts == 2 else "ok")
            used = "tokens not reported" if outcome.tokens is None else f"{outcome.tokens} tokens"
            log(f"Evaluated {name}: {detail}, {used}, {len(outcome.warnings)} warning(s).")

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
                        "evidence_found": None, "filled_by_validator": by_id[cr.criterion_id].filled_by_validator,
                    } for cr in r.criteria],
                    "extracted_facts": facts[r.name],
                    "industry_breakdown": {
                        "score": industry_for.score,
                        "attributes": [{"attribute_key": a.attribute_key, "label": a.label, "grade": a.grade,
                                        "points": a.points, "weight": a.weight, "reason": a.reason}
                                       for a in industry_for.attributes]},
                    "pros": [], "cons": [], "risks": list(card.risks), "overall_summary": card.overall_summary,
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
