"""The price score: the Commercial Value mark is worked out in Python from the proposals' grand totals (lowest total = full marks),
not taken from the AI. Pure rules first, then what a real run does with them. No network, no model."""
from __future__ import annotations

import json

import pytest

from db import repository, seed
from review_support import APEX, FIVE, GOOD_FACTS, HORIZON, NEXA, evaluated_run, marks, rows, saved
from tools import insights
from tools.insights import is_price_criterion, price_marks, price_reason


# ---------------------------------------------------------------- the rule

def test_the_lowest_total_gets_the_full_mark_and_the_others_a_share_of_it():
    got = price_marks({"A": 5616800, "B": 5947200}, 10)
    assert got is not None
    assert got.marks == {"A": 10.0, "B": 9.44}                 # 10 x 5616800 / 5947200 = 9.4445 -> 9.44
    assert got.reference_rs == 5616800 and got.without_total == ()


def test_the_mark_scales_with_the_criterion_maximum():
    got = price_marks({"A": 100, "B": 200}, 20)
    assert got is not None and got.marks == {"A": 20.0, "B": 10.0}


def test_half_a_hundredth_rounds_up():
    got = price_marks({"A": 1000, "B": 1600}, 10)            # 6.25 exactly
    assert got is not None and got.marks["B"] == 6.25
    got = price_marks({"A": 8, "B": 9}, 10)                  # 8.8888 -> 8.89
    assert got is not None and got.marks["B"] == 8.89
    got = price_marks({"A": 2000, "B": 3200}, 1)             # 0.625 -> 0.63 (half up)
    assert got is not None and got.marks["B"] == 0.63


def test_equal_totals_get_equal_full_marks():
    got = price_marks({"A": 5900000, "B": 5900000, "C": 5900000}, 10)
    assert got is not None and set(got.marks.values()) == {10.0}


def test_a_supplier_without_a_total_keeps_the_ai_mark_and_is_named():
    got = price_marks({"A": 100, "B": 125, "C": None, "D": 0, "E": "n/a", "F": True}, 10)
    assert got is not None and set(got.marks) == {"A", "B"}
    assert got.without_total == ("C", "D", "E", "F")


@pytest.mark.parametrize("totals", [{}, {"A": 100}, {"A": 100, "B": None}, {"A": 0, "B": -5}, {"A": float("nan"), "B": 100}])
def test_fewer_than_two_usable_totals_means_no_change(totals):
    assert price_marks(totals, 10) is None


@pytest.mark.parametrize("bad_max", [0, -1, None, float("nan"), True])
def test_a_maximum_that_is_not_a_positive_number_means_no_change(bad_max):
    assert price_marks({"A": 100, "B": 200}, bad_max) is None   # type: ignore[arg-type]


def test_a_cheaper_offer_never_gets_a_lower_mark_than_a_dearer_one():
    totals = {f"S{i}": 5_000_000 + i * 123_457 for i in range(12)}
    got = price_marks(totals, 10)
    assert got is not None
    ordered = [got.marks[n] for n in sorted(totals, key=totals.get)]            # type: ignore[arg-type]
    assert ordered == sorted(ordered, reverse=True) and ordered[0] == 10.0 and all(0 < m <= 10 for m in ordered)


def test_only_the_criterion_named_commercial_value_stands_for_price():
    assert is_price_criterion("Commercial Value") and is_price_criterion("  commercial VALUE ")
    assert not is_price_criterion("Support") and not is_price_criterion(None) and not is_price_criterion(3)


def test_the_reason_shows_the_arithmetic_and_names_no_supplier():
    low = price_reason(5616800, 5616800, 10.0, 10)
    high = price_reason(5947200, 5616800, 9.44, 10)
    assert "full 10 marks" in low and "Rs 56.17 lakh" in low
    assert "10 x 56.17 / 59.47 = 9.44" in high and "not marked by the AI" in high


# ---------------------------------------------------------------- what a run does

@pytest.fixture(autouse=True)
def seeded_database():
    seed.ensure_database()


def three(totals: dict[str, object]):
    rows_ = [r for r in FIVE if r[0] in (APEX, NEXA, HORIZON)]
    return evaluated_run(rows_, facts={n: {**GOOD_FACTS, "grand_total_rs": t} for n, t in totals.items()})


def commercial(stored, name: str) -> dict:
    return next(c for c in stored[name].result["criteria"] if c["name"] == "Commercial Value")


def test_a_run_replaces_the_commercial_mark_by_the_one_worked_out_from_the_totals():
    result = three({APEX: 5900000, NEXA: 5000000, HORIZON: 6250000})
    stored = saved(result.run_id)
    assert [commercial(stored, n)["score"] for n in (APEX, NEXA, HORIZON)] == [8.47, 10.0, 8.0]
    assert "10 x 50 / 62.5 = 8" in commercial(stored, HORIZON)["justification"] or "= 8" in commercial(stored, HORIZON)["justification"]
    assert commercial(stored, HORIZON)["justification"].startswith("Price mark worked out from the totals, not marked by the AI")
    assert commercial(stored, NEXA)["evidence"] == "" and commercial(stored, NEXA)["evidence_found"] is None


def test_the_other_marks_are_left_as_the_ai_gave_them():
    result = three({APEX: 5900000, NEXA: 5000000, HORIZON: 6250000})
    stored = saved(result.run_id)
    first = [c["score"] for c in stored[APEX].result["criteria"] if c["name"] != "Commercial Value"]
    assert first == [9, 7, 9, 8]


def test_the_ai_marks_stay_in_the_attempts_table():
    result = three({APEX: 5900000, NEXA: 5000000, HORIZON: 6250000})
    with repository.connection() as conn:
        attempts = repository.list_attempts(conn, result.run_id)
    apex = {a.criterion_id: a for a in attempts if a.supplier_name == APEX}
    assert apex[3].score == 5 and apex[3].justification == "why 3"


def test_a_cheaper_offer_can_now_win_where_the_ai_marks_alone_would_not():
    dear = three({APEX: 5900000, NEXA: 5900000, HORIZON: 5900000})
    assert next(s for s in dear.suppliers if s.rank == 1).name == APEX
    cheap = three({APEX: 9000000, NEXA: 5000000, HORIZON: 9000000})
    assert next(s for s in cheap.suppliers if s.rank == 1).name == NEXA          # ranking uses the worked-out marks


def test_the_audit_log_records_the_marks_the_totals_and_the_ai_marks():
    result = three({APEX: 5900000, NEXA: 5000000, HORIZON: 6250000})
    (event,) = [r for r in rows("SELECT details_json FROM audit_log WHERE rfp_run_id = ? AND action = 'price_marks_set'", result.run_id)]
    details = json.loads(event["details_json"])
    assert details["lowest_total_rs"] == 5000000 and details["marks"][NEXA] == 10.0
    assert details["ai_marks"] == {APEX: 5, NEXA: 7, HORIZON: 7} and details["without_total"] == []
    assert any("Price marks worked out" in s for s in result.steps)


def test_a_supplier_with_no_total_keeps_the_ai_mark_and_is_named_in_the_log():
    result = three({APEX: 5900000, NEXA: 5000000, HORIZON: None})
    stored = saved(result.run_id)
    assert commercial(stored, HORIZON)["score"] == 7 and commercial(stored, HORIZON)["justification"] == "why 3"
    assert commercial(stored, NEXA)["score"] == 10.0
    details = json.loads(rows("SELECT details_json FROM audit_log WHERE rfp_run_id = ? AND action = 'price_marks_set'", result.run_id)[0]["details_json"])
    assert details["without_total"] == [HORIZON]


def test_with_fewer_than_two_totals_nothing_changes_and_the_step_says_so():
    result = three({APEX: 5900000, NEXA: None, HORIZON: None})
    stored = saved(result.run_id)
    assert [commercial(stored, n)["score"] for n in (APEX, NEXA, HORIZON)] == [5, 7, 7]
    assert not rows("SELECT 1 FROM audit_log WHERE rfp_run_id = ? AND action = 'price_marks_set'", result.run_id)
    assert any("fewer than two suppliers state a total" in s for s in result.steps)


def test_marks_and_the_peer_score_agree_with_the_saved_marks():
    result = three({APEX: 5900000, NEXA: 5000000, HORIZON: 6250000})
    stored = saved(result.run_id)
    assert marks(stored, NEXA)[2] == 10.0 and stored[NEXA].absolute_score is not None
    assert insights.is_price_criterion("Commercial Value")


# ---------------------------------------------------------------- the helper on its own

def _outcome(name: str, score: float, *, failed: bool = False):
    from agents import evaluator
    from tools import validator
    card = validator.EvaluationScorecard(
        criteria=(validator.ScoredCriterion(criterion_id=1, score=score, justification="ai words", evidence="a quote"),),
        extracted_facts=validator.ExtractedFacts())
    return evaluator.EvaluationOutcome(name, card, (), failed, 1, None, None, 0)


def _criteria():
    from agents import orchestrator
    return [orchestrator.RunCriterion(1, "Commercial Value", "", 100.0, 10.0)]


def test_a_failed_evaluation_is_left_out_even_if_it_carries_a_total():
    from agents import orchestrator
    outcomes = {"A": _outcome("A", 4), "B": _outcome("B", 4), "C": _outcome("C", 0, failed=True)}
    facts = {"A": {"grand_total_rs": 200}, "B": {"grand_total_rs": 100}, "C": {"grand_total_rs": 1}}
    changed, worked, _c = orchestrator.apply_price_marks(outcomes, _criteria(), facts)
    assert worked is not None and set(worked.marks) == {"A", "B"} and worked.reference_rs == 100
    assert changed["C"] is outcomes["C"] and changed["B"].scorecard.criteria[0].score == 10.0


def test_the_replaced_mark_is_not_flagged_as_filled_in_by_the_validator_and_has_no_quote():
    from agents import orchestrator
    outcomes = {"A": _outcome("A", 4), "B": _outcome("B", 4)}
    changed, _w, _c = orchestrator.apply_price_marks(outcomes, _criteria(), {"A": {"grand_total_rs": 200}, "B": {"grand_total_rs": 100}})
    mark = changed["A"].scorecard.criteria[0]
    assert (mark.score, mark.evidence, mark.filled_by_validator) == (5.0, "", False)
    assert outcomes["A"].scorecard.criteria[0].score == 4                  # the AI's own outcome is not changed in place


def test_without_a_commercial_value_criterion_nothing_is_changed():
    from agents import orchestrator
    outcomes = {"A": _outcome("A", 4), "B": _outcome("B", 4)}
    other = [orchestrator.RunCriterion(1, "Support", "", 100.0, 10.0)]
    changed, worked, used = orchestrator.apply_price_marks(outcomes, other, {"A": {"grand_total_rs": 200}, "B": {"grand_total_rs": 100}})
    assert changed is outcomes and worked is None and used is None
