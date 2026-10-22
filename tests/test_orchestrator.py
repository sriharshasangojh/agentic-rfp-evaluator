"""Orchestrator tests (feature F09): input checks and the whole run, against a fake model and a temporary database.

No API key or network is used. The fake model returns one prepared reply per supplier, in upload order, so the
spec's ranking fixture (section 16.1) can be pushed through the real pipeline: prompt -> reply -> validator ->
gates -> ranking -> industry score -> saved rows. Tests that read the 7 sample PDFs are skipped without a PDF reader.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents import orchestrator
from agents.orchestrator import Proposal, RunResult
from db import repository, seed
from llm import client
from tools import industry, pdf_tool, ranking

ROOT = Path(__file__).resolve().parents[1]
TERMS = industry.PurchaseTerms(budget_rs=6000000, quantity=100)
SETTINGS = client.LLMSettings("OpenAI", "test-model", "test-key-abc123")
NOW = datetime(2026, 10, 23, 12, 30, 15, tzinfo=timezone.utc)
needs_pdf_reader = pytest.mark.skipif(not pdf_tool.available_backends(), reason="no PDF reader installed")

# supplier -> (sample file, submission date, experience rating): the table in spec 1.3
SAMPLES = {
    "Apex Systems": ("apex_systems.pdf", date(2026, 10, 5), 4),
    "BrightPath Tech": ("brightpath_tech.pdf", date(2026, 10, 7), 2),
    "NexaWorks": ("nexaworks.pdf", date(2026, 10, 6), 4),
    "Horizon Datacom": ("horizon_datacom.pdf", date(2026, 10, 6), 3),
    "Orbit Digital": ("orbit_digital.pdf", date(2026, 10, 8), 5),
    "Zenora Systems": ("zenora_systems.pdf", date(2026, 10, 4), 3),
    "Vertexa Solutions": ("vertexa_solutions.pdf", date(2026, 10, 9), 3),
}
SCORES = {   # the first five are the spec's ranking fixture (16.1)
    "Apex Systems": [9, 7, 5, 9, 8], "BrightPath Tech": [5, 4, 8, 3, 3], "NexaWorks": [7, 9, 7, 7, 9],
    "Horizon Datacom": [7, 9, 7, 7, 9], "Orbit Digital": [7, 5, 7, 6, 9],
    "Zenora Systems": [8, 8, 9, 7, 7], "Vertexa Solutions": [8, 6, 1, 1, 7],
}
FIVE = ["Apex Systems", "BrightPath Tech", "NexaWorks", "Horizon Datacom", "Orbit Digital"]
BASE_FACTS = {"warranty_years": 3, "warranty_type": "on-site", "bis_named": True, "price_table_present": True, "delivery_weeks": 6}
FACTS = {
    "Apex Systems": {"grand_total_rs": 7398600, "unit_price_rs": 59000},
    "BrightPath Tech": {"warranty_years": 1, "warranty_type": "carry-in", "bis_named": None, "delivery_weeks": 4},
    "Vertexa Solutions": {"price_table_present": False},
}


def reply(name: str, **overrides) -> str:
    facts = {**BASE_FACTS, **FACTS.get(name, {}), **overrides}
    return json.dumps({
        "supplier_name": "Supplier A",
        "criteria": [{"criterion_id": i, "score": s, "justification": f"why {i}", "evidence": "Not stated in the proposal"}
                     for i, s in enumerate(SCORES[name], start=1)],
        "extracted_facts": facts, "risks": [], "overall_summary": "ok"})


def ok_response(text: str, tokens: int = 1000):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=SimpleNamespace(total_tokens=tokens))


def make_error(name: str, status: int | None = None, message: str = "") -> Exception:
    error = type(name, (Exception,), {})(message)
    if status is not None:
        error.status_code = status
    error.message = message
    return error


class FakeBackend:
    def __init__(self, *outcomes, cost: float = 0.01) -> None:
        self.outcomes, self.calls, self.cost_value = list(outcomes), [], cost

    def completion(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def supports_json_mode(self, settings) -> bool:
        return True

    def cost(self, response) -> float:
        return self.cost_value


def synthetic_pdf(name: str) -> pdf_tool.ExtractedPdf:
    pages = (f"Offer from {name}.\nWe offer laptops. Contact sales@{name.lower().replace(' ', '')}.example",
             f"Second page. {name} will deliver on time.")
    return pdf_tool.ExtractedPdf(sha256=hashlib.sha256(name.encode()).hexdigest(), pages=pages, backend="pypdf")


def proposal(name: str, pdf=None, **changes) -> Proposal:
    file_name, when, rating = SAMPLES.get(name, (f"{name.lower().replace(' ', '_')}.pdf", date(2026, 10, 1), 3))
    base = Proposal(file_name, name, when, rating, pdf or synthetic_pdf(name))
    return dataclasses.replace(base, **changes)


@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


@pytest.fixture
def criteria():
    with repository.connection() as conn:
        return repository.list_criteria(conn, active_only=True)


def run(names=FIVE, *outcomes, terms=TERMS, proposals=None, **options) -> tuple[RunResult, FakeBackend, list[str]]:
    backend = FakeBackend(*(outcomes or [ok_response(reply(n)) for n in names]))
    seen: list[str] = []
    result = orchestrator.run_evaluation(
        proposals if proposals is not None else [proposal(n) for n in names], terms, SETTINGS, "Sri Harsha",
        progress=seen.append, backend=backend, sleep=lambda _s: None, clock=options.pop("clock", lambda: 5.0),
        now=lambda: NOW, token=options.pop("token", lambda: "ab12"), **options)
    return result, backend, seen


def rows(sql: str, *args):
    with repository.connection() as conn:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]


def whole_database() -> str:
    with repository.connection() as conn:
        return "\n".join(conn.iterdump())


# ---------------------------------------------------------------- input checks

def test_a_good_batch_has_no_errors(criteria):
    check = orchestrator.check_inputs([proposal(n) for n in FIVE], criteria)
    assert check.ok and check.errors == () and check.notes == ()


def test_no_files_and_too_many_files_are_blocked(criteria):
    assert orchestrator.check_inputs([], criteria).errors == ("Upload at least one supplier proposal (PDF).",)
    eleven = [proposal(f"Supplier {i}") for i in range(11)]
    assert orchestrator.check_inputs(eleven, criteria).errors == (
        "You uploaded 11 files, but at most 10 can be evaluated at once. Remove 1.",)
    assert orchestrator.check_inputs(eleven[:10], criteria).ok


@pytest.mark.parametrize("count, note", [(1, True), (3, True), (4, False), (7, False)])
def test_fewer_than_four_proposals_get_an_info_note(count, note, criteria):
    check = orchestrator.check_inputs([proposal(f"Supplier {i}") for i in range(count)], criteria)
    assert check.ok and (check.notes == ("The brief expects at least 4 for a meaningful peer comparison.",)) is note


def test_every_proposal_needs_a_name_and_a_date(criteria):
    check = orchestrator.check_inputs([proposal("A", supplier_name="  "), proposal("B", submission_date=None)], criteria)
    assert "Enter a supplier name for a.pdf." in check.errors
    assert "Pick a submission date for B." in check.errors


def test_the_experience_rating_is_optional(criteria):
    check = orchestrator.check_inputs([proposal("Acme", experience_rating=None), proposal("Other", experience_rating=3)], criteria)
    assert check.ok and check.errors == ()


@pytest.mark.parametrize("rating", [0, 6, -1, True, 3.5, 4.0, 3.0, "4"])
def test_a_given_experience_rating_must_be_a_whole_number_from_1_to_5(rating, criteria):
    check = orchestrator.check_inputs([proposal("Acme", experience_rating=rating)], criteria)
    assert check.errors == ("The experience rating for Acme must be a whole number from 1 to 5, or left empty.",)


@pytest.mark.parametrize("rating", [1, 2, 3, 4, 5])
def test_all_five_ratings_are_accepted(rating, criteria):
    assert orchestrator.check_inputs([proposal("Acme", experience_rating=rating)], criteria).ok


def test_duplicate_names_are_refused_ignoring_capitals_and_spaces(criteria):
    check = orchestrator.check_inputs([proposal("Acme", file_name="one.pdf"), proposal("Other"),
                                       proposal("Acme", supplier_name=" ACME ", file_name="two.pdf")], criteria)
    assert check.errors == ("The supplier name 'Acme' is used for 2 files (one.pdf, two.pdf). Each supplier needs its own name.",)


def test_an_unreadable_or_textless_pdf_is_refused_with_its_reason(criteria):
    textless = pdf_tool.ExtractedPdf("0" * 64, ("",), "pypdf")
    check = orchestrator.check_inputs([
        dataclasses.replace(proposal("Broken"), pdf=None, read_error="This file is not a PDF."),
        dataclasses.replace(proposal("Scan"), pdf=textless)], criteria)
    assert check.errors == ("broken.pdf: This file is not a PDF.",
                            "scan.pdf has no readable text (it may be a scanned image). Upload a text-based PDF.")


def test_all_problems_are_listed_together(criteria):
    check = orchestrator.check_inputs([proposal("A", supplier_name=""), proposal("B", experience_rating=9)], criteria)
    assert len(check.errors) == 2


def test_the_weights_must_total_100_and_a_criterion_must_be_active(criteria):
    heavy = [dataclasses.replace(c, weight=c.weight + (5 if c.criterion_id == 1 else 0)) for c in criteria]
    error = orchestrator.check_inputs([proposal("A")], heavy).errors
    assert error == ("The active criteria weights total 105, not 100. Fix them in Setup before evaluating.",)
    assert orchestrator.check_inputs([proposal("A")], []).errors == (
        "There are no active criteria. Turn at least one on in Setup before evaluating.",)


@needs_pdf_reader
def test_reading_a_pdf_gives_the_text_or_a_friendly_reason():
    pdf, error = orchestrator.read_pdf((ROOT / "data" / "proposals" / "apex_systems.pdf").read_bytes())
    assert error is None and pdf is not None and pdf.has_text and pdf.page_count == 6
    assert orchestrator.read_pdf(b"this is not a pdf") == (None, "This file is not a PDF.")


def test_run_ids_look_like_the_spec_and_are_in_utc():
    assert orchestrator.make_run_id(NOW, "ab12") == "RUN-20261023-123015-ab12"
    local = datetime(2026, 10, 23, 18, 0, 0, tzinfo=timezone(__import__("datetime").timedelta(hours=5, minutes=30)))
    assert orchestrator.make_run_id(local, "ffff") == "RUN-20261023-123000-ffff"


@pytest.mark.parametrize("text, expected", [
    ("Response to RFP\nSubmission date: 5 October 2026\nMore text", date(2026, 10, 5)),
    ("SUBMISSION DATE:\n8 Oct 2026", date(2026, 10, 8)),
    ("Date of submission \u2013 7th October, 2026", date(2026, 10, 7)),
    ("Submitted on October 5, 2026", date(2026, 10, 5)),
    ("Submission date: Sept 9 2026", date(2026, 9, 9)),
    ("Submission date: 2026-10-05", date(2026, 10, 5)),
    ("Submission date: 05/10/2026", date(2026, 10, 5)),       # day first, as written in India
    ("Submission date: 5.10.2026", date(2026, 10, 5)),
    ("[Page 1]\nSubmission date: 31 December 2026", date(2026, 12, 31)),
    ("Submission date: 31 February 2026\nSubmission date: 3 March 2026", date(2026, 3, 3)),   # an impossible date is skipped
    ("Offer letter\nDated 12-Oct-2026\nSome text", date(2026, 10, 12)),
    ("Cover page\nDate: 3rd of November, 2026\n", date(2026, 11, 3)),                       # a line that starts with "Date:"
    ("Proposal date - 1 Oct 2026", date(2026, 10, 1)),
    ("Submitted: 08/10/2026", date(2026, 10, 8)),
    ("Response date: 9 Oct. 2026", date(2026, 10, 9)),
    ("Submitted by Apex on time.\nSubmitted on 4 October 2026", date(2026, 10, 4)),       # a label without a date is skipped
    ("Date: 1 October 2026\nSubmission date: 5 October 2026", date(2026, 10, 5)),         # a stronger label beats a plain "Date:" line
    ("Submission date: 1 January 1990", date(1990, 1, 1)),                                  # the first year accepted
    ("Submission date: 31 December 2100", date(2100, 12, 31)),                              # the last year accepted
])
def test_the_submission_date_is_read_from_the_proposal(text, expected):
    assert orchestrator.find_submission_date(text) == expected


@pytest.mark.parametrize("text", [
    "", "no date here", "This offer is valid for 60 days from 5 October 2026.", "Submission date: soon",
    "Submission date: 31 February 2026", "Submission date: 13/13/2026", "Submission date: 5 Octember 2026", "Submission date:",
    "Prepared by Sales on 5 October 2026", "Submitted to Meridian on 5 October 2026", "Our update: date 5 October 2026",
    "Submission date: 5 October 1850", "Submission date: 5 October 2150",
    "Submission date: 31 December 1989", "Submission date: 1 January 2101",                 # one year outside the accepted years
])
def test_no_submission_date_is_made_up(text):
    assert orchestrator.find_submission_date(text) is None


@needs_pdf_reader
@pytest.mark.parametrize("name", list(SAMPLES))
def test_each_sample_proposal_states_the_date_the_spec_lists(name):
    file, when, _rating = SAMPLES[name]
    pdf = pdf_tool.extract_pdf_file(ROOT / "data" / "proposals" / file)
    assert orchestrator.find_submission_date(pdf.text) == when


TODAY = date(2026, 10, 23)


def pages(*texts: str) -> pdf_tool.ExtractedPdf:
    return pdf_tool.ExtractedPdf("0" * 64, tuple(texts), "pypdf")


@pytest.mark.parametrize("document, expected, source", [
    # a labelled date wins, on any page
    (pages("Cover\nSubmission date: 5 October 2026\n"), date(2026, 10, 5), "labelled"),
    (pages("Cover with 1 October 2026 on it", "p2\nSubmitted on: 08/10/2026"), date(2026, 10, 8), "labelled"),
    (pages("Prepared 1 October 2026", "p2\nDate: 9 October 2026"), date(2026, 10, 9), "labelled"),
    # no label: the first date on page 1 that is not a validity period, deadline or delivery date
    (pages("Proposal for the RFP\nPrepared 7 October 2026 by our sales team"), date(2026, 10, 7), "first date on page 1"),
    (pages("hello October 5, 2026 bye"), date(2026, 10, 5), "first date on page 1"),
    (pages("Submitted by Apex on 6-Oct-2026 to the buyer"), date(2026, 10, 6), "first date on page 1"),
    (pages("This offer is valid for 60 days from 5 October 2026.\nPrepared 3 October 2026"), date(2026, 10, 3), "first date on page 1"),
    (pages("Delivery within 6 weeks. Final delivery by 1 December 2026.\nPrepared on 2 October 2026"), date(2026, 10, 2), "first date on page 1"),
    (pages("Prepared 3 October 2026 and updated 7 October 2026"), date(2026, 10, 3), "first date on page 1"),     # the first, not the last
    (pages("Prepared October 3, 2026, then updated 07/10/2026"), date(2026, 10, 3), "first date on page 1"),     # reading order, not format order
    (pages("Offer valid for 60 days.\nPrepared 5 October 2026"), date(2026, 10, 5), "first date on page 1"),     # a word on the line above is not about this date
    (pages("Delivery on 1 December 2026.\nPrepared 2 October 2026"), date(2026, 10, 2), "first date on page 1"),
    # nothing usable: today
    (pages("Offer valid until 9 October 2026. Delivery by 1 December 2026."), TODAY, "today"),
    (pages("No dates here, only the year 2026."), TODAY, "today"),
    (pages("cover", "A date on page two only: 5 October 2026"), TODAY, "today"),
    (pages(""), TODAY, "today"),
    (pdf_tool.ExtractedPdf("0" * 64, (), "pypdf"), TODAY, "today"),
])
def test_a_submission_date_is_suggested_for_any_pdf(document, expected, source):
    suggestion = orchestrator.suggest_submission_date(document, TODAY)
    assert (suggestion.date, suggestion.source) == (expected, source)
    assert suggestion.source in (orchestrator.LABELLED, orchestrator.FIRST_ON_PAGE_ONE, orchestrator.TODAY)


@pytest.mark.parametrize("other_date", [
    "Offer valid till 9 October 2026", "Prices hold until 9 October 2026", "This quote expires on 9 October 2026",
    "Questions deadline: 9 October 2026", "Payment due 9 October 2026", "Bids closing 9 October 2026",
    "Pricing is firm before 9 October 2026", "Issued 9 October 2026", "Issue date: 9 October 2026",
    "Opening of bids: 9 October 2026", "Complete within 9 October 2026", "Delivery on 9 October 2026", "Final delivery by 9 October 2026",
])
def test_a_validity_deadline_or_delivery_date_is_not_taken_as_the_submission_date(other_date):
    suggestion = orchestrator.suggest_submission_date(pages(f"{other_date}\nPrepared 2 October 2026"), TODAY)
    assert (suggestion.date, suggestion.source) == (date(2026, 10, 2), "first date on page 1")


@needs_pdf_reader
@pytest.mark.parametrize("name", list(SAMPLES))
def test_each_sample_proposal_gets_its_labelled_date(name):
    file, when, _rating = SAMPLES[name]
    pdf = pdf_tool.extract_pdf_file(ROOT / "data" / "proposals" / file)
    suggestion = orchestrator.suggest_submission_date(pdf, TODAY)
    assert (suggestion.date, suggestion.source) == (when, "labelled")


def test_the_suggested_supplier_name_comes_from_the_file_name():
    assert orchestrator.default_supplier_name("apex_systems.pdf") == "Apex Systems"
    assert orchestrator.default_supplier_name("Nexa-Works final.PDF") == "Nexa Works Final"
    assert orchestrator.default_supplier_name("C:/x/horizon_datacom.pdf") == "Horizon Datacom"


# ---------------------------------------------------------------- a whole run

def test_the_spec_fixture_gives_the_spec_ranking_through_the_whole_pipeline():
    result, backend, _ = run()
    assert (result.status, result.run_id) == ("Evaluated", "RUN-20261023-123015-ab12")
    table = [(s.rank, s.name, s.absolute_score, s.ppi) for s in result.suppliers]
    assert table == [(1, "Apex Systems", 77.0, 86.94), (2, "NexaWorks", 76.0, 86.39), (3, "Horizon Datacom", 76.0, 86.39),
                     (4, "Orbit Digital", 66.0, 75.28), (5, "BrightPath Tech", 48.0, 55.56)]
    assert result.tie_break_explanations == ("NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)",)
    assert len(backend.calls) == 5


def test_the_supplier_summaries_come_back_in_rank_order_with_their_scores_and_checks():
    result, _, _ = run()
    first, last = result.suppliers[0], result.suppliers[-1]
    assert [s.name for s in result.suppliers] == ["Apex Systems", "NexaWorks", "Horizon Datacom", "Orbit Digital", "BrightPath Tech"]
    assert [s.rank for s in result.suppliers] == [1, 2, 3, 4, 5]
    assert (first.absolute_score, first.ppi, first.submission_date, first.experience_rating, first.eligible) == (77.0, 86.94, date(2026, 10, 5), 4, True)
    assert not last.eligible and last.failed_gates == ("Warranty: 1 year (at least 3 required)", "Certification: Not stated")
    assert orchestrator.format_submitted(last.submission_date) == "7 Oct 2026"


def test_unrated_suppliers_are_ranked_saved_and_shown_as_not_rated():
    unrated = [proposal(n, experience_rating=None) for n in FIVE]
    result, _, _ = run(proposals=unrated)
    assert result.status == "Evaluated"
    # NexaWorks and Horizon tie on score and date; with no ratings the name decides: Horizon comes first
    assert [s.name for s in result.suppliers] == ["Apex Systems", "Horizon Datacom", "NexaWorks", "Orbit Digital", "BrightPath Tech"]
    assert result.tie_break_explanations == ("Horizon Datacom above NexaWorks: supplier name comes first alphabetically (Horizon Datacom vs NexaWorks)",)
    assert {r[0] for r in rows("SELECT experience_rating FROM supplier_results")} == {None}
    detail = json.loads(rows("SELECT result_json FROM supplier_results WHERE supplier_name = 'Apex Systems'")[0][0])
    assert detail["experience_rating"] is None
    assert {orchestrator.format_experience(s.experience_rating) for s in result.suppliers} == {"Not rated"}


def test_a_rated_supplier_beats_an_unrated_one_that_ties_with_it():
    mixed = [proposal("Apex Systems"), proposal("NexaWorks", experience_rating=None), proposal("Horizon Datacom", experience_rating=1)]
    result, _, _ = run(["Apex Systems", "NexaWorks", "Horizon Datacom"], proposals=mixed)
    assert [s.name for s in result.suppliers] == ["Horizon Datacom", "NexaWorks", "Apex Systems"]   # the two tied leaders, then Apex
    assert result.tie_break_explanations == ("Horizon Datacom above NexaWorks: higher experience rating (1 vs not rated)",)
    table = {s.name: orchestrator.format_experience(s.experience_rating) for s in result.suppliers}
    assert table == {"Apex Systems": "4", "Horizon Datacom": "1", "NexaWorks": "Not rated"}


def test_a_failed_evaluation_is_marked_in_the_summary():
    good = [ok_response(reply(n)) for n in FIVE]
    good[0:1] = [ok_response("nope"), ok_response("nope again")]
    result, _, _ = run(FIVE, *good)
    apex = next(s for s in result.suppliers if s.name == "Apex Systems")
    assert apex.evaluation_failed and not apex.eligible and apex.absolute_score == 0.0


def test_the_run_and_its_settings_are_saved():
    result, _, _ = run()
    run_row = rows("SELECT rfp_run_id, status, provider, model, temperature, template_id, reviewer_name, budget_rs, quantity, "
                   "total_tokens, total_cost_usd, duration_seconds, created_at FROM rfp_runs")
    assert run_row == [(result.run_id, "Evaluated", "OpenAI", "test-model", 0.0, "startup_laptops", "Sri Harsha", 6000000.0, 100,
                        5000, 0.05, 0.0, "2026-10-23T12:30:15+00:00")]
    snapshot = json.loads(rows("SELECT criteria_snapshot_json FROM rfp_runs")[0][0])
    assert [c["criterion_id"] for c in snapshot] == [1, 2, 3, 4, 5] and snapshot[0] == {
        "criterion_id": 1, "name": "Technical Capability", "weight": 30.0, "max_score": 10.0}
    assert (result.tokens, result.cost_usd) == (5000, pytest.approx(0.05))


def test_spaces_around_a_supplier_name_are_dropped_everywhere():
    padded = [proposal("Apex Systems", supplier_name="  Apex Systems "), proposal("NexaWorks", supplier_name="NexaWorks  ")]
    result, _, seen = run(["Apex Systems", "NexaWorks"], proposals=padded)
    assert sorted(s.name for s in result.suppliers) == ["Apex Systems", "NexaWorks"]
    for table in ("run_documents", "evaluation_attempts", "supplier_results"):
        assert {n for (n,) in rows(f"SELECT DISTINCT supplier_name FROM {table}")} == {"Apex Systems", "NexaWorks"}
    assert "Evaluating Apex Systems (1 of 2) with openai/test-model..." in seen


def test_documents_and_every_attempt_are_saved():
    run()
    documents = rows("SELECT supplier_name, file_name, sha256, pages FROM run_documents ORDER BY id")
    assert documents[0] == ("Apex Systems", "apex_systems.pdf", hashlib.sha256(b"Apex Systems").hexdigest(), 2)
    assert len(documents) == 5
    attempts = rows("SELECT supplier_name, attempt_no, criterion_id, score, justification, evidence, evidence_found, raw_json "
                    "FROM evaluation_attempts WHERE supplier_name = 'Apex Systems' ORDER BY criterion_id")
    assert [(a[1], a[2], a[3]) for a in attempts] == [(1, 1, 9.0), (1, 2, 7.0), (1, 3, 5.0), (1, 4, 9.0), (1, 5, 8.0)]
    assert attempts[0][4:7] == ("why 1", "Not stated in the proposal", None)
    assert attempts[0][7] == reply("Apex Systems") and all(a[7] is None for a in attempts[1:])   # the raw reply is stored once
    assert rows("SELECT COUNT(*) FROM evaluation_attempts") == [(25,)]


def test_each_supplier_result_holds_everything_the_screens_need():
    run()
    saved = rows("SELECT supplier_name, submission_date, experience_rating, absolute_score, ppi, industry_score, final_rank, "
                 "gates_passed, result_json FROM supplier_results ORDER BY final_rank")
    assert [(r[0], r[6], r[7]) for r in saved] == [("Apex Systems", 1, 1), ("NexaWorks", 2, 1), ("Horizon Datacom", 3, 1),
                                                   ("Orbit Digital", 4, 1), ("BrightPath Tech", 5, 0)]
    apex = json.loads(saved[0][8])
    assert (saved[0][1], saved[0][2]) == ("2026-10-05", 4)
    assert apex["rank"] == 1 and apex["eligible"] is True and apex["gates"] == {
        "warranty": "pass", "certification": "pass", "price_table": "pass", "delivery": "pass"}
    first = apex["criteria"][0]
    assert first == {"criterion_id": 1, "name": "Technical Capability", "score": 9.0, "max_score": 10.0, "benchmark": 9.0, "gap": 0.0,
                     "relative_pct": 100.0, "weight": 30.0, "strength": "Strong", "justification": "why 1",
                     "evidence": "Not stated in the proposal", "evidence_found": None, "filled_by_validator": False}
    assert [c["strength"] for c in apex["criteria"]] == ["Strong", "Average", "Average", "Strong", "Strong"]
    assert apex["extracted_facts"]["grand_total_rs"] == 7398600 and apex["extracted_facts"]["bis_named"] is True
    assert apex["industry_breakdown"]["score"] == saved[0][5] and len(apex["industry_breakdown"]["attributes"]) == 8
    assert apex["pros"] == [] and apex["cons"] == [] and apex["evaluation_failed"] is False
    assert apex["document_sha256"] == hashlib.sha256(b"Apex Systems").hexdigest() and apex["pages"] == 2
    nexa = json.loads(saved[1][8])
    assert nexa["tie_break_explanation"] == "NexaWorks above Horizon Datacom: higher experience rating (4 vs 3)"
    assert json.loads(saved[2][8])["tie_break_explanation"] is None


def test_gates_decide_eligibility_and_say_why():
    result, _, _ = run()
    by_name = {s.name: s for s in result.suppliers}
    assert by_name["Apex Systems"].eligible and by_name["Apex Systems"].failed_gates == ()
    assert not by_name["BrightPath Tech"].eligible
    assert by_name["BrightPath Tech"].failed_gates == ("Warranty: 1 year (at least 3 required)", "Certification: Not stated")
    detail = json.loads(rows("SELECT result_json FROM supplier_results WHERE supplier_name = 'BrightPath Tech'")[0][0])
    assert detail["eligible"] is False and detail["gates"]["warranty"] == "fail" and detail["gates"]["delivery"] == "pass"


def test_the_saved_result_has_the_gate_and_industry_details_the_overview_reads():
    run()
    detail = json.loads(rows("SELECT result_json FROM supplier_results WHERE supplier_name = 'BrightPath Tech'")[0][0])
    failed = [(g["name"], g["reason"]) for g in detail["gate_details"] if not g["passed"]]
    assert failed == [("Warranty", "1 year (at least 3 required)"), ("Certification", "Not stated")]
    assert set(detail["gate_details"][0]) == {"gate_id", "name", "passed", "reason"}
    attributes = detail["industry_breakdown"]["attributes"]
    assert set(attributes[0]) == {"attribute_key", "label", "grade", "points", "weight", "reason"}
    assert {a["grade"] for a in attributes} <= {industry.ABOVE, industry.MEETS, industry.BELOW, industry.NOT_STATED}
    assert detail["evaluation_failed"] is False


def test_dates_and_ratings_are_written_the_same_way_on_every_leaderboard():
    assert orchestrator.format_submitted(date(2026, 10, 5)) == "5 Oct 2026"
    assert orchestrator.format_submitted(date(2026, 12, 31)) == "31 Dec 2026"
    assert [orchestrator.format_experience(r) for r in (None, 1, 4)] == ["Not rated", "1", "4"]


def test_a_supplier_that_fails_a_gate_is_still_ranked():
    result, _, _ = run()
    assert [s.rank for s in result.suppliers] == [1, 2, 3, 4, 5] and result.suppliers[-1].name == "BrightPath Tech"


def test_the_industry_warning_about_an_estimated_price_is_kept():
    result, _, _ = run(["Apex Systems", "NexaWorks"])
    assert result.suppliers[0].industry_score > 0


def test_the_audit_log_tells_the_story_in_order():
    result, _, _ = run()
    actions = [(a, b) for a, b in rows("SELECT actor, action FROM audit_log ORDER BY id")]
    assert actions == ([("system", "run_created"), ("system", "documents_read")]
                       + [("AI", "supplier_evaluated")] * 5 + [("system", "gate_results")] * 5
                       + [("system", "ranking_computed"), ("system", "run_evaluated")])
    first = json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'supplier_evaluated'")[0][0])
    assert first["supplier"] == "Apex Systems" and first["attempt_no"] == 1 and first["model"] == "test-model"
    ranking_event = json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'ranking_computed'")[0][0])
    assert [t["supplier"] for t in ranking_event["top3"]] == ["Apex Systems", "NexaWorks", "Horizon Datacom"]
    assert ranking_event["tie_break_explanations"] == list(result.tie_break_explanations)


def test_validation_warnings_go_to_the_audit_log():
    broken = json.loads(reply("Apex Systems"))
    broken["criteria"][0]["score"] = 12
    result, _, _ = run(["Apex Systems"], ok_response(json.dumps(broken)))
    event = json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'validation_warnings'")[0][0])
    assert event["supplier"] == "Apex Systems" and any("12" in w for w in event["warnings"])
    assert any("12" in w for w in result.suppliers[0].warnings)


def test_the_step_log_is_given_live_and_returned():
    result, _, seen = run()
    assert list(result.steps) == seen
    assert seen[0].startswith("Checked inputs: 5 proposal(s), criteria weights total 100, budget Rs 60,00,000 for 100 laptop(s).")
    assert seen[1] == "Created run RUN-20261023-123015-ab12."
    assert [s for s in seen if s.startswith("Evaluating")][0] == "Evaluating Apex Systems (1 of 5) with openai/test-model..."
    assert "Evaluated Apex Systems: ok, 1000 tokens, 0 warning(s)." in seen
    assert "Checked 4 gate(s) for 5 supplier(s): 1 not eligible (BrightPath Tech)." in seen
    assert seen[-3].startswith("Ranked 5 supplier(s). 1. Apex Systems (peer score 86.94), 2. NexaWorks")
    assert seen[-1].startswith("Saved the results. Run RUN-20261023-123015-ab12 evaluated 5 supplier(s)")


def test_the_duration_is_measured_with_the_clock():
    ticks = iter(range(0, 1000, 3))
    result, _, _ = run(clock=lambda: float(next(ticks)))
    assert result.duration_seconds > 0 and rows("SELECT duration_seconds FROM rfp_runs") == [(result.duration_seconds,)]


def test_upload_order_does_not_change_the_ranking():
    forward, _, _ = run(FIVE)
    backward, _, _ = run(FIVE[::-1], token=lambda: "cd34")
    assert {s.name: s.rank for s in forward.suppliers} == {s.name: s.rank for s in backward.suppliers}


def test_one_supplier_is_allowed_with_a_warning():
    result, _, _ = run(["Apex Systems"])
    assert result.status == "Evaluated" and result.suppliers[0].ppi == 100.0
    assert any("Only one supplier" in w for w in result.suppliers[0].warnings)


# ---------------------------------------------------------------- the model, the key and the buyer's numbers

def test_the_supplier_names_are_hidden_from_the_model_in_every_call():
    _, backend, _ = run()
    sent = re.sub(r"\s+", "", "\n".join(m["content"] for call in backend.calls for m in call["messages"])).lower()
    for name in FIVE:
        assert name.replace(" ", "").lower() not in sent
    assert all(call["temperature"] == 0 and call["model"] == "openai/test-model" for call in backend.calls)


def test_the_api_key_is_never_stored_or_shown():
    result, backend, seen = run()
    assert SETTINGS.api_key not in whole_database()
    assert SETTINGS.api_key not in "\n".join([*seen, result.message, repr(result)])
    assert all(call["api_key"] == SETTINGS.api_key for call in backend.calls)   # only the provider call gets it


def test_the_proposal_text_itself_is_not_saved():
    run()
    assert "We offer laptops" not in whole_database() and "Second page" not in whole_database()


def test_the_runs_budget_and_quantity_reach_the_model_and_the_price_grade():
    tight = industry.PurchaseTerms(budget_rs=2500000, quantity=50)   # Rs 50,000 per laptop
    _, backend, _ = run(["Apex Systems"], terms=tight)
    sent = backend.calls[0]["messages"][1]["content"]
    assert "Quantity: 50 laptops, one model" in sent and "About Rs 25,00,000" in sent
    assert rows("SELECT budget_rs, quantity FROM rfp_runs") == [(2500000.0, 50)]

    def price_grade() -> str:
        detail = json.loads(rows("SELECT result_json FROM supplier_results ORDER BY rowid DESC")[0][0])
        return next(a["grade"] for a in detail["industry_breakdown"]["attributes"] if a["attribute_key"] == "unit_price")

    # Apex quotes Rs 59,000 per laptop: over a Rs 50,000 budget per laptop, but inside the usual Rs 60,000 one
    assert price_grade() == "Below"
    run(["Apex Systems"], token=lambda: "ee55")
    assert price_grade() == "Meets"


def test_a_provider_like_anthropic_is_called_with_its_prefix():
    backend = FakeBackend(ok_response(reply("Apex Systems")))
    result = orchestrator.run_evaluation([proposal("Apex Systems")], TERMS, client.LLMSettings("Anthropic", "claude-x", "test-key-z"),
                                         backend=backend, sleep=lambda _s: None, now=lambda: NOW, token=lambda: "ab12")
    assert result.status == "Evaluated" and backend.calls[0]["model"] == "anthropic/claude-x"
    assert rows("SELECT provider, model, reviewer_name FROM rfp_runs") == [("Anthropic", "claude-x", "")]


# ---------------------------------------------------------------- when things go wrong

def test_a_blocked_batch_creates_nothing_and_calls_no_model():
    result, backend, _ = run(proposals=[proposal("Acme"), proposal("acme")])
    assert (result.status, result.run_id, backend.calls) == ("Blocked", "", [])
    assert result.message.startswith("The run was not started: The supplier name 'Acme' is used for 2 files")
    assert rows("SELECT COUNT(*) FROM rfp_runs") == [(0,)]


def test_a_reply_that_is_not_json_twice_gives_zeros_and_the_run_goes_on():
    good = [ok_response(reply(n)) for n in FIVE]
    good[2:3] = [ok_response("not json"), ok_response("still not json")]   # NexaWorks fails twice, then the rest continue
    result, backend, _ = run(FIVE, *good)
    assert result.status == "Evaluated" and len(backend.calls) == 6
    nexa = next(s for s in result.suppliers if s.name == "NexaWorks")
    assert nexa.evaluation_failed and nexa.absolute_score == 0.0 and any("Evaluation failed" in w for w in nexa.warnings)
    assert rows("SELECT COUNT(*) FROM supplier_results") == [(5,)]


def test_a_bad_key_stops_the_run_but_keeps_the_work_done():
    outcomes = [ok_response(reply("Apex Systems")), ok_response(reply("BrightPath Tech")),
                make_error("AuthenticationError", 401, f"Incorrect API key provided: {SETTINGS.api_key}")]
    result, backend, _ = run(FIVE, *outcomes)
    assert result.status == "Failed" and len(backend.calls) == 3
    assert result.message == "Stopped while evaluating NexaWorks: Invalid API key for OpenAI. Nothing was ranked. Work done so far is saved."
    assert SETTINGS.api_key not in result.message and SETTINGS.api_key not in whole_database()
    assert rows("SELECT status FROM rfp_runs") == [("Failed",)]
    assert rows("SELECT DISTINCT supplier_name FROM evaluation_attempts ORDER BY supplier_name") == [("Apex Systems",), ("BrightPath Tech",)]
    assert rows("SELECT COUNT(*) FROM supplier_results") == [(0,)]
    event = json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'run_failed'")[0][0])
    assert event["supplier"] == "NexaWorks" and event["kind"] == "invalid_key"


def test_a_timeout_after_its_retry_also_stops_the_run():
    result, backend, _ = run(["Apex Systems", "NexaWorks"], ok_response(reply("Apex Systems")), make_error("Timeout", None, "timed out"))
    assert result.status == "Failed" and "took too long" in result.message and len(backend.calls) == 3   # 1 + (1 try + 1 retry)


def test_stopping_the_script_marks_the_run_interrupted_and_lets_it_stop():
    backend = FakeBackend(ok_response(reply("Apex Systems")), KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        orchestrator.run_evaluation([proposal("Apex Systems"), proposal("NexaWorks")], TERMS, SETTINGS, "Sri", backend=backend,
                                    sleep=lambda _s: None, now=lambda: NOW, token=lambda: "ab12")
    assert rows("SELECT status FROM rfp_runs") == [("Interrupted",)]
    assert rows("SELECT action FROM audit_log ORDER BY id")[-1] == ("run_interrupted",)


def test_a_stop_in_the_progress_callback_is_handled_the_same_way():
    def stop_now(message: str) -> None:
        if message.startswith("Evaluating NexaWorks"):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        orchestrator.run_evaluation([proposal("Apex Systems"), proposal("NexaWorks")], TERMS, SETTINGS, progress=stop_now,
                                    backend=FakeBackend(ok_response(reply("Apex Systems"))), sleep=lambda _s: None,
                                    now=lambda: NOW, token=lambda: "ab12")
    assert rows("SELECT status FROM rfp_runs") == [("Interrupted",)]


def test_an_unexpected_bug_becomes_a_friendly_failure_not_a_stack_trace(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("secret detail about Apex Systems")

    monkeypatch.setattr(ranking, "rank_suppliers", boom)
    result, _, _ = run()
    assert result.status == "Failed" and "ValueError" in result.message and "Traceback" not in result.message
    assert "secret detail" not in result.message
    assert rows("SELECT status FROM rfp_runs") == [("Failed",)]
    event = json.loads(rows("SELECT details_json FROM audit_log WHERE action = 'run_failed'")[0][0])
    assert event["error_type"] == "ValueError" and event["where"].startswith("test_orchestrator.py:") and event["where"].endswith(" in boom")
    assert "secret detail" not in whole_database()   # the error's own text is not stored


def test_an_error_that_contains_the_key_never_reaches_the_database_or_the_screen(monkeypatch):
    def leaky(*args, **kwargs):
        raise RuntimeError(f"request failed for key {SETTINGS.api_key}")

    monkeypatch.setattr(ranking, "rank_suppliers", leaky)
    result, _, seen = run()
    assert result.status == "Failed" and "RuntimeError" in result.message
    assert SETTINGS.api_key not in whole_database() and SETTINGS.api_key not in "\n".join([*seen, result.message, repr(result)])


def test_a_database_that_cannot_be_opened_gives_a_friendly_failure(monkeypatch):
    import sqlite3

    real = repository.create_run

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repository, "create_run", locked)
    result, _, _ = run()
    monkeypatch.setattr(repository, "create_run", real)
    assert result.status == "Failed" and result.run_id == "" and "could not be started (OperationalError)" in result.message
    assert rows("SELECT COUNT(*) FROM rfp_runs") == [(0,)]


# ---------------------------------------------------------------- the 7 sample PDFs

@needs_pdf_reader
def test_the_seven_sample_pdfs_run_end_to_end_with_the_expected_gate_results():
    names = list(SAMPLES)
    proposals = [Proposal(file, name, when, rating, pdf_tool.extract_pdf_file(ROOT / "data" / "proposals" / file))
                 for name, (file, when, rating) in SAMPLES.items()]
    result, backend, _ = run(names, proposals=proposals)
    assert result.status == "Evaluated" and len(result.suppliers) == 7 and len(backend.calls) == 7
    by_name = {s.name: s for s in result.suppliers}
    assert not by_name["BrightPath Tech"].eligible and any("Warranty" in g for g in by_name["BrightPath Tech"].failed_gates)
    assert not by_name["Vertexa Solutions"].eligible and "Price table: No price table" in by_name["Vertexa Solutions"].failed_gates
    sent = re.sub(r"\s+", "", "\n".join(m["content"] for call in backend.calls for m in call["messages"])).lower()
    assert all(name.replace(" ", "").lower() not in sent for name in names)
    expected = ranking.rank_suppliers(
        [ranking.SupplierInput(n, SAMPLES[n][1], SAMPLES[n][2], dict(enumerate(SCORES[n], start=1))) for n in names],
        [ranking.CriterionWeight(i, w, 10) for i, w in zip(range(1, 6), (30, 20, 20, 20, 10))])
    assert [s.name for s in result.suppliers] == [r.name for r in expected.suppliers]
    documents = rows("SELECT file_name, pages, sha256 FROM run_documents ORDER BY id")
    assert len(documents) == 7 and all(len(d[2]) == 64 for d in documents)
    assert rows("SELECT COUNT(*) FROM evaluation_attempts") == [(35,)]
    assert "Response to RFP MA-RFP-2026-017" not in whole_database()


# ---------------------------------------------------------------- guardrails

def test_the_orchestrator_module_has_no_ui_provider_or_print_code():
    source = (ROOT / "agents" / "orchestrator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            top_level.add((node.module or "").split(".")[0])
    assert top_level <= {"__future__", "json", "os", "pathlib", "re", "secrets", "time", "traceback", "collections", "dataclasses", "datetime", "typing",
                         "agents", "db", "llm", "tools"}
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"print", "open", "eval", "exec", "input"}
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings]
    assert not any("proposals" in text for text in literals)                      # never reads the sample folder at run time
    assert "api_key" not in {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}   # never touches the key itself
