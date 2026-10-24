"""F15: what a run does with the evidence check - saved flags, audit rows, the Scorecards cell, and the promise that a quote that is
not found never changes a mark or the ranking. No network, no model."""
from __future__ import annotations

import json

import pytest

from agents import orchestrator
from db import repository, seed
from review_support import (APEX, BRIGHT, FIVE, GOOD_FACTS, QUOTE, SETTINGS, START, TERMS, FakeBackend, evaluated_run, pdf_for, reply,
                            rows, saved)
from tools import review
from ui import tab_scorecards as T

@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


INVENTED = "Apex gives a ten year warranty and a free laptop for every user. (Page 1)"
NOT_STATED = "Not stated in the proposal"


def run_with(evidence_by_supplier: dict[str, list[str]]):
    proposals = [orchestrator.Proposal(f"{n.lower().replace(' ', '_')}.pdf", n, when, rating, pdf_for(n)) for n, when, rating, _s in FIVE]
    replies = []
    for n, _w, _r, scores in FIVE:
        quotes = evidence_by_supplier.get(n, [QUOTE] * 5)
        body = json.loads(reply(scores, GOOD_FACTS))
        for item, quote in zip(body["criteria"], quotes):
            item["evidence"] = quote
        replies.append(json.dumps(body))
    backend = FakeBackend(*replies)
    result = orchestrator.run_evaluation(proposals, TERMS, SETTINGS, "Sri", backend=backend, sleep=lambda _s: None, now=lambda: START)
    assert result.status == "Evaluated", result.message
    return result


def flags(run_id: str, name: str) -> list:
    return [c["evidence_found"] for c in saved(run_id)[name].result["criteria"]]


def test_a_real_quote_is_saved_as_found_and_an_invented_one_as_not_found():
    result = run_with({APEX: [QUOTE, INVENTED, NOT_STATED, QUOTE, "  "]})
    assert flags(result.run_id, APEX) == [True, False, None, True, None]
    assert flags(result.run_id, BRIGHT) == [True] * 5


def test_the_attempt_rows_carry_the_same_flags():
    result = run_with({APEX: [QUOTE, INVENTED, NOT_STATED, QUOTE, QUOTE]})
    found = rows("SELECT criterion_id, evidence_found FROM evaluation_attempts WHERE rfp_run_id = ? AND supplier_name = ? ORDER BY criterion_id",
                 result.run_id, APEX)
    assert [r["evidence_found"] for r in found] == [1, 0, None, 1, 1]


def test_a_quote_that_is_not_found_changes_no_mark_and_no_rank():
    honest, doubtful = evaluated_run(), run_with({APEX: [INVENTED] * 5})
    a, b = saved(honest.run_id), saved(doubtful.run_id)
    assert {n: (s.final_rank, s.absolute_score, s.ppi) for n, s in a.items()} == {n: (s.final_rank, s.absolute_score, s.ppi) for n, s in b.items()}
    assert [c["score"] for c in a[APEX].result["criteria"]] == [c["score"] for c in b[APEX].result["criteria"]]


def test_each_supplier_with_missing_quotes_gets_one_audit_row_that_names_the_criteria():
    result = run_with({APEX: [INVENTED, QUOTE, INVENTED, QUOTE, QUOTE], BRIGHT: [NOT_STATED] * 5})
    events = [r for r in rows("SELECT action, details_json FROM audit_log WHERE rfp_run_id = ?", result.run_id)
              if r["action"] == "evidence_not_found"]
    assert len(events) == 1
    details = json.loads(events[0]["details_json"])
    assert details == {"supplier": APEX, "criterion_ids": [1, 3], "checked": 5}


def test_a_run_with_every_quote_found_has_no_evidence_audit_row():
    result = evaluated_run()
    assert not [r for r in rows("SELECT action FROM audit_log WHERE rfp_run_id = ?", result.run_id) if r["action"] == "evidence_not_found"]


def test_the_step_log_tells_how_many_quotes_were_not_found():
    result = run_with({APEX: [INVENTED, INVENTED, QUOTE, QUOTE, QUOTE]})
    assert any("2 quote(s) not found" in line and APEX in line for line in result.steps)


def test_the_re_evaluation_to_do_list_gets_one_item_per_missing_quote():
    result = run_with({APEX: [INVENTED, QUOTE, QUOTE, INVENTED, QUOTE]})
    todo = [i for i in orchestrator_todo(result.run_id) if i.kind == review.KIND_EVIDENCE]
    assert [(i.supplier, i.criterion_id) for i in todo] == [(APEX, 1), (APEX, 4)]


def orchestrator_todo(run_id: str):
    with repository.connection() as conn:
        results = repository.list_supplier_results(conn, run_id)
    suppliers = [review.TodoSupplier(s.supplier_name, s.final_rank, (), False,
                                     tuple(review.TodoCriterion(c["criterion_id"], c["name"], c.get("evidence_found")) for c in s.result["criteria"]))
                 for s in results]
    return review.build_todo(suppliers)


# ---------------------------------------------------------------- the Scorecards cell

def line(**kw):
    base = dict(criterion_id=1, name="Price", score=5, max_score=10, benchmark=6, gap=-1, relative_pct=83.3, weight=20,
                evidence="We offer laptops. (Page 1)", justification="why", flag=None)
    return T.Line(**{**base, **kw})


def test_a_found_quote_shows_a_tick_and_a_missing_one_a_cross():
    html = T.criteria_html((line(found=True), line(criterion_id=2, found=False)))
    assert html.count(T.FOUND_TEXT) == 1 and html.count(T.NOT_FOUND_TEXT) == 1
    assert html.index(T.FOUND_TEXT) < html.index("We offer laptops.")


def test_nothing_is_added_when_the_quote_was_not_checked():
    html = T.criteria_html((line(found=None, evidence="Not stated in the proposal"),))
    assert T.FOUND_TEXT not in html and T.NOT_FOUND_TEXT not in html


def test_the_line_reads_the_saved_flag_and_ignores_anything_else():
    item = {"criterion_id": 1, "name": "P", "score": 1, "max_score": 10, "evidence": "x", "justification": "y"}
    assert T._line({**item, "evidence_found": True}).found is True
    assert T._line({**item, "evidence_found": False}).found is False
    assert T._line({**item, "evidence_found": None}).found is None
    assert T._line({**item, "evidence_found": "yes"}).found is None and T._line(item).found is None


def test_a_quote_with_html_in_it_is_escaped_next_to_the_cross():
    html = T.criteria_html((line(found=False, evidence="<script>alert(1)</script> (Page 1)"),))
    assert "<script>" not in html and "&lt;script&gt;" in html
